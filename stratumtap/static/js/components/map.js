// Leaflet wrapper. Leaflet is loaded as a classic script in index.html and
// exposes the global `L`; everything here degrades gracefully if it is absent
// or if tiles cannot be fetched (isolated network) — the marker, the accuracy
// circles and the track still render on a blank background.

import { el, clear } from './tiles.js';
import { distance, latLonDec, DASH, isNum } from '../format.js';

const DEFAULT_ZOOM = 16;
const ELLIPSE_POINTS = 36;
const M_PER_DEG_LAT = 111320;   // good to ~0.5% anywhere; the ellipse is meters-scale

/**
 * 1σ GST error ellipse as lat/lon vertices.
 *
 * `orient_deg` is the bearing of the MAJOR axis, measured clockwise from true
 * north. Parametrising the ellipse in its own frame as
 * (major·cos t) along the major axis and (minor·sin t) across it, the rotation
 * into a north/east frame is
 *
 *     north = major·cos t·cos θ − minor·sin t·sin θ
 *     east  = major·cos t·sin θ + minor·sin t·cos θ
 *
 * (a clockwise-from-north rotation, hence sin/cos swapped versus the usual
 * anticlockwise-from-east convention). Meters are then converted locally:
 * one degree of latitude is ~111 320 m, and one degree of longitude is that
 * shrunk by cos(latitude).
 */
export function ellipsePoints(lat, lon, majorM, minorM, orientDeg, n = ELLIPSE_POINTS) {
  const theta = ((orientDeg || 0) * Math.PI) / 180;
  const cosT = Math.cos(theta), sinT = Math.sin(theta);
  const mPerDegLon = M_PER_DEG_LAT * Math.max(0.01, Math.cos((lat * Math.PI) / 180));
  const pts = [];
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    const a = majorM * Math.cos(t);
    const b = minorM * Math.sin(t);
    const north = a * cosT - b * sinT;
    const east = a * sinT + b * cosT;
    pts.push([lat + north / M_PER_DEG_LAT, lon + east / mPerDegLon]);
  }
  return pts;
}

export class MapPanel {
  /** @param {object} ctx { store, config } */
  constructor(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    this.map = null;
    this.marker = null;
    this.ephCircle = null;
    this.sepCircle = null;
    this.track = null;
    this.gstEllipse = null;
    this.tileErrorShown = false;
    this.centered = false;
    this.build();
  }

  build() {
    clear(this.root);
    this.holder = el('div.leaflet-holder', { id: 'map' });
    this.notice = el('div.map-notice', { role: 'status', style: { display: 'none' } });
    this.wrap = el('div.map-holder', this.holder, this.notice);

    this.followBtn = el('button.btn.btn-sm', {
      type: 'button',
      'aria-pressed': String(!!this.ctx.store.settings.mapFollow),
      onclick: () => this.toggleFollow(),
    }, 'Follow position');
    this.centerBtn = el('button.btn.btn-sm', {
      type: 'button',
      onclick: () => this.centerNow(),
    }, 'Center now');
    this.readout = el('span.muted.mono', { style: { fontSize: '12px' } });
    this.legendEl = el('div.legend', { style: { marginTop: '6px' } });
    this.devicesEl = el('div.muted', { style: { fontSize: '11.5px', marginTop: '4px' } });

    this.root.append(
      this.wrap,
      el('div.rowline', { style: { marginTop: '8px' } },
        this.followBtn, this.centerBtn, el('span.grow'), this.readout),
      this.legendEl,
      this.devicesEl,
    );

    if (typeof window.L === 'undefined') {
      this.#notice('Map library not loaded — position shown numerically only.');
      return;
    }
    this.#initMap();
  }

  #initMap() {
    const L = window.L;
    const cfg = this.ctx.config || {};
    this.map = L.map(this.holder, {
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
    }).setView([0, 0], 2);

    if (cfg.tile_url) {
      this.layer = L.tileLayer(cfg.tile_url, {
        attribution: cfg.tile_attribution || '',
        maxZoom: 19,
        crossOrigin: true,
      });
      // A single failed tile is enough to know we are offline; say so once and
      // keep the rest of the map working.
      this.layer.on('tileerror', () => {
        if (this.tileErrorShown) return;
        this.tileErrorShown = true;
        this.#notice('Map tiles unavailable (offline?) — position and accuracy still shown.');
      });
      this.layer.addTo(this.map);
    } else {
      this.#notice('No tile URL configured — position shown on a blank background.');
    }

    L.control.scale({ imperial: this.ctx.store.settings.units === 'imperial', metric: true }).addTo(this.map);

    // A user drag means "stop following"; otherwise the map fights the user.
    this.map.on('dragstart', () => {
      if (this.ctx.store.settings.mapFollow) this.#setFollow(false);
    });
  }

  #notice(text) {
    this.notice.textContent = text;
    this.notice.style.display = '';
  }

  toggleFollow() { this.#setFollow(!this.ctx.store.settings.mapFollow); }

  #setFollow(on) {
    this.ctx.store.set({ mapFollow: on });
    this.followBtn.setAttribute('aria-pressed', String(on));
    if (on) this.centerNow();
  }

  centerNow() {
    if (this.map && this.lastPos) this.map.setView(this.lastPos, Math.max(this.map.getZoom(), DEFAULT_ZOOM));
  }

  /**
   * @param {object|null} gps  the gps domain object
   * @param {[number,number][]} trackPoints lat/lon pairs for the track polyline
   */
  update(gps, trackPoints = []) {
    const pos = gps && gps.position;
    const lat = pos && pos.lat, lon = pos && pos.lon;
    const units = this.ctx.store.settings.units;

    this.#renderDevices(gps);
    if (!isNum(lat) || !isNum(lon)) {
      this.readout.textContent = 'No position fix';
      this.#renderLegend(gps);
      return;
    }
    this.lastPos = [lat, lon];
    const eph = gps.accuracy && gps.accuracy.eph_m;
    const sep = gps.accuracy && gps.accuracy.sep_m;
    this.readout.textContent =
      `${latLonDec(lat, 'lat')}  ${latLonDec(lon, 'lon')}` +
      (isNum(eph) ? `  ±${distance(eph, units)}` : '');

    if (!this.map) return;
    const L = window.L;

    if (!this.marker) {
      this.marker = L.circleMarker([lat, lon], {
        radius: 6, weight: 2,
        color: getComputedStyle(document.documentElement).getPropertyValue('--series-1').trim() || '#2a78d6',
        fillColor: getComputedStyle(document.documentElement).getPropertyValue('--series-1').trim() || '#2a78d6',
        fillOpacity: 0.9,
      }).addTo(this.map);
    } else {
      this.marker.setLatLng([lat, lon]);
    }
    this.marker.bindTooltip(
      `${latLonDec(lat, 'lat')} ${latLonDec(lon, 'lon')}`, { direction: 'top' });

    // 2D CEP circle (solid) and 3D SEP circle (dashed).
    if (isNum(eph) && eph > 0) {
      const opts = { radius: eph, weight: 1.5, color: '#2a78d6', fillOpacity: 0.08, interactive: true };
      if (!this.ephCircle) this.ephCircle = L.circle([lat, lon], opts).addTo(this.map);
      else { this.ephCircle.setLatLng([lat, lon]); this.ephCircle.setRadius(eph); }
      this.ephCircle.bindTooltip(`2D CEP ±${distance(eph, units)}`);
    } else if (this.ephCircle) {
      this.ephCircle.remove(); this.ephCircle = null;
    }

    if (isNum(sep) && sep > 0) {
      const opts = { radius: sep, weight: 1, color: '#898781', dashArray: '4 4', fill: false };
      if (!this.sepCircle) this.sepCircle = L.circle([lat, lon], opts).addTo(this.map);
      else { this.sepCircle.setLatLng([lat, lon]); this.sepCircle.setRadius(sep); }
      this.sepCircle.bindTooltip(`3D SEP ±${distance(sep, units)}`);
    } else if (this.sepCircle) {
      this.sepCircle.remove(); this.sepCircle = null;
    }

    // 1σ GST error ellipse, when the receiver sends NMEA GST.
    const gst = gps.gst;
    if (gst && isNum(gst.major_m) && isNum(gst.minor_m) && gst.major_m > 0) {
      const ring = ellipsePoints(lat, lon, gst.major_m, gst.minor_m, gst.orient_deg || 0);
      if (!this.gstEllipse) {
        this.gstEllipse = L.polygon(ring, {
          color: '#4a3aa7', weight: 1.5, fillOpacity: 0.10, dashArray: null,
        }).addTo(this.map);
      } else {
        this.gstEllipse.setLatLngs(ring);
      }
      this.gstEllipse.bindTooltip(
        `1σ GST ellipse ${distance(gst.major_m, units)} × ${distance(gst.minor_m, units)}`
        + (isNum(gst.orient_deg) ? ` at ${gst.orient_deg.toFixed(0)}°` : ''));
    } else if (this.gstEllipse) {
      this.gstEllipse.remove(); this.gstEllipse = null;
    }
    this.#renderLegend(gps);

    if (trackPoints && trackPoints.length > 1) {
      if (!this.track) {
        this.track = L.polyline(trackPoints, { color: '#eb6834', weight: 2, opacity: 0.85 }).addTo(this.map);
      } else {
        this.track.setLatLngs(trackPoints);
      }
    } else if (this.track) {
      this.track.remove(); this.track = null;
    }

    if (!this.centered) {
      // First fix: frame the accuracy circle rather than jumping to a fixed
      // zoom, so a 4 m CEP is visible instead of being one pixel. The card may
      // still be mid-layout, and fitBounds on a zero-size container resolves to
      // "the whole world" — so measure first, then sanity-check the result.
      this.map.invalidateSize({ animate: false });
      this.map.setView([lat, lon], DEFAULT_ZOOM, { animate: false });
      if (isNum(eph) && eph > 0 && this.ephCircle && this.holder.clientWidth > 50) {
        this.map.fitBounds(this.ephCircle.getBounds().pad(2), { animate: false, maxZoom: 19 });
        if (this.map.getZoom() < DEFAULT_ZOOM) this.map.setView([lat, lon], DEFAULT_ZOOM, { animate: false });
      }
      this.centered = true;
    } else if (this.ctx.store.settings.mapFollow) {
      this.map.panTo([lat, lon], { animate: false });
    }
  }

  /** Map legend: only the overlays actually drawn are listed. */
  #renderLegend(gps) {
    const a = (gps && gps.accuracy) || {};
    const items = [];
    items.push(['var(--series-1)', 'position', 'solid']);
    if (isNum(a.eph_m) && a.eph_m > 0) items.push(['var(--series-1)', '2D CEP (EPH)', 'fill']);
    if (isNum(a.sep_m) && a.sep_m > 0) items.push(['var(--series-gray)', '3D SEP', 'dash']);
    if (gps && gps.gst && isNum(gps.gst.major_m)) items.push(['var(--series-7)', '1σ GST ellipse', 'fill']);
    if (this.track) items.push(['var(--series-2)', 'recorded track', 'solid']);

    clear(this.legendEl);
    for (const [color, label, kind] of items) {
      this.legendEl.append(el('span.item',
        el('span.sw', {
          style: kind === 'dash'
            ? { background: 'transparent', boxShadow: `inset 0 0 0 1px ${color}` }
            : { background: color },
        }),
        label));
    }
  }

  /**
   * One line per device gpsd reports (path · driver · subtype · bps).
   * `gps.device` stays the primary; this is the full list.
   */
  #renderDevices(gps) {
    clear(this.devicesEl);
    const list = gps && Array.isArray(gps.devices) && gps.devices.length
      ? gps.devices
      : (gps && gps.device ? [gps.device] : []);
    if (!list.length) return;
    this.devicesEl.append(el('span', { style: { fontWeight: '600' }, text: 'Receiver: ' }));
    const rows = [];
    for (const d of list) {
      if (!d) continue;
      const bits = [d.path, d.driver, d.subtype].filter(Boolean);
      if (isNum(d.bps)) bits.push(`${d.bps} bps`);
      if (isNum(d.cycle_s)) bits.push(`${d.cycle_s} s cycle`);
      rows.push(bits.join(' · '));
    }
    this.devicesEl.append(el('span.mono', { text: rows.join('  |  ') }));
  }

  /** Leaflet needs a nudge when its container was hidden or resized. */
  invalidate() {
    if (this.map) this.map.invalidateSize({ animate: false });
  }

  destroy() {
    if (this.map) { this.map.remove(); this.map = null; }
    this.marker = this.ephCircle = this.sepCircle = this.track = this.gstEllipse = null;
    clear(this.root);
  }
}

export default MapPanel;
