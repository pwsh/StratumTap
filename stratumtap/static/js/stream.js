// SSE client for GET /api/v1/stream (see the "Streaming (v0.2)" section of
// docs/api-contract.md). Wraps EventSource, parses the named events (all payloads are
// JSON) and keeps an explicit state machine the UI can render.
//
// EventSource reconnects by itself after a dropped connection, but NOT after a
// response the spec calls a "fatal" one — a non-200 status (our 503 "too many
// stream clients") or the wrong content type closes the connection for good.
// We therefore drive our own retry when readyState lands on CLOSED, and give up
// after REFUSE_ERRORS failures inside REFUSE_WINDOW_MS so a capped server does
// not get hammered; the UI then offers a manual retry.
//
// No DOM access at module scope, so node can import this file.

export const STREAM_PATH = '/api/v1/stream';

const REFUSE_ERRORS = 3;
const REFUSE_WINDOW_MS = 10000;
const RETRY_MS = 2000;

/** 'disconnected' | 'connecting' | 'live' | 'reconnecting' | 'refused' */
export const STATES = ['disconnected', 'connecting', 'live', 'reconnecting', 'refused'];

export const REFUSED_MESSAGE = 'server refused (too many clients?)';

/** Event names we subscribe to on the EventSource. */
export const SERVER_EVENTS = ['hello', 'nmea', 'gpsd', 'ntp', 'status', 'stats'];

export class RawStream {
  /**
   * @param {object} [opts]
   * @param {string[]} [opts.events] which event families to ask the server for
   * @param {string} [opts.path] override for tests
   * @param {number} [opts.statusInterval] seconds; only meaningful with 'status'
   */
  constructor({ events = ['nmea', 'gpsd'], path = STREAM_PATH, statusInterval = null } = {}) {
    this.events = [...events];
    this.path = path;
    this.statusInterval = statusInterval;
    this.state = 'disconnected';
    this.message = null;
    /** last `hello` payload (client_id, queue size, server info) */
    this.hello = null;
    /** last `stats` payload */
    this.stats = null;
    this.es = null;
    this.errorTimes = [];
    this.retryTimer = null;
    this.listeners = new Map();
    this.wanted = false;   // the user asked for a connection
  }

  // ------------------------------------------------------------- pub/sub

  /** on('nmea', fn) → off(). Also emits 'state' and 'any'. */
  on(event, fn) {
    let set = this.listeners.get(event);
    if (!set) { set = new Set(); this.listeners.set(event, set); }
    set.add(fn);
    return () => set.delete(fn);
  }

  emit(event, data) {
    const set = this.listeners.get(event);
    if (set) for (const fn of set) { try { fn(data, event); } catch (err) { console.error('stream listener', event, err); } }
    if (event !== 'any') {
      const all = this.listeners.get('any');
      if (all) for (const fn of all) { try { fn(data, event); } catch (err) { console.error('stream listener', err); } }
    }
  }

  // -------------------------------------------------------------- state

  setState(state, message = null) {
    if (this.state === state && this.message === message) return;
    this.state = state;
    this.message = message;
    this.emit('state', { state, message });
  }

  get connected() { return this.state === 'live'; }
  get active() { return this.es !== null || this.retryTimer !== null; }

  // ---------------------------------------------------------------- url

  url() {
    const u = new URL(this.path, location.origin);
    u.searchParams.set('events', this.events.join(','));
    if (this.statusInterval) u.searchParams.set('status_interval', String(this.statusInterval));
    return u.toString();
  }

  /** Change the requested event families; reconnects if already streaming. */
  setEvents(list) {
    const next = [...list];
    if (next.join(',') === this.events.join(',')) return false;
    this.events = next;
    if (this.wanted) { this.stop(true); this.connect(); }
    return true;
  }

  // ------------------------------------------------------- connect/close

  connect() {
    this.wanted = true;
    this.errorTimes = [];
    this.open();
  }

  /** Internal: (re)open the EventSource without resetting the error budget. */
  open() {
    if (typeof EventSource === 'undefined') {
      this.setState('refused', 'this browser has no EventSource');
      return;
    }
    if (!this.events.length) {
      this.setState('disconnected', 'no event sources selected');
      return;
    }
    this.clearRetry();
    this.dropSource();
    this.setState(this.errorTimes.length ? 'reconnecting' : 'connecting');
    let es;
    try {
      es = new EventSource(this.url());
    } catch (err) {
      this.setState('refused', (err && err.message) || 'could not open the stream');
      return;
    }
    this.es = es;
    es.onopen = () => {
      if (this.es !== es) return;
      this.errorTimes = [];
      this.setState('live');
    };
    es.onerror = () => {
      if (this.es !== es) return;
      this.onError(es);
    };
    for (const name of SERVER_EVENTS) {
      es.addEventListener(name, (ev) => {
        if (this.es !== es) return;
        this.onMessage(name, ev);
      });
    }
  }

  onMessage(name, ev) {
    let data = null;
    if (ev && typeof ev.data === 'string' && ev.data.length) {
      try {
        data = JSON.parse(ev.data);
      } catch {
        this.emit('bad-json', { event: name, raw: ev.data });
        return;
      }
    }
    // Any payload proves the connection is up, even if `open` was missed.
    if (this.state !== 'live') { this.errorTimes = []; this.setState('live'); }
    if (name === 'hello') this.hello = data;
    if (name === 'stats') this.stats = data;
    this.emit(name, data);
  }

  onError() {
    const now = Date.now();
    this.errorTimes = this.errorTimes.filter((t) => now - t < REFUSE_WINDOW_MS);
    this.errorTimes.push(now);
    if (this.errorTimes.length >= REFUSE_ERRORS) {
      this.stop(true);
      this.wanted = false;
      this.setState('refused', REFUSED_MESSAGE);
      return;
    }
    const closed = !this.es || this.es.readyState === 2; // 2 = CLOSED
    this.setState('reconnecting', closed ? 'connection refused, retrying…' : null);
    if (closed) {
      // The browser will not retry a fatal response (e.g. 503) — we do.
      this.dropSource();
      this.clearRetry();
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        if (this.wanted) this.open();
      }, RETRY_MS);
    }
  }

  dropSource() {
    if (!this.es) return;
    const es = this.es;
    this.es = null;
    es.onopen = null;
    es.onerror = null;
    try { es.close(); } catch { /* already gone */ }
  }

  clearRetry() {
    if (this.retryTimer !== null) { clearTimeout(this.retryTimer); this.retryTimer = null; }
  }

  /** Tear down the transport. `silent` keeps the current state text. */
  stop(silent = false) {
    this.clearRetry();
    this.dropSource();
    if (!silent) this.setState('disconnected');
  }

  /** Public close: the user (or an unmount) asked us to stop. */
  close() {
    this.wanted = false;
    this.errorTimes = [];
    this.stop(false);
  }
}

export default RawStream;
