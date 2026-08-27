// Hand-rolled canvas time-series panels. No chart library.
//
// One HistoryChart per measure; a HistoryPanels container owns the shared data
// (server history merged with live poll samples), the range selector and a
// single ResizeObserver. Nulls are gaps, never zeros: a break in the series is
// drawn as a break.

import { el, clear, cssVar } from './tiles.js';
import { axisTime, siSeconds, siSecondsUnit, num, isNum, DASH } from '../format.js';

const PAD = { top: 8, right: 8, bottom: 18, left: 46 };

/** "nice" step for an axis: 1, 2, 2.5 or 5 times a power of ten. */
function niceStep(range, targetTicks) {
  if (!(range > 0)) return 1;
  const raw = range / targetTicks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return step * mag;
}

/** Time-axis step from a seconds span: seconds → minutes → hours. */
function niceTimeStep(spanS, targetTicks) {
  const CANDIDATES = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
    3600, 7200, 10800, 21600, 43200, 86400];
  const want = spanS / targetTicks;
  for (const c of CANDIDATES) if (c >= want) return c;
  return 86400;
}

export class HistoryChart {
  /**
   * @param {object} spec { key, title, series:[{col,label,color}], kind:'si'|'plain',
   *                        unit?, digits?, zeroLine?:bool }
   */
  constructor(root, spec) {
    this.spec = spec;
    this.root = root;
    this.root.classList.add('chart');
    this.rows = [];
    this.colIndex = {};
    this.hover = null;   // { x, y } in CSS px
    this.dpr = 1;
    this.build();
  }

  build() {
    clear(this.root);
    this.head = el('div.chart-head',
      el('span.t', { text: this.spec.title }),
      el('span.cur'));
    this.canvas = el('canvas', { role: 'img', 'aria-label': `${this.spec.title} over time` });
    this.tip = el('div.chart-tip');
    this.root.append(this.head, this.canvas, this.tip);

    this.onMove = (ev) => {
      const r = this.canvas.getBoundingClientRect();
      this.hover = { x: ev.clientX - r.left, y: ev.clientY - r.top };
      this.draw();
    };
    this.onLeave = () => { this.hover = null; this.draw(); };
    this.canvas.addEventListener('pointermove', this.onMove);
    this.canvas.addEventListener('pointerleave', this.onLeave);
    // Touch: a tap places the crosshair rather than scrolling the page away.
    this.canvas.style.touchAction = 'pan-y';
  }

  setData(rows, colIndex) {
    this.rows = rows;
    this.colIndex = colIndex;
    this.draw();
  }

  /** Resize the backing store to the CSS box × devicePixelRatio. */
  resize() {
    const w = Math.max(120, Math.floor(this.canvas.clientWidth));
    const h = Math.max(60, Math.floor(this.canvas.clientHeight));
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    if (this.canvas.width === w * dpr && this.canvas.height === h * dpr) return false;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.dpr = dpr;
    return true;
  }

  /** Extract [t, value] pairs for one series; nulls preserved as null. */
  #points(col) {
    const i = this.colIndex[col];
    if (i === undefined) return [];
    const out = new Array(this.rows.length);
    for (let k = 0; k < this.rows.length; k++) {
      const r = this.rows[k];
      const v = r[i];
      out[k] = [r[0], isNum(v) ? v : null];
    }
    return out;
  }

  draw() {
    this.resize();
    const ctx = this.canvas.getContext('2d');
    if (!ctx) return;
    const dpr = this.dpr;
    const W = this.canvas.width / dpr;
    const H = this.canvas.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const ink = cssVar('--ink', '#111');
    const muted = cssVar('--ink-muted', '#888');
    const grid = cssVar('--hairline-2', '#e0e0e0');
    const axis = cssVar('--axis', '#c0c0c0');

    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;
    if (plotW <= 4 || plotH <= 4) return;

    const seriesPts = this.spec.series.map((s) => ({ ...s, pts: this.#points(s.col) }));

    // ---- domains
    let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
    for (const s of seriesPts) {
      for (const [t, v] of s.pts) {
        if (t < tMin) tMin = t;
        if (t > tMax) tMax = t;
        if (v === null) continue;
        if (v < vMin) vMin = v;
        if (v > vMax) vMax = v;
      }
    }
    if (!Number.isFinite(tMin) || !Number.isFinite(vMin)) {
      ctx.fillStyle = muted;
      ctx.font = '12px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('no data in range', W / 2, H / 2);
      this.head.querySelector('.cur').textContent = DASH;
      return;
    }
    if (tMax - tMin < 1) tMax = tMin + 1;
    if (vMax - vMin < 1e-15) { const c = vMax || 1e-9; vMin = c - Math.abs(c) * 0.5 - 1e-12; vMax = c + Math.abs(c) * 0.5 + 1e-12; }
    if (this.spec.zeroLine) {
      // Symmetric around zero so "fast" and "slow" are visually comparable.
      const m = Math.max(Math.abs(vMin), Math.abs(vMax));
      vMin = -m; vMax = m;
    } else {
      const padv = (vMax - vMin) * 0.08;
      vMin -= padv; vMax += padv;
    }

    const x = (t) => PAD.left + ((t - tMin) / (tMax - tMin)) * plotW;
    const y = (v) => PAD.top + plotH - ((v - vMin) / (vMax - vMin)) * plotH;

    // ---- value axis: pick a unit once for the whole panel
    const scale = this.spec.kind === 'si'
      ? siSecondsUnit(Math.max(Math.abs(vMin), Math.abs(vMax)))
      : { unit: this.spec.unit || '', scale: 1 };
    const yStep = niceStep((vMax - vMin) * scale.scale, Math.max(2, Math.floor(plotH / 28))) / scale.scale;

    ctx.font = '10px system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 1;
    const first = Math.ceil(vMin / yStep) * yStep;
    for (let v = first; v <= vMax + yStep * 1e-9; v += yStep) {
      const py = Math.round(y(v)) + 0.5;
      ctx.strokeStyle = Math.abs(v) < yStep * 1e-6 ? axis : grid;
      ctx.beginPath();
      ctx.moveTo(PAD.left, py);
      ctx.lineTo(W - PAD.right, py);
      ctx.stroke();
      ctx.fillStyle = muted;
      ctx.textAlign = 'right';
      const scaled = v * scale.scale;
      const lbl = this.spec.kind === 'si'
        ? scaled.toFixed(Math.abs(scaled) >= 100 || Math.abs(scaled) < 1e-9 ? 0 : 1)
        : num(v, this.spec.digits ?? 2);
      ctx.fillText(lbl, PAD.left - 5, py);
    }
    // The unit lives in the panel header (set below), not floating over the
    // y-axis labels where it would collide with the topmost tick.
    this.head.querySelector('.t').textContent =
      scale.unit ? `${this.spec.title} (${scale.unit})` : this.spec.title;

    // ---- time axis
    const tStep = niceTimeStep(tMax - tMin, Math.max(2, Math.floor(plotW / 78)));
    const withSec = tStep < 60;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const t0 = Math.ceil(tMin / tStep) * tStep;
    for (let t = t0; t <= tMax; t += tStep) {
      const px = Math.round(x(t)) + 0.5;
      ctx.strokeStyle = grid;
      ctx.beginPath();
      ctx.moveTo(px, PAD.top);
      ctx.lineTo(px, PAD.top + plotH);
      ctx.stroke();
      ctx.fillStyle = muted;
      ctx.fillText(axisTime(t, withSec), px, PAD.top + plotH + 4);
    }
    ctx.strokeStyle = axis;
    ctx.beginPath();
    ctx.moveTo(PAD.left + 0.5, PAD.top);
    ctx.lineTo(PAD.left + 0.5, PAD.top + plotH + 0.5);
    ctx.lineTo(W - PAD.right, PAD.top + plotH + 0.5);
    ctx.stroke();

    // ---- series (2 px lines; a null starts a new subpath = a visible gap)
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    for (const s of seriesPts) {
      ctx.strokeStyle = cssVar(s.color, '#2a78d6');
      ctx.beginPath();
      let pen = false;
      for (const [t, v] of s.pts) {
        if (v === null) { pen = false; continue; }
        const px = x(t), py = y(v);
        if (!pen) { ctx.moveTo(px, py); pen = true; } else ctx.lineTo(px, py);
      }
      ctx.stroke();
    }

    // ---- crosshair + tooltip
    const cur = this.head.querySelector('.cur');
    let sample = null;
    if (this.hover && this.hover.x >= PAD.left && this.hover.x <= W - PAD.right) {
      const tHover = tMin + ((this.hover.x - PAD.left) / plotW) * (tMax - tMin);
      sample = this.#nearest(seriesPts, tHover);
      if (sample) {
        const px = Math.round(x(sample.t)) + 0.5;
        ctx.save();
        ctx.strokeStyle = axis;
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(px, PAD.top);
        ctx.lineTo(px, PAD.top + plotH);
        ctx.stroke();
        ctx.restore();
        for (const s of sample.values) {
          if (s.v === null) continue;
          ctx.fillStyle = cssVar(s.color, '#2a78d6');
          ctx.strokeStyle = cssVar('--surface', '#fff');
          ctx.lineWidth = 2;               // 2 px surface ring so marks stay separate
          ctx.beginPath();
          ctx.arc(px, y(s.v), 4, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        }
        this.#showTip(sample, px, W);
      }
    } else {
      this.tip.classList.remove('on');
    }

    // headline readout: hovered sample, else the latest non-null
    const readout = sample || this.#latest(seriesPts);
    cur.textContent = readout ? this.#fmtAll(readout) : DASH;
    ctx.fillStyle = ink; // leave the context in a sane state
  }

  #nearest(seriesPts, tHover) {
    const ref = seriesPts.find((s) => s.pts.length) || seriesPts[0];
    if (!ref || !ref.pts.length) return null;
    // Points are ascending in t → binary search.
    let lo = 0, hi = ref.pts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (ref.pts[mid][0] < tHover) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(ref.pts[lo - 1][0] - tHover) < Math.abs(ref.pts[lo][0] - tHover)) lo -= 1;
    const t = ref.pts[lo][0];
    return {
      t,
      values: seriesPts.map((s) => ({ label: s.label, color: s.color, v: s.pts[lo] ? s.pts[lo][1] : null })),
    };
  }

  #latest(seriesPts) {
    const ref = seriesPts.find((s) => s.pts.length);
    if (!ref) return null;
    for (let i = ref.pts.length - 1; i >= 0; i--) {
      if (ref.pts[i][1] !== null) {
        const t = ref.pts[i][0];
        return { t, values: seriesPts.map((s) => ({ label: s.label, color: s.color, v: s.pts[i] ? s.pts[i][1] : null })) };
      }
    }
    return null;
  }

  #fmtOne(v) {
    if (v === null) return DASH;
    return this.spec.kind === 'si'
      ? siSeconds(v, { sign: this.spec.zeroLine })
      : `${num(v, this.spec.digits ?? 2)}${this.spec.unit ? ' ' + this.spec.unit : ''}`;
  }

  #fmtAll(sample) {
    if (sample.values.length === 1) return this.#fmtOne(sample.values[0].v);
    return sample.values.map((s) => `${s.label} ${this.#fmtOne(s.v)}`).join(' · ');
  }

  #showTip(sample, px, W) {
    const lines = [axisTime(sample.t, true), this.#fmtAll(sample)];
    this.tip.textContent = lines.join('  ');
    this.tip.classList.add('on');
    const tw = this.tip.offsetWidth || 100;
    const left = Math.max(0, Math.min(W - tw, px - tw / 2));
    this.tip.style.left = `${left}px`;
    this.tip.style.top = `${PAD.top}px`;
  }

  destroy() {
    this.canvas.removeEventListener('pointermove', this.onMove);
    this.canvas.removeEventListener('pointerleave', this.onLeave);
    clear(this.root);
  }
}

// --------------------------------------------------------------- container

const RANGES = [
  { label: '15 m', s: 900 },
  { label: '1 h', s: 3600 },
  { label: '6 h', s: 21600 },
  { label: '24 h', s: 86400 },
];

const PANELS = [
  { key: 'sysoff', title: 'System clock offset', kind: 'si', zeroLine: true, series: [{ col: 'ntp_system_offset_s', label: '', color: '--series-1' }] },
  { key: 'lastoff', title: 'Last offset', kind: 'si', zeroLine: true, series: [{ col: 'ntp_last_offset_s', label: '', color: '--series-7' }] },
  { key: 'freq', title: 'Frequency', kind: 'plain', unit: 'ppm', digits: 2, series: [{ col: 'ntp_frequency_ppm', label: '', color: '--series-2' }] },
  { key: 'sats', title: 'Satellites', kind: 'plain', unit: '', digits: 0, series: [
    { col: 'gps_sats_used', label: 'used', color: '--series-1' },
    { col: 'gps_sats_seen', label: 'seen', color: '--series-gray' },
  ] },
  { key: 'acc', title: 'HDOP / horizontal error', kind: 'plain', unit: '', digits: 2, series: [
    { col: 'gps_hdop', label: 'HDOP', color: '--series-3' },
    { col: 'gps_eph_m', label: 'EPH m', color: '--series-4' },
  ] },
];

export class HistoryPanels {
  /**
   * @param {HTMLElement} root
   * @param {object} ctx { api, store, onRangeChange }
   */
  constructor(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    this.rangeS = ctx.store.settings.historyRangeS || 3600;
    this.columns = [];
    this.colIndex = {};
    this.rows = [];
    this.charts = [];
    this.loading = false;
    this.build();
  }

  build() {
    clear(this.root);
    const seg = el('div.seg', { role: 'group', 'aria-label': 'History range' });
    this.rangeButtons = RANGES.map((r) => {
      const b = el('button', {
        type: 'button', 'aria-pressed': String(r.s === this.rangeS),
        onclick: () => this.setRange(r.s),
      }, r.label);
      seg.append(b);
      return { b, s: r.s };
    });
    this.status = el('span.muted', { style: { fontSize: '12px' } });
    this.root.append(el('div.rowline', seg, el('span.grow'), this.status));

    this.grid = el('div', { style: { display: 'grid', gap: '14px', marginTop: '10px' } });
    this.root.append(this.grid);
    for (const spec of PANELS) {
      const holder = el('div');
      this.grid.append(holder);
      this.charts.push(new HistoryChart(holder, spec));
    }

    // One observer for the whole panel set: a width change redraws them all.
    this.ro = new ResizeObserver(() => this.redraw());
    this.ro.observe(this.grid);
  }

  setRange(s) {
    if (this.rangeS === s) return;
    this.rangeS = s;
    for (const rb of this.rangeButtons) rb.b.setAttribute('aria-pressed', String(rb.s === s));
    this.ctx.store.set({ historyRangeS: s });
    this.load();
  }

  /** Fetch server history for the current range and replace the buffer. */
  async load(signal) {
    if (this.loading) return;
    this.loading = true;
    this.status.textContent = 'loading…';
    try {
      const width = Math.max(200, Math.round(this.grid.clientWidth || 720));
      const data = await this.ctx.api.getHistory(this.rangeS, width, { signal });
      this.columns = Array.isArray(data.columns) ? data.columns : [];
      this.colIndex = {};
      this.columns.forEach((c, i) => { this.colIndex[c] = i; });
      this.rows = Array.isArray(data.rows) ? data.rows : [];
      this.status.textContent = `${this.rows.length} points · every ${num(data.interval_s, 0)} s`;
      this.redraw();
    } catch (err) {
      this.status.textContent = `history unavailable (${err.message || err})`;
    } finally {
      this.loading = false;
    }
  }

  /**
   * Append one live status snapshot so the right edge keeps moving between
   * server fetches. Rows stay sorted and are trimmed to the visible range.
   */
  appendLive(status, tUnix) {
    if (!this.columns.length || !status) return;
    const row = new Array(this.columns.length).fill(null);
    const put = (col, v) => {
      const i = this.colIndex[col];
      if (i !== undefined) row[i] = isNum(v) ? v : null;
    };
    row[0] = tUnix;
    const n = status.ntp || {};
    const g = status.gps || {};
    put('ntp_system_offset_s', n.system_offset_s);
    put('ntp_last_offset_s', n.last_offset_s);
    put('ntp_rms_offset_s', n.rms_offset_s);
    put('ntp_frequency_ppm', n.frequency_ppm);
    put('ntp_stratum', n.stratum);
    put('gps_mode', g.fix && g.fix.mode);
    put('gps_sats_used', g.satellites && g.satellites.used);
    put('gps_sats_seen', g.satellites && g.satellites.seen);
    put('gps_hdop', g.dop && g.dop.hdop);
    put('gps_eph_m', g.accuracy && g.accuracy.eph_m);
    put('gps_time_offset_s', g.time_offset && g.time_offset.offset_s);
    put('lat', g.position && g.position.lat);
    put('lon', g.position && g.position.lon);
    put('alt_hae_m', g.position && g.position.alt_hae_m);

    const last = this.rows[this.rows.length - 1];
    if (last && tUnix <= last[0]) return;    // never go backwards
    this.rows.push(row);

    // Trim to the range, with a cap so a long session cannot grow unbounded.
    const cutoff = tUnix - this.rangeS;
    let drop = 0;
    while (drop < this.rows.length && this.rows[drop][0] < cutoff) drop++;
    if (drop) this.rows.splice(0, drop);
    if (this.rows.length > 20000) this.rows.splice(0, this.rows.length - 20000);
    this.redraw();
  }

  redraw() {
    for (const c of this.charts) c.setData(this.rows, this.colIndex);
  }

  destroy() {
    if (this.ro) { this.ro.disconnect(); this.ro = null; }
    for (const c of this.charts) c.destroy();
    this.charts.length = 0;
    clear(this.root);
  }
}

export default HistoryPanels;
