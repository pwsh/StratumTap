// Polling scheduler.
//
// A setTimeout chain, never setInterval: the next poll is only scheduled once the
// previous one has settled, so a slow server can never queue up overlapping
// requests. On failure the delay doubles (capped at 30 s) until the next success.

const MAX_BACKOFF_MS = 30000;

export class Scheduler {
  /**
   * @param {() => Promise<any>} task the poll itself; it should not throw for
   *        "degraded" payloads, only for transport failures.
   * @param {object} opts
   */
  constructor(task, { store, onTick } = {}) {
    this.task = task;
    this.store = store;
    this.onTick = onTick || (() => {});
    this.timer = null;
    this.tickTimer = null;
    this.inFlight = false;
    this.backoffMs = 0;
    this.nextAt = null;
    this.lastSuccessAt = null;
    this.abort = null;
    this.stopped = true;
    this.onVisibility = this.#visibility.bind(this);
  }

  get intervalMs() {
    const s = this.store ? this.store.settings.refreshS : 2;
    return s > 0 ? s * 1000 : 0;   // 0 = "Off"
  }

  get isPaused() {
    return !this.store || this.store.settings.paused || this.intervalMs === 0;
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    document.addEventListener('visibilitychange', this.onVisibility);
    // 5 Hz UI ticker for "updated N.N s ago" + countdown. Cheap and independent
    // of the poll rate so the readout stays live while paused.
    this.tickTimer = setInterval(() => this.onTick(this), 200);
    this.run();
  }

  stop() {
    this.stopped = true;
    document.removeEventListener('visibilitychange', this.onVisibility);
    clearTimeout(this.timer); this.timer = null;
    clearInterval(this.tickTimer); this.tickTimer = null;
    if (this.abort) { this.abort.abort(new Error('scheduler stopped')); this.abort = null; }
  }

  #visibility() {
    if (!this.store || !this.store.settings.pauseWhenHidden) return;
    if (document.hidden) {
      clearTimeout(this.timer);
      this.timer = null;
      this.nextAt = null;
    } else if (!this.isPaused) {
      this.run(); // catch up immediately on return
    }
  }

  /** Run the task now (also the manual "refresh" button). */
  async run() {
    if (this.stopped || this.inFlight) return;
    clearTimeout(this.timer);
    this.timer = null;
    this.inFlight = true;
    this.nextAt = null;
    this.abort = new AbortController();
    try {
      await this.task({ signal: this.abort.signal });
      this.backoffMs = 0;
      this.lastSuccessAt = Date.now();
    } catch (err) {
      if (!this.stopped) {
        // Double the wait each failure so a dead server is not hammered.
        this.backoffMs = this.backoffMs ? Math.min(this.backoffMs * 2, MAX_BACKOFF_MS)
          : Math.max(this.intervalMs || 1000, 1000);
        if (this.store) this.store.setError(err);
      }
    } finally {
      this.inFlight = false;
      this.abort = null;
      this.schedule();
      this.onTick(this);
    }
  }

  /** Manual refresh: clears the backoff so the user is not made to wait it out. */
  refreshNow() {
    this.backoffMs = 0;
    this.run();
  }

  schedule() {
    clearTimeout(this.timer);
    this.timer = null;
    if (this.stopped) return;
    if (this.store && this.store.settings.pauseWhenHidden && document.hidden) return;
    if (this.isPaused) return;          // paused/off: no retries either
    this.#arm(this.backoffMs || this.intervalMs);
  }

  #arm(ms) {
    this.nextAt = Date.now() + ms;
    if (this.store) this.store.nextPollAt = this.nextAt;
    this.timer = setTimeout(() => this.run(), ms);
  }

  /** Seconds since the last successful poll, or null. */
  ageS() {
    return this.lastSuccessAt == null ? null : (Date.now() - this.lastSuccessAt) / 1000;
  }

  /** Seconds until the next poll, or null when nothing is scheduled. */
  countdownS() {
    if (this.inFlight) return 0;
    return this.nextAt == null ? null : Math.max(0, (this.nextAt - Date.now()) / 1000);
  }

  /** Called after the user changes the interval / pause state. */
  reschedule() {
    if (this.inFlight) return;
    if (this.isPaused) { clearTimeout(this.timer); this.timer = null; this.nextAt = null; this.onTick(this); return; }
    this.schedule();
    this.onTick(this);
  }
}

export default Scheduler;
