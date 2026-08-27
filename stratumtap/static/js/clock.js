// NTP-style 4-timestamp clock synchronization against the API server.
//
//   t0  browser sends the request       (Date.now() just before fetch)
//   t1  server receives it              (server.t_recv, Unix seconds)
//   t2  server sends the response       (server.t_send, Unix seconds)
//   t3  browser receives it             (Date.now() just after fetch resolves)
//
//   round-trip delay = (t3 − t0) − (t2 − t1)      // network only, server work removed
//   offset (server − browser) = ((t1 − t0) + (t2 − t3)) / 2
//
// Both formulas assume the path is symmetric; asymmetry shows up as offset error
// of up to ±delay/2, which is why we display the delay next to the offset.
//
// The estimator keeps a small ring of samples and reports the one with the lowest
// delay (the NTP "clock filter" — the least-delayed sample is the least distorted
// by queueing). No DOM access here.

const RING = 8;

export class ClockSync {
  constructor(size = RING) {
    this.size = size;
    /** @type {{offset_ms:number, delay_ms:number, t:number}[]} */
    this.samples = [];
    this.best = null;
    /** last raw exchange, for the "as received" (uncorrected) display */
    this.lastReceived = null; // { t2, t3 }
  }

  /**
   * Feed one exchange. t0/t3 are browser epoch ms; t1/t2 are server Unix seconds.
   * Returns the sample, or null if the server timestamps were unusable.
   */
  addSample(t0, t1, t2, t3) {
    if (![t0, t1, t2, t3].every((v) => typeof v === 'number' && Number.isFinite(v))) return null;
    const t1ms = t1 * 1000;
    const t2ms = t2 * 1000;
    if (t2ms < t1ms) return null;             // server clock went backwards mid-request
    let delay = (t3 - t0) - (t2ms - t1ms);
    // Date.now() is integer ms while the server stamps are sub-ms floats, so on a
    // LAN the delay can compute a hair negative; treat small negatives as zero and
    // only reject genuinely nonsensical exchanges (clock jump, huge stall).
    if (delay < -5 || delay > 60000) return null;
    delay = Math.max(0, delay);
    const offset = ((t1ms - t0) + (t2ms - t3)) / 2;
    const s = { offset_ms: offset, delay_ms: delay, t: t3 };
    this.samples.push(s);
    if (this.samples.length > this.size) this.samples.shift();
    this.lastReceived = { t2, t3 };
    this.#refreshBest();
    return s;
  }

  #refreshBest() {
    let best = null;
    for (const s of this.samples) if (!best || s.delay_ms < best.delay_ms) best = s;
    this.best = best;
  }

  /** Server − browser, in ms. null until the first usable sample. */
  offsetMs() { return this.best ? this.best.offset_ms : null; }

  /** Round-trip delay of the best sample, in ms. */
  delayMs() { return this.best ? this.best.delay_ms : null; }

  sampleCount() { return this.samples.length; }

  /** Spread of the offsets in the ring — a rough confidence indicator (ms). */
  jitterMs() {
    if (this.samples.length < 2) return null;
    let lo = Infinity, hi = -Infinity;
    for (const s of this.samples) {
      if (s.offset_ms < lo) lo = s.offset_ms;
      if (s.offset_ms > hi) hi = s.offset_ms;
    }
    return hi - lo;
  }

  /** Best estimate of server wall clock now, in epoch ms. */
  serverNow() {
    return Date.now() + (this.best ? this.best.offset_ms : 0);
  }

  /**
   * The timestamp exactly as it was received, ticking forward on the local clock.
   * No delay correction: this is what a naive "server said X" display shows, and
   * it lags real server time by roughly the one-way return delay.
   */
  uncorrectedNow() {
    if (!this.lastReceived) return Date.now();
    const { t2, t3 } = this.lastReceived;
    return t2 * 1000 + (Date.now() - t3);
  }

  /** serverNow() or uncorrectedNow() depending on the user's toggle. */
  now(corrected = true) {
    return corrected ? this.serverNow() : this.uncorrectedNow();
  }

  /** True once we have any usable estimate. */
  get ready() { return this.best !== null; }

  reset() {
    this.samples.length = 0;
    this.best = null;
    this.lastReceived = null;
  }
}

export default ClockSync;
