// Boot: theme, header wiring, hash router, the poll loop.
//
// Views export mount(root, ctx) → unmount(). Exactly one view is mounted at a
// time and its unmount() is always called before the next mount, so timers,
// observers and store subscriptions never leak between routes.

import Api from './api.js';
import ClockSync from './clock.js';
import Store from './store.js';
import Scheduler from './refresh.js';
import Recorder from './recorder.js';
import { el, clear } from './components/tiles.js';
import * as F from './format.js';

const ROUTES = {
  '#/': () => import('./views/dashboard.js'),
  '#/detail': () => import('./views/detail.js'),
};

const store = new Store();
const clock = new ClockSync();
const api = new Api(clock);
const recorder = new Recorder(store.settings.recordCap);
const ctx = { api, store, clock, recorder, config: null };

let currentUnmount = null;
let currentRoute = null;
let scheduler = null;

// --------------------------------------------------------------------- theme

function applyTheme() {
  const t = store.settings.theme;
  if (t === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  const btn = document.getElementById('theme-btn');
  if (btn) {
    btn.textContent = t === 'auto' ? '◐' : t === 'dark' ? '☾' : '☀';
    btn.title = `Theme: ${t} (click to change)`;
    btn.setAttribute('aria-label', `Theme: ${t}. Click to change.`);
  }
}

function cycleTheme() {
  const order = ['auto', 'light', 'dark'];
  const next = order[(order.indexOf(store.settings.theme) + 1) % order.length];
  store.set({ theme: next });
  applyTheme();
}

// -------------------------------------------------------------------- header

function buildHeader() {
  const header = document.getElementById('app-header');
  clear(header);

  const bar = el('header.header-bar');

  bar.append(el('div.brand',
    el('h1', { text: 'StratumTap' }),
    el('span.host', { id: 'hostname', text: '' })));

  const nav = el('nav.nav', { 'aria-label': 'Views' },
    el('a', { href: '#/', id: 'nav-dash' }, 'Dashboard'),
    el('a', { href: '#/detail', id: 'nav-detail' }, 'Detail'));
  bar.append(nav);

  bar.append(el('span.header-spacer'));

  // --- always-visible controls
  const refreshSel = el('select.ctl', {
    id: 'refresh-select', 'aria-label': 'Refresh interval',
    onchange: (e) => {
      store.userSetRefresh = true;
      store.set({ refreshS: Number(e.target.value) });
      if (scheduler) scheduler.reschedule();
    },
  });

  const pauseBtn = el('button.btn.btn-icon', {
    type: 'button', id: 'pause-btn',
    'aria-pressed': String(store.settings.paused),
    onclick: () => {
      store.set({ paused: !store.settings.paused });
      updatePause();
      if (scheduler) scheduler.reschedule();
    },
  }, '⏸');

  const refreshBtn = el('button.btn.btn-icon', {
    type: 'button', id: 'refresh-btn', title: 'Refresh now', 'aria-label': 'Refresh now',
    onclick: () => scheduler && scheduler.refreshNow(),
  }, '↻');

  const meta = el('span.refresh-meta', { id: 'refresh-meta', text: '—' });

  const conn = el('div.conn', {
    id: 'conn', dataset: { state: 'init' },
    role: 'status', 'aria-live': 'polite',
  }, el('span.dot', { 'aria-hidden': 'true' }), el('span.txt', { text: 'connecting…' }));

  bar.append(el('div.controls', refreshSel, pauseBtn, refreshBtn, conn, meta));

  // --- secondary controls: inline on wide screens, in a popover on narrow ones
  const secondary = () => [
    el('label.switch', { title: 'Show server time corrected for measured network delay' },
      el('input', {
        type: 'checkbox', id: 'correction-cb', checked: store.settings.correction,
        onchange: (e) => store.set({ correction: e.target.checked }),
      }),
      'Correct for network delay'),
    el('span.field',
      el('label', { for: 'units-select', text: 'Units' }),
      el('select.ctl', {
        id: 'units-select',
        onchange: (e) => store.set({ units: e.target.value }),
      },
      ...['metric', 'imperial', 'nautical'].map((u) => el('option', {
        value: u, selected: store.settings.units === u,
      }, u)))),
    el('label.switch', { title: 'Stop polling while this tab is in the background' },
      el('input', {
        type: 'checkbox', id: 'hidden-cb', checked: store.settings.pauseWhenHidden,
        onchange: (e) => store.set({ pauseWhenHidden: e.target.checked }),
      }),
      'Pause when hidden'),
    el('button.btn.btn-icon', {
      type: 'button', id: 'theme-btn', onclick: cycleTheme,
    }, '◐'),
  ];

  const wide = el('div.controls.only-wide', ...secondary());
  const popover = el('details.settings.only-narrow',
    el('summary', { 'aria-label': 'Settings' }, '⚙'),
    el('div.popover', ...secondary()));
  // Two DOM copies of the same controls would fight over ids; the narrow copy
  // gets suffixed ids and both write to the same store, which re-renders both.
  for (const node of popover.querySelectorAll('[id]')) node.id += '-narrow';
  for (const node of popover.querySelectorAll('label[for]')) node.setAttribute('for', node.getAttribute('for') + '-narrow');

  bar.append(wide, popover);
  header.append(bar);

  // error banner slot lives under the bar so it never covers the nav
  header.append(el('div', { id: 'error-slot' }));

  populateRefreshChoices();
  updatePause();
  applyTheme();
}

function populateRefreshChoices() {
  const sel = document.getElementById('refresh-select');
  if (!sel) return;
  clear(sel);
  for (const s of store.refreshChoices()) {
    sel.append(el('option', { value: String(s), selected: store.settings.refreshS === s }, `${s} s`));
  }
  sel.append(el('option', { value: '0', selected: store.settings.refreshS === 0 }, 'Off'));
}

function updatePause() {
  const btn = document.getElementById('pause-btn');
  if (!btn) return;
  const p = store.settings.paused;
  btn.textContent = p ? '▶' : '⏸';
  btn.setAttribute('aria-pressed', String(p));
  btn.title = p ? 'Resume polling' : 'Pause polling';
  btn.setAttribute('aria-label', btn.title);
}

function syncSecondaryControls() {
  for (const suffix of ['', '-narrow']) {
    const c = document.getElementById('correction-cb' + suffix);
    if (c) c.checked = store.settings.correction;
    const u = document.getElementById('units-select' + suffix);
    if (u) u.value = store.settings.units;
    const h = document.getElementById('hidden-cb' + suffix);
    if (h) h.checked = store.settings.pauseWhenHidden;
  }
  const sel = document.getElementById('refresh-select');
  if (sel) sel.value = String(store.settings.refreshS);
}

const CONN_TEXT = {
  init: 'connecting…', ok: 'connected', degraded: 'degraded', failing: 'no data',
};

function renderConnection() {
  const conn = document.getElementById('conn');
  if (!conn) return;
  const state = store.connection;
  if (conn.dataset.state !== state) conn.dataset.state = state;
  const txt = conn.querySelector('.txt');
  const label = state === 'degraded' && store.lastError ? 'degraded' : CONN_TEXT[state] || state;
  if (txt.textContent !== label) txt.textContent = label;
}

function renderErrorBanner() {
  const slot = document.getElementById('error-slot');
  if (!slot) return;
  const failing = store.connection === 'failing' && store.lastError;
  const existing = slot.firstChild;
  if (!failing) { if (existing) clear(slot); return; }
  if (existing) {
    const msg = existing.querySelector('.grow');
    if (msg) msg.textContent = `Cannot reach the server: ${store.lastError}`;
    return;
  }
  slot.append(el('div.banner', { role: 'alert' },
    el('span.mark', { 'aria-hidden': 'true' }),
    el('span.grow', { text: `Cannot reach the server: ${store.lastError}` }),
    el('button.btn.btn-sm', { type: 'button', onclick: () => scheduler && scheduler.refreshNow() }, 'Retry')));
}

function renderRefreshMeta(sched) {
  const meta = document.getElementById('refresh-meta');
  if (!meta || !sched) return;
  const age = sched.ageS();
  const cd = sched.countdownS();
  const parts = [];
  if (sched.inFlight) parts.push('updating…');
  else if (age != null) parts.push(`updated ${age < 0.15 ? 'just now' : F.duration(age) + ' ago'}`);
  else parts.push('no data yet');
  if (store.settings.paused) parts.push('paused');
  else if (store.settings.refreshS === 0) parts.push('auto-refresh off');
  else if (cd != null && !sched.inFlight) parts.push(`next in ${cd.toFixed(1)} s`);
  const text = parts.join(' · ');
  if (meta.dataset.text !== text) {
    meta.dataset.text = text;
    clear(meta);
    meta.append(el('span', { text: parts[0] }));
    if (parts.length > 1) meta.append(el('span.cd', { text: ` · ${parts.slice(1).join(' · ')}` }));
  }
}

// --------------------------------------------------------------------- router

async function route() {
  const hash = location.hash || '#/';
  const key = ROUTES[hash] ? hash : '#/';
  if (key !== hash) { location.replace(key); return; }
  if (key === currentRoute) return;

  if (currentUnmount) {
    try { currentUnmount(); } catch (err) { console.error('unmount', err); }
    currentUnmount = null;
  }
  currentRoute = key;

  for (const a of document.querySelectorAll('.nav a')) {
    if (a.getAttribute('href') === key) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }

  const main = document.getElementById('view');
  clear(main);
  main.setAttribute('aria-busy', 'true');
  try {
    const mod = await ROUTES[key]();
    currentUnmount = mod.mount(main, ctx);
  } catch (err) {
    console.error('view failed', err);
    main.append(el('div.banner', el('span.mark'), el('span.grow', { text: `View failed to load: ${err.message || err}` })));
  } finally {
    main.removeAttribute('aria-busy');
  }
}

// ----------------------------------------------------------------- poll loop

async function poll({ signal } = {}) {
  const data = await api.getStatus({ signal });
  store.setStatus(data, Date.now());
  recorder.add(data, Date.now());
}

async function boot() {
  buildHeader();
  syncSecondaryControls();

  // Config first: it supplies the hostname, the tile URL and the refresh choices.
  try {
    const cfg = await api.getConfig();
    ctx.config = cfg;
    store.setConfig(cfg);
    populateRefreshChoices();
    const host = document.getElementById('hostname');
    if (host) {
      host.textContent = cfg.hostname || '';
      host.title = `${cfg.hostname || ''} · stratumtap ${cfg.version || ''}${cfg.demo ? ' · demo data' : ''}`;
    }
    document.title = `${cfg.hostname || 'stratumtap'} · StratumTap`;
  } catch (err) {
    console.warn('config unavailable', err);
  }

  scheduler = new Scheduler(poll, {
    store,
    onTick: (s) => renderRefreshMeta(s),
  });
  ctx.scheduler = scheduler;

  store.subscribe((_s, reason) => {
    renderConnection();
    renderErrorBanner();
    if (reason === 'settings') syncSecondaryControls();
  });

  window.addEventListener('hashchange', route);
  await route();
  scheduler.start();
}

window.addEventListener('beforeunload', () => {
  if (scheduler) scheduler.stop();
  if (currentUnmount) { try { currentUnmount(); } catch { /* leaving anyway */ } }
});

boot().catch((err) => {
  console.error('boot failed', err);
  const main = document.getElementById('view');
  if (main) {
    main.append(el('div.banner', el('span.mark'),
      el('span.grow', { text: `Startup failed: ${err.message || err}` })));
  }
});
