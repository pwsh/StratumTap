// Client-side session recorder: keeps status snapshots in memory and exports
// them as JSON / CSV / GPX / GeoJSON. Serialisers are pure so they can be
// unit-tested in node; only download() touches the DOM.

import { isoFromUnix } from './format.js';

/** Dotted path lookup that never throws on nulls. */
export function dig(obj, path) {
  let cur = obj;
  for (const key of path.split('.')) {
    if (cur == null) return null;
    cur = cur[key];
  }
  return cur === undefined ? null : cur;
}

/** CSV column list: [header, dotted path into the status payload]. */
export const CSV_COLUMNS = [
  ['ntp.available', 'ntp.available'],
  ['ntp.stratum', 'ntp.stratum'],
  ['ntp.reference_name', 'ntp.reference_name'],
  ['ntp.reference_id', 'ntp.reference_id'],
  ['ntp.system_offset_s', 'ntp.system_offset_s'],
  ['ntp.last_offset_s', 'ntp.last_offset_s'],
  ['ntp.rms_offset_s', 'ntp.rms_offset_s'],
  ['ntp.frequency_ppm', 'ntp.frequency_ppm'],
  ['ntp.residual_freq_ppm', 'ntp.residual_freq_ppm'],
  ['ntp.skew_ppm', 'ntp.skew_ppm'],
  ['ntp.root_delay_s', 'ntp.root_delay_s'],
  ['ntp.root_dispersion_s', 'ntp.root_dispersion_s'],
  ['ntp.update_interval_s', 'ntp.update_interval_s'],
  ['ntp.leap_status', 'ntp.leap_status'],
  ['ntp.synchronized', 'ntp.synchronized'],
  ['gps.fix.mode', 'gps.fix.mode'],
  ['gps.fix.mode_text', 'gps.fix.mode_text'],
  ['gps.fix.status_text', 'gps.fix.status_text'],
  ['gps.fix.fix_text', 'gps.fix.fix_text'],
  ['gps.fix.fix_age_s', 'gps.fix.fix_age_s'],
  ['gps.fix.time_unix', 'gps.fix.time_unix'],
  ['gps.fix.time_age_s', 'gps.fix.time_age_s'],
  ['gps.fix.ept_s', 'gps.fix.ept_s'],
  ['gps.position.lat', 'gps.position.lat'],
  ['gps.position.lon', 'gps.position.lon'],
  ['gps.position.alt_hae_m', 'gps.position.alt_hae_m'],
  ['gps.position.alt_msl_m', 'gps.position.alt_msl_m'],
  ['gps.position.geoid_sep_m', 'gps.position.geoid_sep_m'],
  ['gps.position.grid_square', 'gps.position.grid_square'],
  ['gps.motion.speed_mps', 'gps.motion.speed_mps'],
  ['gps.motion.track_deg', 'gps.motion.track_deg'],
  ['gps.motion.mag_track_deg', 'gps.motion.mag_track_deg'],
  ['gps.motion.climb_mps', 'gps.motion.climb_mps'],
  ['gps.accuracy.epx_m', 'gps.accuracy.epx_m'],
  ['gps.accuracy.epy_m', 'gps.accuracy.epy_m'],
  ['gps.accuracy.epv_m', 'gps.accuracy.epv_m'],
  ['gps.accuracy.eph_m', 'gps.accuracy.eph_m'],
  ['gps.accuracy.sep_m', 'gps.accuracy.sep_m'],
  ['gps.accuracy.eps_mps', 'gps.accuracy.eps_mps'],
  ['gps.accuracy.ept_s', 'gps.accuracy.ept_s'],
  ['gps.dop.xdop', 'gps.dop.xdop'],
  ['gps.dop.ydop', 'gps.dop.ydop'],
  ['gps.dop.vdop', 'gps.dop.vdop'],
  ['gps.dop.hdop', 'gps.dop.hdop'],
  ['gps.dop.pdop', 'gps.dop.pdop'],
  ['gps.dop.tdop', 'gps.dop.tdop'],
  ['gps.dop.gdop', 'gps.dop.gdop'],
  ['gps.time_offset.source', 'gps.time_offset.source'],
  ['gps.time_offset.offset_s', 'gps.time_offset.offset_s'],
  ['gps.satellites.seen', 'gps.satellites.seen'],
  ['gps.satellites.used', 'gps.satellites.used'],
];

function csvCell(v) {
  if (v === null || v === undefined) return '';
  const s = typeof v === 'boolean' ? (v ? 'true' : 'false') : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function xmlEscape(s) {
  return String(s).replace(/[<>&'"]/g, (c) => (
    { '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[c]
  ));
}

/** A sample has a usable position when lat and lon are both finite numbers. */
export function hasFix(sample) {
  const lat = dig(sample.status, 'gps.position.lat');
  const lon = dig(sample.status, 'gps.position.lon');
  return Number.isFinite(lat) && Number.isFinite(lon);
}

// ------------------------------------------------------------ serialisers

export function toJSON(samples) {
  return JSON.stringify(samples, null, 2);
}

export function toCSV(samples) {
  const head = ['t_iso', 't_unix', ...CSV_COLUMNS.map(([h]) => h)];
  const lines = [head.join(',')];
  for (const s of samples) {
    const tUnix = s.t_received / 1000;
    const row = [isoFromUnix(tUnix), tUnix.toFixed(3)];
    for (const [, path] of CSV_COLUMNS) row.push(csvCell(dig(s.status, path)));
    lines.push(row.join(','));
  }
  return lines.join('\n') + '\n';
}

export function toGPX(samples, { name = 'StratumTap track', creator = 'StratumTap' } = {}) {
  const pts = [];
  for (const s of samples) {
    if (!hasFix(s)) continue;
    const p = s.status.gps.position;
    // Prefer the GPS fix time; fall back to the browser receive time.
    const tUnix = dig(s.status, 'gps.fix.time_unix') ?? s.t_received / 1000;
    const ele = p.alt_msl_m ?? p.alt_hae_m;
    pts.push(
      `      <trkpt lat="${p.lat.toFixed(7)}" lon="${p.lon.toFixed(7)}">` +
      (Number.isFinite(ele) ? `<ele>${ele.toFixed(2)}</ele>` : '') +
      `<time>${isoFromUnix(tUnix)}</time>` +
      `</trkpt>`
    );
  }
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="${xmlEscape(creator)}" xmlns="http://www.topografix.com/GPX/1/1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">
  <metadata><name>${xmlEscape(name)}</name><time>${new Date().toISOString()}</time></metadata>
  <trk>
    <name>${xmlEscape(name)}</name>
    <trkseg>
${pts.join('\n')}
    </trkseg>
  </trk>
</gpx>
`;
}

export function toGeoJSON(samples) {
  const coords = [];
  const features = [];
  for (const s of samples) {
    if (!hasFix(s)) continue;
    const p = s.status.gps.position;
    const alt = p.alt_msl_m ?? p.alt_hae_m;
    const c = Number.isFinite(alt) ? [p.lon, p.lat, alt] : [p.lon, p.lat];
    coords.push(c);
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: c },
      properties: {
        t_iso: isoFromUnix(s.t_received / 1000),
        t_unix: s.t_received / 1000,
        fix_mode: dig(s.status, 'gps.fix.mode'),
        fix_text: dig(s.status, 'gps.fix.fix_text'),
        eph_m: dig(s.status, 'gps.accuracy.eph_m'),
        sep_m: dig(s.status, 'gps.accuracy.sep_m'),
        hdop: dig(s.status, 'gps.dop.hdop'),
        sats_used: dig(s.status, 'gps.satellites.used'),
        sats_seen: dig(s.status, 'gps.satellites.seen'),
        speed_mps: dig(s.status, 'gps.motion.speed_mps'),
        track_deg: dig(s.status, 'gps.motion.track_deg'),
        ntp_system_offset_s: dig(s.status, 'ntp.system_offset_s'),
      },
    });
  }
  const fc = { type: 'FeatureCollection', features: [] };
  if (coords.length >= 2) {
    fc.features.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: coords },
      properties: { name: 'track', points: coords.length },
    });
  }
  fc.features.push(...features);
  return JSON.stringify(fc, null, 2);
}

export const FORMATS = {
  json: { ext: 'json', mime: 'application/json', build: toJSON },
  csv: { ext: 'csv', mime: 'text/csv', build: toCSV },
  gpx: { ext: 'gpx', mime: 'application/gpx+xml', build: toGPX },
  geojson: { ext: 'geojson', mime: 'application/geo+json', build: toGeoJSON },
};

/** Trigger a browser download. DOM-only — kept out of the serialisers above. */
export function download(filename, text, mime = 'text/plain') {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next task so Safari has finished reading the blob.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ------------------------------------------------------------- recorder

export class Recorder {
  constructor(cap = 50000) {
    this.cap = cap;
    /** @type {{t_received:number, status:object}[]} */
    this.samples = [];
    this.recording = false;
    this.startedAt = null;
    this.dropped = 0;
    this.listeners = new Set();
  }

  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  #emit() { for (const fn of this.listeners) { try { fn(this); } catch (e) { console.error(e); } } }

  start() {
    if (this.recording) return;
    this.recording = true;
    if (this.startedAt == null) this.startedAt = Date.now();
    this.#emit();
  }

  stop() {
    if (!this.recording) return;
    this.recording = false;
    this.#emit();
  }

  toggle() { this.recording ? this.stop() : this.start(); }

  clear() {
    this.samples.length = 0;
    this.dropped = 0;
    this.startedAt = this.recording ? Date.now() : null;
    this.#emit();
  }

  setCap(n) {
    this.cap = Math.max(100, Math.min(1000000, Math.trunc(n) || 50000));
    this.#trim();
    this.#emit();
  }

  /** Append one status snapshot; ignored when not recording. */
  add(status, t = Date.now()) {
    if (!this.recording || !status) return false;
    this.samples.push({ t_received: t, status });
    this.#trim();
    this.#emit();
    return true;
  }

  #trim() {
    if (this.samples.length <= this.cap) return;
    this.dropped += this.samples.length - this.cap;
    this.samples.splice(0, this.samples.length - this.cap);
  }

  get count() { return this.samples.length; }

  /** Wall-clock span of the recording, in seconds. */
  get durationS() {
    if (this.samples.length < 2) return this.startedAt ? (Date.now() - this.startedAt) / 1000 : 0;
    return (this.samples[this.samples.length - 1].t_received - this.samples[0].t_received) / 1000;
  }

  /** Rough in-memory/JSON size: measure one sample and multiply. */
  approxBytes() {
    if (!this.samples.length) return 0;
    const one = JSON.stringify(this.samples[this.samples.length - 1]).length;
    return one * this.samples.length;
  }

  /** Positions of every sample that has a fix — the map track. */
  track() {
    const out = [];
    for (const s of this.samples) {
      if (hasFix(s)) out.push([s.status.gps.position.lat, s.status.gps.position.lon]);
    }
    return out;
  }

  export(kind, hostname = 'stratumtap') {
    const f = FORMATS[kind];
    if (!f) throw new Error(`unknown export format: ${kind}`);
    const text = f.build(this.samples);
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    download(`${hostname}-${stamp}.${f.ext}`, text, f.mime);
    return text.length;
  }
}

export default Recorder;
