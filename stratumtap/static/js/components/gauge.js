// Auto-scaling time-offset gauge.
//
// A 180° arc: zero at top center, negative (system clock SLOW) sweeping left,
// positive (FAST) sweeping right. The needle's color comes from the ABSOLUTE
// offset, never from its position on the arc — otherwise a tiny offset on a
// tiny scale would look alarming.
//
// Scale selection
// ---------------
// Full-scale is drawn from a fixed decade ladder so the eye can compare two
// glances at different times. We track the recent peak (last 60 values) and
// pick the smallest rung that is >= 1.25 x that peak, giving the needle a
// little headroom so it does not sit pinned at the end.
//
// Shrinking is deliberately sticky: a rung that is too big is merely
// unflattering, but a rung that keeps flapping is unreadable. We only step
// down after the value has stayed under 20% of the current full scale for 30
// consecutive samples. Growing is immediate — a spike must be visible at once.

import { el, clear, offsetLevel } from './tiles.js';
import { siSeconds, DASH, isNum } from '../format.js';

const SCALES = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1, 10];
const PEAK_WINDOW = 60;
const SHRINK_SAMPLES = 30;
const SHRINK_FRACTION = 0.2;
const HEADROOM = 1.25;

const W = 320, H = 190;
const CX = W / 2, CY = 160;   // arc center, low in the box so the half-circle fills the box
const R_OUT = 128, R_IN = 96;

/** Map a value in [-full, +full] to an angle in radians. -90°..+90° from vertical. */
function angleFor(value, full) {
  const clamped = Math.max(-1, Math.min(1, value / full));
  return clamped * (Math.PI / 2);
}

/** Point on the arc at `theta` (0 = straight up) and radius r. */
function pt(theta, r) {
  return [CX + Math.sin(theta) * r, CY - Math.cos(theta) * r];
}

function arcPath(from, to, r) {
  const [x0, y0] = pt(from, r);
  const [x1, y1] = pt(to, r);
  const large = Math.abs(to - from) > Math.PI ? 1 : 0;
  const sweep = to > from ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} ${sweep} ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

function svg(tag, attrs) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v !== null && v !== undefined) n.setAttribute(k, String(v));
  }
  return n;
}

export class Gauge {
  constructor(root, { label = 'System clock offset' } = {}) {
    this.root = root;
    this.label = label;
    this.recent = [];            // ring of the last PEAK_WINDOW |values|
    this.scaleIdx = 0;
    this.belowCount = 0;
    this.value = null;
    this.build();
  }

  build() {
    clear(this.root);
    this.root.classList.add('gauge');

    const s = svg('svg', {
      viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': `${this.label} gauge`,
      preserveAspectRatio: 'xMidYMid meet',
    });

    // track
    s.append(svg('path', {
      d: arcPath(-Math.PI / 2, Math.PI / 2, (R_OUT + R_IN) / 2),
      fill: 'none', stroke: 'var(--surface-sunken)', 'stroke-width': R_OUT - R_IN,
      'stroke-linecap': 'butt',
    }));
    s.append(svg('path', {
      d: arcPath(-Math.PI / 2, Math.PI / 2, R_OUT),
      fill: 'none', stroke: 'var(--hairline-2)', 'stroke-width': 1,
    }));
    s.append(svg('path', {
      d: arcPath(-Math.PI / 2, Math.PI / 2, R_IN),
      fill: 'none', stroke: 'var(--hairline-2)', 'stroke-width': 1,
    }));

    // graticule at ±25/50/75/100 % of full scale
    for (const frac of [-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1]) {
      const th = frac * (Math.PI / 2);
      const major = frac === 0 || Math.abs(frac) === 1;
      const [xa, ya] = pt(th, R_IN);
      const [xb, yb] = pt(th, major ? R_OUT : R_OUT - 7);
      s.append(svg('line', {
        x1: xa.toFixed(2), y1: ya.toFixed(2), x2: xb.toFixed(2), y2: yb.toFixed(2),
        stroke: major ? 'var(--axis)' : 'var(--hairline-2)', 'stroke-width': major ? 1.5 : 1,
      }));
    }

    // ±RMS band (drawn under the needle, updated later)
    this.rmsBand = svg('path', {
      d: '', fill: 'none', stroke: 'var(--accent)', 'stroke-width': R_OUT - R_IN,
      opacity: '0.16', 'stroke-linecap': 'butt',
    });
    s.append(this.rmsBand);

    // thin marker for the last measured offset
    this.lastMark = svg('line', {
      x1: 0, y1: 0, x2: 0, y2: 0, stroke: 'var(--ink-2)', 'stroke-width': 2,
      'stroke-linecap': 'round', opacity: '0.75',
    });
    s.append(this.lastMark);

    // needle
    this.needle = svg('line', {
      x1: CX, y1: CY, x2: CX, y2: CY - R_OUT + 4,
      stroke: 'var(--ink)', 'stroke-width': 3, 'stroke-linecap': 'round',
    });
    s.append(this.needle);
    s.append(svg('circle', { cx: CX, cy: CY, r: 5, fill: 'var(--surface)', stroke: 'var(--axis)', 'stroke-width': 1.5 }));

    // end labels (full scale, both ends) + zero
    this.lblNeg = svg('text', { x: 8, y: CY - 4, class: 'sky-label', 'text-anchor': 'start' });
    this.lblPos = svg('text', { x: W - 8, y: CY - 4, class: 'sky-label', 'text-anchor': 'end' });
    const zero = svg('text', { x: CX, y: 18, class: 'sky-label', 'text-anchor': 'middle' });
    zero.textContent = '0';
    s.append(this.lblNeg, this.lblPos, zero);

    const slow = svg('text', { x: 8, y: CY + 14, class: 'sky-label', 'text-anchor': 'start' });
    slow.textContent = 'slow';
    const fast = svg('text', { x: W - 8, y: CY + 14, class: 'sky-label', 'text-anchor': 'end' });
    fast.textContent = 'fast';
    s.append(slow, fast);

    this.svgEl = s;
    this.readout = el('div.readout', { text: DASH });
    this.sub = el('div.sub', { text: 'waiting for data' });
    this.root.append(s, this.readout, this.sub);
  }

  /** Choose the full-scale rung. Returns the chosen value in seconds. */
  #chooseScale(value) {
    const a = isNum(value) ? Math.abs(value) : 0;
    this.recent.push(a);
    if (this.recent.length > PEAK_WINDOW) this.recent.shift();
    let peak = 0;
    for (const v of this.recent) if (v > peak) peak = v;

    const need = Math.max(a, peak) * HEADROOM;
    let want = SCALES.findIndex((s) => s >= need);
    if (want < 0) want = SCALES.length - 1;

    if (want > this.scaleIdx) {
      // Grow immediately: a spike has to be on screen this frame.
      this.scaleIdx = want;
      this.belowCount = 0;
    } else if (want < this.scaleIdx) {
      // Shrink only after a sustained quiet period (hysteresis).
      if (a < SCALES[this.scaleIdx] * SHRINK_FRACTION) this.belowCount += 1;
      else this.belowCount = 0;
      if (this.belowCount >= SHRINK_SAMPLES) {
        this.scaleIdx = want;
        this.belowCount = 0;
      }
    } else {
      this.belowCount = 0;
    }
    return SCALES[this.scaleIdx];
  }

  /**
   * @param {number|null} value    ntp.system_offset_s
   * @param {object} extra         { lastOffset, rms, secondary: {label, value} }
   */
  update(value, extra = {}) {
    const full = this.#chooseScale(value);
    const level = offsetLevel(value);
    const color = level === 'good' ? 'var(--good)'
      : level === 'warning' ? 'var(--warning)'
        : level === 'critical' ? 'var(--critical)' : 'var(--ink-muted)';

    const th = angleFor(isNum(value) ? value : 0, full);
    const [nx, ny] = pt(th, R_OUT - 4);
    this.needle.setAttribute('x2', nx.toFixed(2));
    this.needle.setAttribute('y2', ny.toFixed(2));
    this.needle.setAttribute('stroke', color);
    this.needle.setAttribute('opacity', isNum(value) ? '1' : '0.35');

    // last-offset tick
    if (isNum(extra.lastOffset)) {
      const t = angleFor(extra.lastOffset, full);
      const [ax, ay] = pt(t, R_IN + 2);
      const [bx, by] = pt(t, R_OUT - 2);
      this.lastMark.setAttribute('x1', ax.toFixed(2));
      this.lastMark.setAttribute('y1', ay.toFixed(2));
      this.lastMark.setAttribute('x2', bx.toFixed(2));
      this.lastMark.setAttribute('y2', by.toFixed(2));
      this.lastMark.setAttribute('opacity', '0.75');
    } else {
      this.lastMark.setAttribute('opacity', '0');
    }

    // ±rms band centered on zero
    if (isNum(extra.rms) && extra.rms > 0) {
      const r = Math.min(extra.rms, full);
      this.rmsBand.setAttribute('d', arcPath(angleFor(-r, full), angleFor(r, full), (R_OUT + R_IN) / 2));
      this.rmsBand.setAttribute('opacity', '0.16');
    } else {
      this.rmsBand.setAttribute('opacity', '0');
    }

    const fullLabel = siSeconds(full, { digits: 0 });
    this.lblNeg.textContent = `−${fullLabel}`;
    this.lblPos.textContent = `+${fullLabel}`;

    this.readout.textContent = siSeconds(value, { sign: true });
    this.readout.dataset.level = level || '';
    this.svgEl.setAttribute('aria-label',
      `${this.label}: ${siSeconds(value, { sign: true })}, full scale ±${fullLabel}`);

    const bits = [`full scale ±${fullLabel}`];
    if (isNum(value)) bits.push(value >= 0 ? 'system clock fast' : 'system clock slow');
    if (extra.secondary && isNum(extra.secondary.value)) {
      bits.push(`${extra.secondary.label}: ${siSeconds(extra.secondary.value, { sign: true })}`);
    }
    this.sub.textContent = bits.join(' · ');
    this.value = value;
  }

  destroy() { clear(this.root); }
}

export default Gauge;
