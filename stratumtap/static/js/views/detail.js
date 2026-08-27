// Detail (#/detail): map, sky plot, satellite table, accuracy, time gauge,
// history charts, NTP sources, raw output, recording & export.

import { el, clear, card, tile, chip, banner, offsetLevel } from '../components/tiles.js';
import MapPanel from '../components/map.js';
import SkyPlot from '../components/skyplot.js';
import SatTable from '../components/sattable.js';
import SourcesPanel from '../components/sources.js';
import Gauge from '../components/gauge.js';
import HistoryPanels from '../components/history.js';
import RawLog from '../components/rawlog.js';
import * as F from '../format.js';

const SOURCES_MIN_INTERVAL_MS = 10000;   // never poll chronyc sources faster than this

export function mount(root, ctx) {
  const { store, api, recorder } = ctx;
  clear(root);
  const disposers = [];

  const grid = el('div.grid');
  root.append(grid);

  // ------------------------------------------------------------------ map
  const mapCard = card('Position', { cls: 'span-2' });
  const mapPanel = new MapPanel(mapCard.body.appendChild(el('div')), ctx);
  disposers.push(() => mapPanel.destroy());
  grid.append(mapCard.root);

  // -------------------------------------------------------------- gauge
  const gaugeCard = card('Time accuracy', { cls: '' });
  const gaugeHolder = el('div');
  const gaugeSecondary = el('div.tiles');
  gaugeCard.body.append(gaugeHolder, gaugeSecondary);
  const gauge = new Gauge(gaugeHolder, { label: 'System clock offset' });
  disposers.push(() => gauge.destroy());
  grid.append(gaugeCard.root);

  // ------------------------------------------------------------- sky plot
  const skyCard = card('Sky plot', { cls: '' });
  const skyHolder = el('div');
  skyCard.body.append(skyHolder);
  const sky = new SkyPlot(skyHolder);
  disposers.push(() => sky.destroy());
  grid.append(skyCard.root);

  // -------------------------------------------------------- satellite table
  const satCard = card('Satellites in view', { cls: 'span-2' });
  const satHolder = el('div');
  satCard.body.append(satHolder);
  const satTable = new SatTable(satHolder);
  disposers.push(() => satTable.destroy());
  grid.append(satCard.root);

  // ------------------------------------------------------------- accuracy
  const accCard = card('Accuracy', { cls: 'span-2' });
  const dopRow = el('div.chips');
  const errTiles = el('div.tiles');
  const gstSlot = el('div');
  accCard.body.append(
    el('div.muted', { style: { fontSize: '11.5px' }, text: 'Dilution of precision (lower is better)' }),
    dopRow,
    el('div.muted', { style: { fontSize: '11.5px', marginTop: '4px' }, text: 'Error estimates (1σ)' }),
    errTiles,
    gstSlot);
  grid.append(accCard.root);

  // -------------------------------------------------------------- history
  const histCard = card('History', { cls: 'span-all' });
  const histHolder = el('div');
  histCard.body.append(histHolder);
  const history = new HistoryPanels(histHolder, ctx);
  disposers.push(() => history.destroy());
  grid.append(histCard.root);

  // -------------------------------------------------------------- sources
  const srcCard = card('NTP sources', { cls: 'span-all' });
  const srcHolder = el('div');
  srcCard.body.append(srcHolder);
  const sources = new SourcesPanel(srcHolder);
  disposers.push(() => sources.destroy());
  grid.append(srcCard.root);

  // ------------------------------------------------------------- recording
  const recCard = card('Recording & export', { cls: '' });
  const recStats = el('div.tiles');
  const recBtn = el('button.btn', { type: 'button', onclick: () => { recorder.toggle(); } }, 'Start recording');
  const clearBtn = el('button.btn', { type: 'button', onclick: () => recorder.clear() }, 'Clear');
  const capInput = el('input.ctl', {
    type: 'number', min: '100', step: '100', value: String(store.settings.recordCap),
    id: 'rec-cap', 'aria-label': 'Maximum samples kept',
    onchange: (e) => {
      const v = Number(e.target.value);
      store.set({ recordCap: v });
      recorder.setCap(v);
      e.target.value = String(recorder.cap);
    },
  });
  const exportBtns = el('div.rowline');
  for (const [kind, label] of [['json', 'JSON'], ['csv', 'CSV'], ['gpx', 'GPX'], ['geojson', 'GeoJSON']]) {
    exportBtns.append(el('button.btn.btn-sm', {
      type: 'button',
      onclick: () => {
        if (!recorder.count) return;
        recorder.export(kind, (store.config && store.config.hostname) || 'stratumtap');
      },
    }, label));
  }
  recCard.body.append(
    el('div.rowline', recBtn, clearBtn,
      el('span.field', el('label', { for: 'rec-cap', text: 'Cap' }), capInput)),
    recStats,
    el('div.muted', { style: { fontSize: '11.5px' }, text: 'Export this browser session' }),
    exportBtns,
    el('div', el('a', {
      href: api.historyCsvUrl(86400, 17280),
      download: '',
    }, 'Server history CSV (24 h) ↓')));
  grid.append(recCard.root);

  // ------------------------------------------------------------- live raw
  // Full width, right under "Recording & export": the SSE console. It mounts
  // disconnected; nothing hits /api/v1/stream until the user presses Connect.
  const liveCard = card('Live raw (streamed)', { cls: 'span-all' });
  const liveHolder = el('div');
  liveCard.body.append(liveHolder);
  const unmountLive = RawLog.mount(liveHolder, ctx);
  disposers.push(unmountLive);
  grid.append(liveCard.root);

  // ------------------------------------------------------------ raw output
  const rawCard = card('Raw output', { cls: 'span-all' });
  const rawPanels = [
    { id: 'tracking', label: 'chronyc tracking', fetch: (o) => api.getRawTracking(o), json: false },
    { id: 'sources', label: 'chronyc sources -v', fetch: (o) => api.getRawSources(o), json: false },
    { id: 'sourcestats', label: 'chronyc sourcestats -v', fetch: (o) => api.getRawSourcestats(o), json: false },
    { id: 'gpsd', label: 'gpsd last messages', fetch: (o) => api.getRawGpsd(o), json: true },
  ];
  for (const p of rawPanels) {
    const pre = el('pre.raw', { text: 'loading…' });
    const det = el('details.raw-panel', el('summary', { text: p.label }), pre);
    p.pre = pre;
    p.det = det;
    // Lazily fetched: nothing is requested until the panel is actually opened.
    det.addEventListener('toggle', () => { if (det.open) loadRaw(p); });
    rawCard.body.append(det);
  }
  grid.append(rawCard.root);

  async function loadRaw(p) {
    try {
      const data = await p.fetch();
      p.pre.textContent = p.json ? JSON.stringify(data, null, 2) : String(data);
    } catch (err) {
      p.pre.textContent = `unavailable: ${err.message || err}`;
    }
  }

  // ------------------------------------------------------------- rendering
  function renderGauge(status) {
    const n = (status && status.ntp) || {};
    const g = (status && status.gps) || {};
    const to = g.time_offset || {};
    gauge.update(n.available === false ? null : n.system_offset_s, {
      lastOffset: n.last_offset_s,
      rms: n.rms_offset_s,
      secondary: { label: to.source ? `GPS→system (${to.source})` : 'GPS→system', value: to.offset_s },
    });
    clear(gaugeSecondary);
    gaugeSecondary.append(
      tile('GPS→system offset', F.siSeconds(to.offset_s, { sign: true }),
        { sub: to.source ? `from ${to.source}` : 'no PPS/TOFF seen',
          title: 'System clock minus GPS time (gpsd PPS/TOFF)' }),
      tile('PPS offset', F.siSeconds(to.pps_offset_s, { sign: true })),
      tile('TOFF offset', F.siSeconds(to.toff_offset_s, { sign: true })),
      tile('Fix age (cgps)',
        F.isNum(g.fix && g.fix.time_age_s) ? F.siSeconds(g.fix.time_age_s, { sign: true }) : F.DASH,
        { sub: g.cgps_time_offset_text || g.raw_time_offset_text || null }),
      tile('Last offset', F.siSeconds(n.last_offset_s, { sign: true })),
      tile('RMS offset', F.siSeconds(n.rms_offset_s)),
    );
  }

  function renderAccuracy(status) {
    const g = (status && status.gps) || {};
    const units = store.settings.units;
    clear(dopRow); clear(errTiles);
    const d = g.dop || {};
    for (const [k, label] of [['xdop', 'XDOP'], ['ydop', 'YDOP'], ['vdop', 'VDOP'],
      ['hdop', 'HDOP'], ['pdop', 'PDOP'], ['tdop', 'TDOP'], ['gdop', 'GDOP']]) {
      dopRow.append(chip(label, F.isNum(d[k]) ? d[k].toFixed(2) : F.DASH));
    }
    const a = g.accuracy || {};
    errTiles.append(
      tile('EPX (longitude)', F.distance(a.epx_m, units)),
      tile('EPY (latitude)', F.distance(a.epy_m, units)),
      tile('EPV (vertical)', F.distance(a.epv_m, units)),
      tile('EPH (2D CEP)', F.distance(a.eph_m, units)),
      tile('SEP (3D)', F.distance(a.sep_m, units)),
      tile('EPS (speed)', F.isNum(a.eps_mps) ? F.speed(a.eps_mps, units, 2) : F.DASH),
      tile('EPD (track)', F.degrees(a.epd_deg)),
      tile('EPT (time)', F.siSeconds(a.ept_s)),
    );

    // NMEA GST pseudorange-noise statistics — only shown when the receiver
    // actually sends the sentence.
    const gst = g.gst;
    clear(gstSlot);
    if (gst) {
      gstSlot.append(
        el('div.muted', { style: { fontSize: '11.5px', marginTop: '4px' } },
          'GST error statistics (1σ)'),
        el('div.tiles',
          tile('RMS', F.distance(gst.rms_m, units)),
          tile('Semi-major', F.distance(gst.major_m, units),
            { sub: F.isNum(gst.orient_deg) ? `at ${F.degrees(gst.orient_deg, 0)}` : null }),
          tile('Semi-minor', F.distance(gst.minor_m, units)),
          tile('Latitude err', F.distance(gst.lat_err_m, units)),
          tile('Longitude err', F.distance(gst.lon_err_m, units)),
          tile('Altitude err', F.distance(gst.alt_err_m, units))));
    }
  }

  function renderRecorder() {
    clear(recStats);
    recBtn.textContent = recorder.recording ? 'Stop recording' : 'Start recording';
    recBtn.classList.toggle('is-on', recorder.recording);
    recBtn.setAttribute('aria-pressed', String(recorder.recording));
    recStats.append(
      tile('Samples', String(recorder.count), { sub: recorder.dropped ? `${recorder.dropped} dropped` : null }),
      tile('Duration', recorder.count ? F.duration(recorder.durationS) : F.DASH),
      tile('Approx size', F.bytes(recorder.approxBytes())),
      tile('State', recorder.recording ? 'recording' : 'stopped',
        { level: recorder.recording ? 'good' : null }),
    );
    for (const b of exportBtns.children) b.disabled = recorder.count === 0;
  }

  function renderAll() {
    const status = store.status;
    const g = status && status.gps;
    mapPanel.update(g, recorder.track());
    sky.update(g && g.satellites);
    satTable.update(g && g.satellites);
    renderGauge(status);
    renderAccuracy(status);
    // Domain-level failure notices, once per card.
    for (const [c, dom] of [[skyCard, g], [satCard, g], [accCard, g], [mapCard, g]]) {
      const existing = c.body.querySelector('.card-banner');
      if (dom && dom.available === false) {
        if (!existing) c.body.prepend(banner(dom.error || 'gpsd data unavailable', { error: true }));
      } else if (existing) existing.remove();
    }
  }

  // ------------------------------------------------------------ data flow
  let lastSourcesAt = 0;
  let sourcesInFlight = false;
  async function pollSources() {
    if (sourcesInFlight) return;
    if (Date.now() - lastSourcesAt < SOURCES_MIN_INTERVAL_MS) return;
    sourcesInFlight = true;
    try {
      const data = await api.getSources();
      lastSourcesAt = Date.now();
      store.setSources(data.ntp_sources || data.sources || null);
      sources.update(data.ntp_sources || data.sources || null);
    } catch (err) {
      lastSourcesAt = Date.now();
      sources.update({ available: false, error: err.message || String(err), sources: [], sourcestats: [] });
    } finally {
      sourcesInFlight = false;
    }
  }

  function refreshOpenRaw() {
    for (const p of rawPanels) if (p.det.open) loadRaw(p);
  }

  renderAll();
  renderRecorder();
  sources.update(store.sources);
  pollSources();
  history.load();

  const unsubStore = store.subscribe((_s, reason) => {
    if (reason === 'status') {
      renderAll();
      // Stamp live points with the SERVER's clock (server.t_send), not the
      // browser's: the history rows from /api/v1/history are on the server
      // timeline, and mixing the two would put a step at the right edge.
      const srv = store.status && store.status.server;
      const tUnix = srv && Number.isFinite(srv.t_send)
        ? srv.t_send
        : (store.statusAt || Date.now()) / 1000;
      history.appendLive(store.status, tUnix);
      pollSources();
      refreshOpenRaw();
    } else if (reason === 'settings') {
      renderAll();
    }
  });
  const unsubRec = recorder.subscribe(renderRecorder);

  // Leaflet must be told about its box once the card has been laid out.
  const invalidateTimer = setTimeout(() => mapPanel.invalidate(), 60);
  const onResize = () => mapPanel.invalidate();
  window.addEventListener('resize', onResize, { passive: true });

  return function unmount() {
    clearTimeout(invalidateTimer);
    window.removeEventListener('resize', onResize);
    unsubStore();
    unsubRec();
    for (const d of disposers) { try { d(); } catch (e) { console.error(e); } }
    clear(root);
  };
}

export default { mount };
