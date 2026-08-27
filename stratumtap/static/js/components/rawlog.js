// "Live raw (streamed)" panel: an SSE-fed console of raw NMEA sentences, gpsd
// JSON objects and chrony tracking snapshots.
//
// Throughput rules that shape this file:
//   * the stream runs at 10-20 events/s, so nothing touches the DOM per event.
//     Events land in `pending`; one requestAnimationFrame callback renders the
//     whole batch. The rate/stat strips redraw on a slower 500 ms timer.
//   * memory is bounded: MAX_ENTRIES kept for export, MAX_ROWS rendered.
//   * the stream is only open while the user asks for it; unmount(), a hidden
//     tab (when "Pause when hidden" is on) and a filter change all close it.

import { el, clear, chip } from './tiles.js';
import { download } from '../recorder.js';
import RawStream from '../stream.js';
import * as F from '../format.js';

const MAX_ENTRIES = 2000;   // kept in memory (export + refilter)
const MAX_ROWS = 500;       // kept in the DOM
const RATE_WINDOW_MS = 10000;
const RATE_REDRAW_MS = 500;
const SNAPSHOT_N = 200;
const JSON_CLIP = 200;

const KINDS = ['nmea', 'gpsd', 'ntp'];
const KIND_LABEL = { nmea: 'NMEA', gpsd: 'gpsd JSON', ntp: 'ntp' };
const BADGE = { nmea: 'NMEA', gpsd: 'JSON', ntp: 'NTP' };
/** Sentences a GPS/NTP operator looks for first; everything else follows, sorted. */
const TYPE_ORDER = ['RMC', 'GGA', 'GSA', 'GSV', 'VTG', 'GLL', 'ZDA', 'GST', 'TXT'];

const STATE_TEXT = {
  disconnected: 'disconnected', connecting: 'connecting', live: 'live',
  reconnecting: 'reconnecting', refused: 'refused',
};

function clip(s, n) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function nowUnix() { return Date.now() / 1000; }

/** Normalize a server event into the one shape the log renders. */
export function toEntry(kind, data) {
  const d = data && typeof data === 'object' ? data : {};
  if (kind === 'nmea') {
    const line = typeof d.line === 'string' ? d.line : '';
    const type = typeof d.type === 'string' ? d.type : '';
    const bad = d.checksum_ok === false;
    return {
      kind, raw: d, type, bad,
      t: F.isNum(d.t) ? d.t : nowUnix(),
      text: line,
      tag: null,
      title: bad ? `checksum mismatch — ${line}` : null,
      search: `${type} ${line}`.toLowerCase(),
    };
  }
  if (kind === 'gpsd') {
    let json = '{}';
    try { json = JSON.stringify(d); } catch { json = String(d); }
    const cls = typeof d.class === 'string' ? d.class : '?';
    return {
      kind, raw: d, type: cls, bad: false,
      t: F.isNum(d._t) ? d._t : nowUnix(),
      text: clip(json, JSON_CLIP),
      tag: cls,
      title: json,
      search: json.toLowerCase(),
    };
  }
  // ntp
  const parts = [`system offset ${F.siSeconds(d.system_offset_s, { sign: true })}`];
  if (F.isNum(d.stratum)) parts.push(`stratum ${d.stratum}`);
  if (F.isNum(d.last_offset_s)) parts.push(`last ${F.siSeconds(d.last_offset_s, { sign: true })}`);
  let json = '';
  try { json = JSON.stringify(d); } catch { /* circular — no title */ }
  const text = parts.join(' · ');
  return {
    kind: 'ntp', raw: d, type: 'tracking', bad: false,
    t: F.isNum(d.collected_at) ? d.collected_at : (F.isNum(d.t) ? d.t : nowUnix()),
    text,
    tag: null,
    title: json || null,
    search: `ntp ${text} ${json}`.toLowerCase(),
  };
}

/** Order the rate chips: the interesting sentences first, then alphabetical. */
export function orderTypes(types) {
  return [...types].sort((a, b) => {
    const ia = TYPE_ORDER.indexOf(a);
    const ib = TYPE_ORDER.indexOf(b);
    if (ia !== ib) return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    return a < b ? -1 : a > b ? 1 : 0;
  });
}

export function mount(root, ctx) {
  const { store, api } = ctx;
  const saved = { ...(store.settings.rawStream || {}) };
  const sel = {
    nmea: saved.nmea !== false,
    gpsd: saved.gpsd !== false,
    ntp: saved.ntp !== false,
  };
  let filter = typeof saved.filter === 'string' ? saved.filter : '';
  let autoscroll = saved.autoscroll !== false;
  let userOn = saved.on === true;
  let paused = false;
  let hiddenPaused = false;

  // ------------------------------------------------------------ data state
  let entries = [];         // newest last, capped at MAX_ENTRIES
  let pending = [];         // waiting for the next animation frame
  let counts = new Map();   // NMEA type -> total seen
  let recent = [];          // { ms, type } within RATE_WINDOW_MS
  let checksumErrors = 0;
  let frame = 0;
  let lastRateDraw = 0;

  const stream = new RawStream({ events: activeEvents() });

  // --------------------------------------------------------------- markup
  const connectBtn = el('button.btn', { type: 'button', onclick: () => setOn(!userOn) }, 'Connect');
  const pauseBtn = el('button.btn', {
    type: 'button', title: 'Keep buffering, stop drawing', onclick: () => setPaused(!paused),
  }, 'Pause');
  const clearBtn = el('button.btn', { type: 'button', onclick: () => { resetData(); rerender(); renderRates(true); } }, 'Clear');

  const sourceRow = el('span.rowline.rl-sources');
  for (const k of KINDS) {
    const cb = el('input', {
      type: 'checkbox', id: `rl-src-${k}`, checked: sel[k],
      onchange: (e) => {
        sel[k] = e.target.checked;
        persist();
        const evs = activeEvents();
        stream.setEvents(evs);              // reconnects in place while streaming
        if (!evs.length) {                  // nothing left to ask for
          stream.close();
          stream.setState('disconnected', 'no event sources selected');
        }
        else if (userOn && !hiddenPaused && !stream.wanted && stream.state !== 'refused') stream.connect();
        rerender();
        renderStatus();
      },
    });
    sourceRow.append(el('label.switch', { for: `rl-src-${k}` }, cb, KIND_LABEL[k]));
  }

  const filterInput = el('input.ctl', {
    type: 'search', id: 'rl-filter', value: filter, placeholder: 'GGA, $GP, TPV…',
    'aria-label': 'Filter lines (sentence type or any substring)',
    oninput: (e) => {
      filter = e.target.value;
      persistLater();
      rerender();
    },
  });

  const autoCb = el('input', {
    type: 'checkbox', id: 'rl-autoscroll', checked: autoscroll,
    onchange: (e) => { autoscroll = e.target.checked; persist(); if (autoscroll) scrollToEnd(); },
  });

  const statePill = el('div.rl-pill', {
    dataset: { state: 'disconnected' }, role: 'status', 'aria-live': 'polite',
  }, el('span.mark', { 'aria-hidden': 'true' }), el('span.txt', { text: 'disconnected' }));

  const statChips = el('span.chips.rl-stats');
  const retryBtn = el('button.btn.btn-sm', {
    type: 'button', onclick: () => { userOn = true; persist(); stream.connect(); renderControls(); },
  }, 'Retry');
  const noteEl = el('span.muted.rl-note');

  const rateStrip = el('div.chips.rl-rates');

  const logEl = el('div.rl-log.mono', {
    role: 'log', tabindex: '0',
    'aria-label': 'Live raw stream (server receive time, UTC)',
  });
  const emptyEl = el('div.rl-empty.muted', { text: 'Not connected. Press Connect to open the stream, or take a snapshot.' });
  logEl.append(emptyEl);

  const saveNmeaBtn = el('button.btn.btn-sm', { type: 'button', onclick: saveNmea }, 'Save .nmea');
  const saveJsonlBtn = el('button.btn.btn-sm', { type: 'button', onclick: saveJsonl }, 'Save .jsonl');
  const saveCounts = el('span.muted.rl-counts');
  const snapBtn = el('button.btn.btn-sm', {
    type: 'button', title: `GET /api/v1/raw/nmea?n=${SNAPSHOT_N} — works without opening the stream`,
    onclick: snapshot,
  }, `Snapshot last ${SNAPSHOT_N} (poll)`);

  clear(root);
  root.classList.add('rawlog');
  root.append(
    el('div.rowline.rl-controls',
      connectBtn, pauseBtn, clearBtn,
      el('span.rl-sep', { 'aria-hidden': 'true' }),
      sourceRow,
      el('span.field', el('label', { for: 'rl-filter', text: 'Filter' }), filterInput),
      el('label.switch', { for: 'rl-autoscroll' }, autoCb, 'Auto-scroll')),
    el('div.rowline.rl-status', statePill, statChips, retryBtn, noteEl),
    rateStrip,
    logEl,
    el('div.rowline.rl-save', saveNmeaBtn, saveJsonlBtn, saveCounts,
      el('span.grow'), snapBtn),
  );

  // ------------------------------------------------------------- helpers
  function activeEvents() { return KINDS.filter((k) => sel[k]); }

  /**
   * `?rawstream=1` (search or hash) auto-connects on mount. Test-only hook so a
   * headless screenshot of #/detail captures a running stream; nothing in the
   * app ever sets it.
   */
  function wantsAutoConnect() {
    try {
      if (new URLSearchParams(location.search).get('rawstream') === '1') return true;
      return /[?&]rawstream=1\b/.test(location.hash);
    } catch {
      return false;
    }
  }

  let persistTimer = 0;
  function persist() {
    if (persistTimer) { clearTimeout(persistTimer); persistTimer = 0; }
    store.set({ rawStream: { on: userOn, ...sel, filter, autoscroll } });
  }
  function persistLater() {
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = setTimeout(() => { persistTimer = 0; persist(); }, 400);
  }

  function matches(e) {
    if (!sel[e.kind]) return false;
    if (!filter) return true;
    return e.search.includes(filter.toLowerCase());
  }

  function resetData() {
    entries = [];
    pending = [];
    counts = new Map();
    recent = [];
    checksumErrors = 0;
  }

  // --------------------------------------------------------- event intake
  function addEntry(e, live = true) {
    entries.push(e);
    if (entries.length > MAX_ENTRIES) entries.splice(0, entries.length - MAX_ENTRIES);
    if (e.kind === 'nmea' && e.type) counts.set(e.type, (counts.get(e.type) || 0) + 1);
    if (e.bad) checksumErrors += 1;
    if (live) {
      recent.push({ ms: Date.now(), type: e.kind === 'nmea' ? e.type : null });
      pending.push(e);
      if (pending.length > MAX_ENTRIES) pending.splice(0, pending.length - MAX_ENTRIES);
      schedule();
    }
  }

  function onServerEvent(kind, data) {
    if (!data) return;
    addEntry(toEntry(kind, data));
  }

  // ------------------------------------------------------------ rendering
  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(flush);
  }

  function flush() {
    frame = 0;
    // Paused: leave `pending` alone — resuming replays the whole buffer.
    if (paused || !pending.length) return;
    const frag = document.createDocumentFragment();
    let added = 0;
    for (const e of pending) {
      if (!matches(e)) continue;
      frag.append(rowFor(e));
      added += 1;
    }
    pending.length = 0;
    if (added) {
      if (emptyEl.isConnected) emptyEl.remove();
      logEl.append(frag);
      trimRows();
      if (autoscroll) scrollToEnd();
    }
  }

  function rowFor(e) {
    return el(`div.rl-row${e.bad ? '.is-bad' : ''}`, { dataset: { kind: e.kind }, title: e.title },
      el('span.rl-t', { text: F.utcClock(e.t * 1000) }),
      el('span.rl-b', { text: BADGE[e.kind] }),
      e.tag ? el('span.rl-tag', { text: e.tag }) : null,
      el('span.rl-l', { text: e.text }));
  }

  function trimRows() {
    while (logEl.childElementCount > MAX_ROWS) logEl.removeChild(logEl.firstElementChild);
  }

  function scrollToEnd() { logEl.scrollTop = logEl.scrollHeight; }

  /** Full redraw from `entries` — filter changes, Clear, snapshots. */
  function rerender() {
    pending.length = 0;
    clear(logEl);
    const shown = entries.filter(matches).slice(-MAX_ROWS);
    if (!shown.length) {
      logEl.append(entries.length
        ? el('div.rl-empty.muted', { text: 'No lines match the current filter.' })
        : emptyEl);
    } else {
      const frag = document.createDocumentFragment();
      for (const e of shown) frag.append(rowFor(e));
      logEl.append(frag);
      scrollToEnd();
    }
    renderCounts();
  }

  function renderControls() {
    const on = userOn && stream.state !== 'refused';
    connectBtn.textContent = on ? 'Disconnect' : 'Connect';
    connectBtn.classList.toggle('is-on', on);
    connectBtn.setAttribute('aria-pressed', String(on));
    pauseBtn.textContent = paused ? 'Resume' : 'Pause';
    pauseBtn.classList.toggle('is-on', paused);
    pauseBtn.setAttribute('aria-pressed', String(paused));
    retryBtn.style.display = stream.state === 'refused' ? '' : 'none';
  }

  function renderStatus() {
    const st = stream.state;
    statePill.dataset.state = st;
    const txt = statePill.querySelector('.txt');
    const label = STATE_TEXT[st] || st;
    if (txt.textContent !== label) txt.textContent = label;

    clear(statChips);
    const s = stream.stats;
    const sent = s && F.isNum(s.sent) ? s.sent : null;
    const dropped = s && F.isNum(s.dropped) ? s.dropped : null;
    const chips = [
      chip('sent', sent == null ? F.DASH : String(sent), { title: 'Events this server has sent us' }),
      chip('dropped', dropped == null ? F.DASH : String(dropped), { title: 'Events dropped because our queue was full' }),
      chip('queue', s && F.isNum(s.queue_len) ? String(s.queue_len) : F.DASH,
        { title: stream.hello && F.isNum(stream.hello.queue) ? `of ${stream.hello.queue}` : 'server-side queue depth' }),
      chip('clients', s && F.isNum(s.clients) ? String(s.clients) : F.DASH, { title: 'Stream subscribers on the server' }),
    ];
    if (dropped) chips[1].classList.add('is-warn');
    for (const c of chips) statChips.append(c);

    const bits = [];
    if (stream.message) bits.push(stream.message);
    else if (stream.hello && F.isNum(stream.hello.client_id)) bits.push(`client #${stream.hello.client_id}`);
    if (paused) bits.push(`paused · ${pending.length} buffered`);
    noteEl.textContent = bits.join(' · ');
    renderControls();
  }

  function renderRates(force = false) {
    const now = Date.now();
    if (!force && now - lastRateDraw < RATE_REDRAW_MS) return;
    lastRateDraw = now;
    while (recent.length && now - recent[0].ms > RATE_WINDOW_MS) recent.shift();
    const perType = new Map();
    for (const r of recent) {
      if (r.type) perType.set(r.type, (perType.get(r.type) || 0) + 1);
    }
    const win = RATE_WINDOW_MS / 1000;
    clear(rateStrip);
    for (const type of orderTypes(counts.keys())) {
      const rate = (perType.get(type) || 0) / win;
      rateStrip.append(chip(type, `${counts.get(type)} · ${rate.toFixed(1)}/s`,
        { title: `${type}: ${counts.get(type)} seen, ${rate.toFixed(2)} lines/s over the last ${win} s` }));
    }
    if (!counts.size) rateStrip.append(el('span.muted', { style: { fontSize: '11.5px' }, text: 'No NMEA seen yet.' }));
    const total = chip('total', `${(recent.length / win).toFixed(1)}/s`,
      { title: `All stream events over the last ${win} s` });
    total.classList.add('is-total');
    rateStrip.append(total);
    const errs = chip('checksum err', String(checksumErrors),
      { title: 'NMEA sentences whose checksum did not verify' });
    if (checksumErrors > 0) errs.classList.add('is-bad');
    rateStrip.append(errs);
    if (paused) noteEl.textContent = `paused · ${pending.length} buffered`;
    renderCounts();
  }

  function renderCounts() {
    const n = entries.filter((e) => e.kind === 'nmea').length;
    saveNmeaBtn.disabled = n === 0;
    saveJsonlBtn.disabled = entries.length === 0;
    saveCounts.textContent = `${n} NMEA · ${entries.length} events kept (max ${MAX_ENTRIES})`;
  }

  // -------------------------------------------------------------- actions
  /** `remember` is false for the test-only ?rawstream=1 auto-connect. */
  function setOn(on, remember = true) {
    userOn = on;
    if (remember) persist();
    if (on) {
      hiddenPaused = false;
      stream.setEvents(activeEvents());
      stream.connect();
    } else {
      stream.close();
    }
    renderControls();
  }

  function setPaused(p) {
    paused = p;
    if (!paused) schedule();
    renderStatus();
  }

  function stamp() {
    return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  }
  function hostname() {
    return (store.config && store.config.hostname) || 'stratumtap';
  }

  function saveNmea() {
    const lines = entries.filter((e) => e.kind === 'nmea' && typeof e.raw.line === 'string')
      .map((e) => e.raw.line);
    if (!lines.length) return;
    // The standard NMEA log format: bare sentences, CRLF terminated.
    download(`${hostname()}-${stamp()}.nmea`, `${lines.join('\r\n')}\r\n`, 'text/plain');
  }

  function saveJsonl() {
    if (!entries.length) return;
    const text = entries.map((e) => JSON.stringify({ event: e.kind, ...e.raw })).join('\n');
    download(`${hostname()}-${stamp()}.jsonl`, `${text}\n`, 'application/x-ndjson');
  }

  async function snapshot() {
    snapBtn.disabled = true;
    noteEl.textContent = 'fetching snapshot…';
    try {
      const data = await api.getRawNmea(SNAPSHOT_N);
      const lines = Array.isArray(data && data.lines) ? data.lines : [];
      resetData();
      for (const ln of lines) addEntry(toEntry('nmea', ln), false);
      rerender();
      renderRates(true);
      noteEl.textContent = `snapshot: ${lines.length} lines`
        + (F.isNum(data.ring_size) ? ` of a ${data.ring_size}-line ring` : '');
    } catch (err) {
      noteEl.textContent = `snapshot failed: ${(err && err.message) || err}`;
    } finally {
      snapBtn.disabled = false;
    }
  }

  // ------------------------------------------------------------ lifecycle
  const offs = [
    stream.on('hello', () => renderStatus()),
    stream.on('stats', () => renderStatus()),
    stream.on('state', () => renderStatus()),
    stream.on('nmea', (d) => onServerEvent('nmea', d)),
    stream.on('gpsd', (d) => onServerEvent('gpsd', d)),
    stream.on('ntp', (d) => onServerEvent('ntp', d)),
  ];

  const rateTimer = setInterval(() => renderRates(), RATE_REDRAW_MS);

  function onVisibility() {
    if (!userOn) return;
    if (document.hidden && store.settings.pauseWhenHidden) {
      if (stream.active || stream.wanted) {
        hiddenPaused = true;
        stream.close();
        stream.setState('disconnected', 'paused while the tab is hidden');
      }
    } else if (hiddenPaused && !document.hidden) {
      hiddenPaused = false;
      stream.connect();
    }
  }
  document.addEventListener('visibilitychange', onVisibility);

  renderControls();
  renderStatus();
  renderRates(true);
  // Reconnect only because the user left it connected, or because the test flag
  // asked for it — never just because the panel was mounted.
  if (userOn) setOn(true);
  else if (wantsAutoConnect()) setOn(true, false);

  return function unmount() {
    clearInterval(rateTimer);
    if (persistTimer) { clearTimeout(persistTimer); persistTimer = 0; }
    if (frame) { cancelAnimationFrame(frame); frame = 0; }
    document.removeEventListener('visibilitychange', onVisibility);
    for (const off of offs) off();
    stream.close();
    resetData();
    root.classList.remove('rawlog');
    clear(root);
  };
}

export default { mount };
