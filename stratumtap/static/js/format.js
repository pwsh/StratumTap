// Pure formatting helpers. No DOM access — importable in node for unit checks.

export const DASH = '—'; // em dash: "no value"
const MICRO = 'µ';
const NBSP = ' ';        // narrow no-break space between number and unit

/** True for values we can actually format as a number. */
export function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

/** Round to `d` significant-ish decimals and strip trailing zeros. */
function trim(v, d) {
  const s = v.toFixed(d);
  return d > 0 ? s.replace(/\.?0+$/, '') : s;
}

/**
 * SI-format a duration given in seconds: 3.72e-7 -> "372 ns".
 * Picks the unit so the mantissa lands in 1..999 and keeps ~3 significant digits.
 */
export function siSeconds(sec, { sign = false, digits = null } = {}) {
  if (!isNum(sec)) return DASH;
  const neg = sec < 0;
  const a = Math.abs(sec);
  let v, unit;
  if (a === 0) { v = 0; unit = 's'; }
  else if (a < 1e-6) { v = a * 1e9; unit = 'ns'; }
  else if (a < 1e-3) { v = a * 1e6; unit = MICRO + 's'; }
  else if (a < 1) { v = a * 1e3; unit = 'ms'; }
  else if (a < 60) { v = a; unit = 's'; }
  else { return (neg ? '-' : sign ? '+' : '') + duration(a); }
  // 3 significant digits: 372 / 37.2 / 3.72
  const d = digits != null ? digits : v >= 100 ? 0 : v >= 10 ? 1 : 2;
  const body = trim(v, d);
  const s = neg ? '-' : sign ? '+' : '';
  return `${s}${body}${NBSP}${unit}`;
}

/** Just the unit siSeconds() would choose — for axis labels. */
export function siSecondsUnit(sec) {
  const a = Math.abs(isNum(sec) ? sec : 0);
  if (a === 0) return { unit: 's', scale: 1 };
  if (a < 1e-6) return { unit: 'ns', scale: 1e9 };
  if (a < 1e-3) return { unit: MICRO + 's', scale: 1e6 };
  if (a < 1) return { unit: 'ms', scale: 1e3 };
  return { unit: 's', scale: 1 };
}

/** "fast"/"slow" word for a signed clock offset (positive = system clock fast). */
export function fastSlow(sec) {
  if (!isNum(sec) || sec === 0) return '';
  return sec > 0 ? 'fast' : 'slow';
}

/** Coarse duration for humans: "1 h 04 m", "3 d 02 h", "45 s". */
export function duration(sec) {
  if (!isNum(sec)) return DASH;
  const a = Math.abs(sec);
  if (a < 60) return `${trim(a, a < 10 ? 1 : 0)}${NBSP}s`;
  const m = Math.floor(a / 60), s = Math.floor(a % 60);
  if (a < 3600) return `${m}${NBSP}m ${pad(s, 2)}${NBSP}s`;
  const h = Math.floor(a / 3600);
  if (a < 86400) return `${h}${NBSP}h ${pad(Math.floor((a % 3600) / 60), 2)}${NBSP}m`;
  return `${Math.floor(a / 86400)}${NBSP}d ${pad(Math.floor((a % 86400) / 3600), 2)}${NBSP}h`;
}

/** "1.2 s ago" / "just now" / "in 3 s". */
export function relTime(deltaS) {
  if (!isNum(deltaS)) return DASH;
  if (deltaS < 0) return `in ${duration(-deltaS)}`;
  if (deltaS < 0.15) return 'just now';
  return `${duration(deltaS)} ago`;
}

export function pad(n, width = 2) {
  return String(Math.trunc(Math.abs(n))).padStart(width, '0');
}

/** Fixed-decimal number with a unit, or the em dash. */
export function num(v, digits = 2, unit = '') {
  if (!isNum(v)) return DASH;
  const body = v.toFixed(digits);
  return unit ? `${body}${NBSP}${unit}` : body;
}

export function signedNum(v, digits = 2, unit = '') {
  if (!isNum(v)) return DASH;
  const body = (v >= 0 ? '+' : '') + v.toFixed(digits);
  return unit ? `${body}${NBSP}${unit}` : body;
}

export function ppm(v, digits = 3) {
  if (!isNum(v)) return DASH;
  return `${Math.abs(v).toFixed(digits)}${NBSP}ppm ${v >= 0 ? 'fast' : 'slow'}`;
}

// ---------------------------------------------------------------- units

const FT_PER_M = 3.280839895;
const NM_PER_M = 1 / 1852;

/** Distance in the chosen unit system. nautical uses meters for small values. */
export function distance(m, units = 'metric', digits = 1) {
  if (!isNum(m)) return DASH;
  if (units === 'imperial') return `${(m * FT_PER_M).toFixed(digits)}${NBSP}ft`;
  return `${m.toFixed(digits)}${NBSP}m`;
}

export function altitude(m, units = 'metric') {
  return distance(m, units, 1);
}

/** Speed: m/s in, km/h / mph / kn out. */
export function speed(mps, units = 'metric', digits = 1) {
  if (!isNum(mps)) return DASH;
  if (units === 'imperial') return `${(mps * FT_PER_M * 3600 / 5280).toFixed(digits)}${NBSP}mph`;
  if (units === 'nautical') return `${(mps * NM_PER_M * 3600).toFixed(digits)}${NBSP}kn`;
  return `${(mps * 3.6).toFixed(digits)}${NBSP}km/h`;
}

export function degrees(d, digits = 1) {
  if (!isNum(d)) return DASH;
  return `${d.toFixed(digits)}°`;
}

/** Decimal degrees with hemisphere letter: "41.71343° N". */
export function latLonDec(v, axis, digits = 6) {
  if (!isNum(v)) return DASH;
  const h = axis === 'lat' ? (v >= 0 ? 'N' : 'S') : (v >= 0 ? 'E' : 'W');
  return `${Math.abs(v).toFixed(digits)}°${NBSP}${h}`;
}

/** Degrees/minutes/seconds: "41° 42' 48.36\" N". */
export function latLonDMS(v, axis, secDigits = 2) {
  if (!isNum(v)) return DASH;
  const h = axis === 'lat' ? (v >= 0 ? 'N' : 'S') : (v >= 0 ? 'E' : 'W');
  let a = Math.abs(v);
  const d = Math.floor(a);
  a = (a - d) * 60;
  let m = Math.floor(a);
  let s = (a - m) * 60;
  if (s >= 60 - 5e-7) { s = 0; m += 1; }
  return `${d}°${NBSP}${pad(m)}′${NBSP}${s.toFixed(secDigits)}″${NBSP}${h}`;
}

// ---------------------------------------------------------------- clocks

/** UTC "HH:MM:SS.mmm" from a Date (or ms epoch). */
export function utcClock(ms, withMillis = true) {
  const d = ms instanceof Date ? ms : new Date(ms);
  if (Number.isNaN(d.getTime())) return DASH;
  const base = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  return withMillis ? `${base}.${pad(d.getUTCMilliseconds(), 3)}` : base;
}

export function utcDate(ms) {
  const d = ms instanceof Date ? ms : new Date(ms);
  if (Number.isNaN(d.getTime())) return DASH;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** Local wall clock "HH:MM:SS" plus the zone name. */
export function localClock(ms) {
  const d = ms instanceof Date ? ms : new Date(ms);
  if (Number.isNaN(d.getTime())) return DASH;
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function localZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
  } catch {
    return 'local';
  }
}

/** ISO-8601 UTC with millisecond precision from Unix seconds. */
export function isoFromUnix(sec) {
  if (!isNum(sec)) return '';
  return new Date(sec * 1000).toISOString();
}

/** Short ISO-ish time for chart axes: "14:17" or "14:17:51". */
export function axisTime(unixSec, withSeconds = false) {
  if (!isNum(unixSec)) return '';
  const d = new Date(unixSec * 1000);
  const b = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withSeconds ? `${b}:${pad(d.getSeconds())}` : b;
}

/** Bytes as "12.4 kB" / "3.1 MB". */
export function bytes(n) {
  if (!isNum(n)) return DASH;
  if (n < 1024) return `${Math.round(n)}${NBSP}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}${NBSP}kB`;
  return `${(n / 1048576).toFixed(1)}${NBSP}MB`;
}

/**
 * chrony's reachability register, shown exactly as the number the API supplies.
 * `chronyc` prints it octal in the text report (377 = the last 8 polls all
 * answered) while the CSV form is decimal, and the API passes the value through
 * — so we do not reinterpret it. When it is in 0..255 we can also offer the bit
 * pattern, which is what reachTitle() is for.
 */
export function reach(v) {
  if (!isNum(v)) return DASH;
  return String(v);
}

/** Tooltip for a reach value: the bit pattern when the value is a byte. */
export function reachTitle(v) {
  if (!isNum(v)) return 'reachability register';
  if (v >= 0 && v <= 255) {
    return `reachability register: ${v.toString(2).padStart(8, '0')} (last 8 polls, newest first)`;
  }
  return `reachability register (chronyc prints this octal; 377 = all of the last 8 polls answered)`;
}
