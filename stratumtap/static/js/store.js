// App state + persisted settings, with a minimal pub/sub.
// No DOM access at module scope (localStorage is guarded so node can import it).

const KEY = 'stratumtap.settings.v1';

export const DEFAULTS = Object.freeze({
  refreshS: 2,          // 0 = off
  paused: false,
  correction: true,     // correct displayed server time for network delay
  units: 'metric',      // metric | imperial | nautical
  theme: 'auto',        // auto | light | dark
  mapFollow: true,
  pauseWhenHidden: true,
  recordCap: 50000,
  historyRangeS: 3600,
  // Live raw stream panel (detail view). `on` is the user's last Connect state;
  // the stream is never opened unless it is true.
  rawStream: {
    on: false,
    nmea: true,
    gpsd: true,
    ntp: true,
    filter: '',
    autoscroll: true,
  },
});

function safeStorage() {
  try {
    if (typeof localStorage === 'undefined') return null;
    localStorage.getItem(KEY);
    return localStorage;
  } catch {
    return null; // private mode / blocked cookies
  }
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function loadSettings() {
  const ls = safeStorage();
  if (!ls) return { ...DEFAULTS };
  try {
    const raw = JSON.parse(ls.getItem(KEY) || '{}');
    const out = { ...DEFAULTS };
    for (const k of Object.keys(DEFAULTS)) {
      const def = DEFAULTS[k];
      const val = raw[k];
      if (val === undefined || val === null) continue;
      if (isPlainObject(def)) {
        // Nested groups (rawStream): merge key by key so a default added in a
        // later version still appears for someone with an old stored blob.
        if (!isPlainObject(val)) continue;
        const merged = { ...def };
        for (const nk of Object.keys(def)) {
          if (val[nk] !== undefined && typeof val[nk] === typeof def[nk]) merged[nk] = val[nk];
        }
        out[k] = merged;
      } else if (typeof val === typeof def) {
        out[k] = val;
      }
    }
    return out;
  } catch {
    return { ...DEFAULTS };
  }
}

export class Store {
  constructor() {
    this.settings = loadSettings();
    /** last successful /api/v1/status payload */
    this.status = null;
    /** browser epoch ms when `status` arrived */
    this.statusAt = null;
    this.sources = null;
    this.sourcesAt = null;
    this.config = null;
    /** 'init' | 'ok' | 'degraded' | 'failing' */
    this.connection = 'init';
    this.lastError = null;
    this.consecutiveErrors = 0;
    this.nextPollAt = null;
    this.userSetRefresh = false; // true once the user picks a rate, so config can't override it
    this.listeners = new Set();
  }

  /** subscribe(fn) → unsubscribe(). fn(store, reason). */
  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  emit(reason = 'change') {
    for (const fn of this.listeners) {
      try { fn(this, reason); } catch (err) { console.error('store listener', err); }
    }
  }

  /** Patch settings, persist, notify. */
  set(patch, reason = 'settings') {
    let changed = false;
    for (const [k, v] of Object.entries(patch)) {
      // Nested groups are compared by value; identity would always "change".
      if (isPlainObject(v) && isPlainObject(this.settings[k])) {
        if (JSON.stringify(this.settings[k]) !== JSON.stringify(v)) {
          this.settings[k] = { ...this.settings[k], ...v };
          changed = true;
        }
      } else if (this.settings[k] !== v) {
        this.settings[k] = v;
        changed = true;
      }
    }
    if (!changed) return false;
    this.persist();
    this.emit(reason);
    return true;
  }

  persist() {
    const ls = safeStorage();
    if (!ls) return;
    try { ls.setItem(KEY, JSON.stringify(this.settings)); } catch { /* quota */ }
  }

  setConfig(cfg) {
    this.config = cfg;
    // Adopt the server default only if the user has not chosen a rate themselves.
    if (!this.userSetRefresh && cfg && typeof cfg.default_refresh_s === 'number') {
      const ls = safeStorage();
      const stored = ls ? ls.getItem(KEY) : null;
      if (!stored || JSON.parse(stored).refreshS === undefined) {
        this.settings.refreshS = cfg.default_refresh_s;
      }
    }
    this.emit('config');
  }

  setStatus(status, at = Date.now()) {
    this.status = status;
    this.statusAt = at;
    this.consecutiveErrors = 0;
    this.lastError = null;
    this.connection = degradedFrom(status) ? 'degraded' : 'ok';
    this.emit('status');
  }

  setSources(sources, at = Date.now()) {
    this.sources = sources;
    this.sourcesAt = at;
    this.emit('sources');
  }

  setError(err) {
    this.consecutiveErrors += 1;
    this.lastError = err ? (err.message || String(err)) : 'request failed';
    this.connection = this.consecutiveErrors >= 2 ? 'failing' : 'degraded';
    this.emit('error');
  }

  refreshChoices() {
    const c = this.config && Array.isArray(this.config.refresh_choices_s)
      ? this.config.refresh_choices_s.filter((n) => typeof n === 'number' && n > 0)
      : [1, 2, 5, 10, 30, 60];
    return c.length ? c : [1, 2, 5, 10, 30, 60];
  }
}

/** A payload is "degraded" when any present domain reports itself unavailable. */
export function degradedFrom(status) {
  if (!status) return true;
  const doms = [status.ntp, status.gps];
  return doms.some((d) => d && d.available === false);
}

export default Store;
