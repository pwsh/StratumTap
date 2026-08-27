// Thin fetch wrappers for /api/v1/*.
//
// Every call runs the 4-timestamp exchange: we stamp t0 immediately before
// fetch() and t3 immediately after the promise resolves — deliberately BEFORE
// .json(), so body parsing time is not charged to the network delay. (Capturing
// t3 when the headers land is the ideal, but the Fetch API gives us no such hook;
// `await fetch()` resolving is the closest available moment and for these small
// bodies the difference is well under the measurement noise.)

const BASE = '/api/v1';

export class ApiError extends Error {
  constructor(message, status, url) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
  }
}

export class Api {
  /** @param {import('./clock.js').ClockSync} [clock] fed with every exchange */
  constructor(clock = null) {
    this.clock = clock;
    this.timeoutMs = 8000;
  }

  /** Build a URL with the caller's params plus the ?t0= echo. */
  #url(path, params, t0) {
    const u = new URL(BASE + path, location.origin);
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== undefined && v !== null) u.searchParams.set(k, String(v));
    }
    // t0 in Unix seconds (float), the same scale as server.t_recv/t_send.
    u.searchParams.set('t0', (t0 / 1000).toFixed(3));
    return u;
  }

  async #request(path, { params, as = 'json', signal } = {}) {
    const t0 = Date.now();
    const url = this.#url(path, params, t0);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(new Error('timeout')), this.timeoutMs);
    const onAbort = () => ctrl.abort(signal.reason);
    if (signal) {
      if (signal.aborted) { clearTimeout(timer); throw signal.reason || new Error('aborted'); }
      signal.addEventListener('abort', onAbort, { once: true });
    }
    let res;
    try {
      res = await fetch(url, { signal: ctrl.signal, credentials: 'same-origin' });
    } catch (err) {
      throw new ApiError(err && err.name === 'AbortError' ? 'request timed out' : 'network error', 0, url.pathname);
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener('abort', onAbort);
    }
    const t3 = Date.now();
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body && body.detail) detail = body.detail;
      } catch { /* not JSON */ }
      throw new ApiError(detail, res.status, url.pathname);
    }
    if (as === 'text') return res.text();
    const data = await res.json();
    this.#feedClock(data, t0, t3);
    return data;
  }

  #feedClock(data, t0, t3) {
    if (!this.clock || !data || typeof data !== 'object') return;
    const s = data.server;
    if (!s) return;
    this.clock.addSample(t0, s.t_recv, s.t_send, t3);
  }

  getTime(opts) { return this.#request('/time', opts); }
  getStatus(opts) { return this.#request('/status', opts); }
  getNtp(opts) { return this.#request('/ntp', opts); }
  getGps(opts) { return this.#request('/gps', opts); }
  getSatellites(opts) { return this.#request('/gps/satellites', opts); }
  getSources(opts) { return this.#request('/ntp/sources', opts); }
  getConfig(opts) { return this.#request('/config', opts); }
  getHealth(opts) { return this.#request('/health', opts); }

  /** History rows; `max` should be about the pixel width of the chart. */
  getHistory(seconds = 3600, max = 720, opts = {}) {
    return this.#request('/history', { ...opts, params: { seconds, max } });
  }

  getRawTracking(opts) { return this.#request('/raw/chronyc/tracking', { ...opts, as: 'text' }); }
  getRawSources(opts) { return this.#request('/raw/chronyc/sources', { ...opts, as: 'text' }); }
  getRawSourcestats(opts) { return this.#request('/raw/chronyc/sourcestats', { ...opts, as: 'text' }); }
  getRawGpsd(opts) { return this.#request('/raw/gpsd', opts); }

  /**
   * Last `n` raw NMEA lines from the server-side ring buffer (newest last).
   * A plain poll, so it works even when the SSE client cap has been reached.
   */
  getRawNmea(n = 200, opts = {}) {
    return this.#request('/raw/nmea', { ...opts, params: { n } });
  }

  /** URL for the "download the server's 24 h history" link. */
  historyCsvUrl(seconds = 86400, max = 17280) {
    return `${BASE}/history?seconds=${seconds}&max=${max}&format=csv`;
  }
}

export default Api;
