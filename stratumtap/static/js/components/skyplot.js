// Polar sky plot of the satellites currently in view.
//
// Projection: azimuth is compass bearing (0° = north, drawn straight up;
// 90° = east, drawn to the right), elevation is 0° at the horizon ring and 90°
// at the center. We use the standard *stereographic-free* linear mapping that
// cgps and every other sky plot uses — radius is proportional to the zenith
// angle:
//
//     r = R * (90 − elevation) / 90
//     x = cx + r * sin(az),   y = cy − r * cos(az)
//
// (sin/cos are swapped relative to the usual maths convention because bearings
// run clockwise from north, not anticlockwise from east.)
//
// Marker radius encodes SNR; fill vs. outline encodes "used in the fix", and
// every marker carries its satellite number as a direct label so identity never
// rests on color alone.

import { el, clear } from './tiles.js';
import { DASH, isNum } from '../format.js';

const NS = 'http://www.w3.org/2000/svg';
const SIZE = 440;
const CX = SIZE / 2, CY = SIZE / 2;
const R = SIZE / 2 - 26;

/** GNSS → CSS color variable + display name. Gray is the "unknown" slot. */
export const CONSTELLATIONS = {
  GP: { name: 'GPS', color: 'var(--series-1)' },      // blue
  GL: { name: 'GLONASS', color: 'var(--series-8)' },  // red
  GA: { name: 'Galileo', color: 'var(--series-6)' },  // green
  BD: { name: 'BeiDou', color: 'var(--series-4)' },   // amber
  SB: { name: 'SBAS', color: 'var(--series-7)' },     // violet
  QZ: { name: 'QZSS', color: 'var(--series-3)' },     // aqua
  IR: { name: 'NavIC', color: 'var(--series-5)' },    // magenta
  IM: { name: 'IMES', color: 'var(--series-gray)' },
  '??': { name: 'Unknown', color: 'var(--series-gray)' },
};

export function constColor(gnss) {
  return (CONSTELLATIONS[gnss] || CONSTELLATIONS['??']).color;
}

export function constName(gnss, fallback) {
  return fallback || (CONSTELLATIONS[gnss] || CONSTELLATIONS['??']).name;
}

/** The number to show for a satellite: svid first, gpsd's prn as a fallback. */
export function satNumber(sat) {
  if (!sat) return null;
  return isNum(sat.svid) ? sat.svid : (isNum(sat.prn) ? sat.prn : null);
}

function svg(tag, attrs, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v !== null && v !== undefined) n.setAttribute(k, String(v));
  }
  if (text != null) n.textContent = String(text);
  return n;
}

/** az/el (degrees) → x/y in the SVG box. */
export function project(azDeg, elDeg) {
  const az = (azDeg * Math.PI) / 180;
  const zenithFraction = Math.max(0, Math.min(1, (90 - elDeg) / 90));
  const r = R * zenithFraction;
  return [CX + r * Math.sin(az), CY - r * Math.cos(az)];
}

/** SNR (dB-Hz) → marker radius. Clamped so a 0 dB satellite is still clickable. */
function snrRadius(snr) {
  if (!isNum(snr) || snr <= 0) return 4;
  return 4 + Math.min(1, snr / 50) * 6;   // 4..10 px in a 440 px box
}

export class SkyPlot {
  constructor(root) {
    this.root = root;
    this.root.classList.add('skyplot');
    this.build();
  }

  build() {
    clear(this.root);
    const s = svg('svg', {
      viewBox: `0 0 ${SIZE} ${SIZE}`, role: 'img',
      'aria-label': 'Sky plot of satellites in view',
    });

    s.append(svg('circle', { cx: CX, cy: CY, r: R, class: 'sky-disc' }));
    // horizon + 30°/60° elevation rings
    for (const elev of [0, 30, 60]) {
      s.append(svg('circle', {
        cx: CX, cy: CY, r: (R * (90 - elev)) / 90, class: 'sky-ring',
        'stroke-width': elev === 0 ? 1.5 : 1,
      }));
    }
    // azimuth spokes every 30°
    for (let az = 0; az < 360; az += 30) {
      const [x, y] = project(az, 0);
      s.append(svg('line', { x1: CX, y1: CY, x2: x, y2: y, class: 'sky-spoke' }));
    }
    // cardinal labels + elevation ring labels
    for (const [az, txt] of [[0, 'N'], [90, 'E'], [180, 'S'], [270, 'W']]) {
      const [x, y] = project(az, -8);
      s.append(svg('text', {
        x, y: y + 4, class: 'sky-card', 'text-anchor': 'middle',
      }, txt));
    }
    for (const elev of [30, 60]) {
      s.append(svg('text', {
        x: CX + 3, y: CY - (R * (90 - elev)) / 90 - 3, class: 'sky-label',
      }, `${elev}°`));
    }

    this.satLayer = svg('g', { 'aria-hidden': 'false' });
    s.append(this.satLayer);
    this.svgEl = s;

    this.legendEl = el('div.legend');
    this.countEl = el('div.muted', { style: { fontSize: '12px' } });
    this.root.append(s, this.legendEl, this.countEl);
  }

  /** @param {object|null} satellites the gps.satellites object */
  update(satellites) {
    const list = (satellites && Array.isArray(satellites.list)) ? satellites.list : [];
    clear(this.satLayer);
    const present = new Map();

    for (const sat of list) {
      if (!isNum(sat.el_deg) || !isNum(sat.az_deg)) continue;
      const color = constColor(sat.gnss);
      present.set(sat.gnss, constName(sat.gnss, sat.gnss_name));
      const [x, y] = project(sat.az_deg, sat.el_deg);
      const r = snrRadius(sat.snr_db);
      const num = satNumber(sat);

      const c = svg('circle', {
        cx: x.toFixed(1), cy: y.toFixed(1), r: r.toFixed(1),
        class: 'sky-sat',
        fill: sat.used ? color : 'var(--surface)',
        stroke: color,
        'stroke-dasharray': sat.used ? null : '2 2',
      });
      const bits = [
        `${constName(sat.gnss, sat.gnss_name)} ${num ?? DASH}`,
        `el ${sat.el_deg}° az ${sat.az_deg}°`,
        isNum(sat.snr_db) ? `SNR ${sat.snr_db} dB` : 'SNR —',
        sat.used ? 'used in fix' : 'not used',
      ];
      if (isNum(sat.health)) bits.push(sat.health === 1 ? 'healthy' : `health ${sat.health}`);
      c.append(svg('title', {}, bits.join(' · ')));
      this.satLayer.append(c);

      if (num != null) {
        this.satLayer.append(svg('text', {
          x: (x + r + 2).toFixed(1), y: (y + 3).toFixed(1), class: 'sky-prn',
        }, num));
      }
    }

    // legend: only constellations actually on screen, plus the used/unused key
    clear(this.legendEl);
    for (const [code, name] of [...present.entries()].sort()) {
      this.legendEl.append(el('span.item',
        el('span.sw', { style: { background: constColor(code) } }),
        `${code} ${name}`));
    }
    if (present.size) {
      this.legendEl.append(el('span.item',
        el('span.sw', { style: { background: 'var(--ink-2)' } }), 'used'));
      this.legendEl.append(el('span.item',
        el('span.sw', { style: { background: 'var(--surface)', boxShadow: 'inset 0 0 0 1px var(--ink-2)' } }),
        'not used'));
    }

    const seen = satellites && isNum(satellites.seen) ? satellites.seen : list.length;
    const used = satellites && isNum(satellites.used)
      ? satellites.used : list.filter((s) => s.used).length;
    this.countEl.textContent = list.length
      ? `${used} used of ${seen} seen · marker size = SNR`
      : 'No satellites reported';
  }

  destroy() { clear(this.root); }
}

export default SkyPlot;
