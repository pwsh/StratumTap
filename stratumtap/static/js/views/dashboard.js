// Dashboard (#/): hero clock, status pills, NTP card, GPS card, satellite bars.
//
// mount(root, ctx) returns an unmount() that tears down every timer, rAF and
// store subscription it created.

import { el, clear, card, tile, tileGrid, pill, chip, kv, banner, offsetLevel } from '../components/tiles.js';
import { constColor, constName, satNumber } from '../components/skyplot.js';
import * as F from '../format.js';

const CLOCK_INTERVAL_MS = 50;   // the hero clock only needs ~20 Hz

export function mount(root, ctx) {
  const { store, clock } = ctx;
  clear(root);

  const grid = el('div.grid');
  root.append(grid);

  // --------------------------------------------------------------- pills
  // The status band sits above everything: it answers "is it working?" first.
  const pillsCard = card('Status', { cls: 'span-all' });
  grid.append(pillsCard.root);

  // ---------------------------------------------------------------- hero
  const hero = card('Server time', { cls: 'hero span-all' });
  const clockEl = el('div.clock', { role: 'timer', 'aria-live': 'off' },
    el('span.hms', { text: '--:--:--' }),
    el('span.ms', { text: '.---' }),
    el('span.zone', { text: 'UTC' }));
  const dateEl = el('div.datestr', { text: '—' });
  const localEl = el('div.localstr', { text: '—' });
  const syncEl = el('div.syncline');
  const syncText = el('span', { text: 'measuring clock offset…' });
  const badge = el('span.badge', { dataset: { kind: 'corrected' }, text: 'corrected' });
  syncEl.append(syncText, badge);
  const explain = el('details.explain',
    el('summary', { text: 'How is this time obtained?' }),
    el('p', {
      html: 'Every API request carries the browser send time <code>t0</code>; the server '
        + 'stamps receive (<code>t1</code>) and send (<code>t2</code>) times into the response, and the '
        + 'browser records arrival as <code>t3</code>. Round-trip delay is '
        + '<code>(t3−t0)−(t2−t1)</code> and the clock offset is <code>((t1−t0)+(t2−t3))/2</code> — the '
        + 'same four-timestamp exchange NTP itself uses. The estimator keeps the last 8 exchanges and '
        + 'trusts the one with the lowest delay. With correction off the display shows the timestamp '
        + 'exactly as received, ticking forward on the browser clock, which lags by about half the round trip.',
    }));
  // Banner layout: the clock on the left, the sync explanation on the right.
  hero.body.append(el('div.hero-band',
    el('div.hero-left', clockEl, dateEl, localEl),
    el('div.hero-right', syncEl, explain)));
  grid.append(hero.root);

  // --------------------------------------------------------------- pills
  const pillsRow = el('div.pills');
  pillsCard.body.append(pillsRow);

  // ----------------------------------------------------------- ntp card
  const ntpCard = card('Time sync (chrony)', { cls: 'span-all' });
  const ntpBanner = el('div');
  const ntpFigure = el('div.hero-figure',
    el('span.fig', { text: F.DASH }), el('span.word'));
  const ntpTiles = el('div.tiles');
  ntpCard.body.append(ntpBanner, ntpFigure, ntpTiles);
  grid.append(ntpCard.root);

  // ----------------------------------------------------------- gps card
  const gpsCard = card('GPS (gpsd)', { cls: 'span-2' });
  const gpsBanner = el('div');
  const gpsKv = el('div');
  const gpsTiles = el('div.tiles');
  gpsCard.body.append(gpsBanner, gpsKv, gpsTiles);
  grid.append(gpsCard.root);

  // ---------------------------------------------------------- satellites
  const satCard = card('Satellites', { cls: '' });
  const satSummary = el('div.rowline');
  const satBars = el('div.satbars');
  const dopChips = el('div.chips');
  satCard.body.append(satSummary, satBars, dopChips,
    el('p.muted', { style: { fontSize: '11.5px', margin: '2px 0 0' } },
      'Solid = used in the fix, hatched outline = tracked but unused.'));
  grid.append(satCard.root);

  // --------------------------------------------------------------- link
  grid.append(el('div.span-all', { style: { textAlign: 'right' } },
    el('a', { href: '#/detail' }, 'Detail view: map, sky plot, gauge, history →')));

  // ------------------------------------------------------------ clock tick
  // Pre-created nodes + a single interval: no per-tick allocation and no layout
  // reads. We write textContent on three spans and nothing else.
  const hmsNode = clockEl.querySelector('.hms');
  const msNode = clockEl.querySelector('.ms');
  const zoneNode = clockEl.querySelector('.zone');
  let lastSecond = -1;
  let raf = 0;
  let lastPaint = 0;

  function paintClock(now) {
    if (document.hidden) return;
    if (now - lastPaint < CLOCK_INTERVAL_MS) return;
    lastPaint = now;
    const corrected = store.settings.correction;
    const ms = clock.ready || clock.lastReceived ? clock.now(corrected) : Date.now();
    const d = new Date(ms);
    const s = d.getUTCSeconds();
    if (s !== lastSecond) {
      // Only rebuild the HH:MM:SS string when the second actually changed.
      lastSecond = s;
      hmsNode.textContent = F.utcClock(d, false);
      dateEl.textContent = `${F.utcDate(d)} UTC`;
      localEl.textContent = `${F.localClock(d)} local · ${F.localZone()}`;
    }
    msNode.textContent = `.${F.pad(d.getUTCMilliseconds(), 3)}`;
    zoneNode.textContent = 'UTC';
  }

  function frame(now) {
    paintClock(now);
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  const onVisible = () => { lastSecond = -1; };
  document.addEventListener('visibilitychange', onVisible);

  // ------------------------------------------------------------- rendering
  function renderSync() {
    const off = clock.offsetMs();
    const delay = clock.delayMs();
    const n = clock.sampleCount();
    if (off == null) {
      syncText.textContent = n ? 'clock offset not yet usable' : 'measuring clock offset…';
    } else {
      // The browser is +X ms relative to the server when the server is behind us.
      const browserVsServer = -off;
      syncText.textContent =
        `Browser clock is ${F.siSeconds(browserVsServer / 1000, { sign: true })} vs server`
        + ` · RTT ${F.siSeconds((delay || 0) / 1000)}`
        + ` · ${n} sample${n === 1 ? '' : 's'}`;
    }
    const corrected = store.settings.correction;
    badge.textContent = corrected ? 'corrected' : 'as received';
    badge.dataset.kind = corrected ? 'corrected' : 'raw';
    badge.title = corrected
      ? 'Displayed time = browser clock + measured offset'
      : 'Displayed time = the server timestamp as received, ticking forward locally';
  }

  function renderPills(status) {
    clear(pillsRow);
    const n = status && status.ntp;
    const g = status && status.gps;

    if (n && n.available !== false) {
      const sync = n.synchronized === true;
      pillsRow.append(pill('NTP', sync ? `Synchronized · stratum ${n.stratum ?? F.DASH}` : 'Not synchronized',
        { state: sync ? 'good' : 'critical' }));
      pillsRow.append(pill('Reference', `${n.reference_name || F.DASH}`,
        { state: sync && n.reference_name ? 'good' : null,
          title: n.reference_id ? `Reference ID ${n.reference_id}` : null }));
      pillsRow.append(pill('Leap', n.leap_status || F.DASH,
        { state: n.leap_status === 'Normal' ? 'good' : n.leap_status ? 'warning' : null }));
    } else {
      pillsRow.append(pill('NTP', 'unavailable', { state: 'critical' }));
    }

    if (g && g.available !== false) {
      const fix = g.fix || {};
      const has = F.isNum(fix.mode) && fix.mode >= 2;
      pillsRow.append(pill('Fix', fix.fix_text || fix.mode_text || F.DASH, {
        state: has ? 'good' : 'warning',
        title: F.isNum(fix.fix_age_s) ? `mode unchanged for ${F.duration(fix.fix_age_s)}` : null,
      }));
      if (F.isNum(fix.fix_age_s)) {
        pillsRow.append(pill('Fix age', F.duration(fix.fix_age_s), {
          state: has ? (fix.fix_age_s >= 60 ? 'good' : 'warning') : null,
          title: 'Time since the fix mode last changed \u2014 longer means a stable fix',
        }));
      }
      const sats = g.satellites || {};
      pillsRow.append(pill('Satellites', `${sats.used ?? F.DASH}/${sats.seen ?? F.DASH} used`, {
        state: F.isNum(sats.used) ? (sats.used >= 4 ? 'good' : 'warning') : null,
      }));
    } else {
      pillsRow.append(pill('GPS', g && g.connected === false ? 'gpsd not connected' : 'unavailable',
        { state: 'critical' }));
    }
  }

  function renderNtp(status) {
    const n = status && status.ntp;
    clear(ntpBanner);
    clear(ntpTiles);
    if (!n) return;
    if (n.available === false) {
      ntpBanner.append(banner(n.error || 'chrony data unavailable', { error: true }));
      ntpFigure.querySelector('.fig').textContent = F.DASH;
      ntpFigure.querySelector('.word').textContent = '';
      ntpFigure.removeAttribute('data-level');
      return;
    }
    const off = n.system_offset_s;
    const level = offsetLevel(off);
    ntpFigure.querySelector('.fig').textContent = F.siSeconds(off, { sign: true });
    ntpFigure.querySelector('.word').textContent =
      F.isNum(off) ? `system clock ${F.fastSlow(off) || 'exact'}` : 'no reading';
    if (level) ntpFigure.dataset.level = level; else ntpFigure.removeAttribute('data-level');
    ntpFigure.title = 'chrony "System time": how far the system clock is ahead of (fast) or behind (slow) true time';

    ntpTiles.append(
      tile('Last offset', F.siSeconds(n.last_offset_s, { sign: true })),
      tile('RMS offset', F.siSeconds(n.rms_offset_s)),
      tile('Frequency', F.ppm(n.frequency_ppm)),
      tile('Residual freq', F.isNum(n.residual_freq_ppm) ? F.signedNum(n.residual_freq_ppm, 3, 'ppm') : F.DASH),
      tile('Skew', F.isNum(n.skew_ppm) ? F.num(n.skew_ppm, 3, 'ppm') : F.DASH),
      tile('Root delay', F.siSeconds(n.root_delay_s)),
      tile('Root dispersion', F.siSeconds(n.root_dispersion_s)),
      tile('Update interval', F.isNum(n.update_interval_s) ? F.duration(n.update_interval_s) : F.DASH),
      tile('Ref time', n.ref_time ? `${F.utcClock(new Date(n.ref_time), false)} UTC` : F.DASH,
        { sub: F.isNum(n.ref_time_unix) ? F.relTime((Date.now() / 1000) - n.ref_time_unix) : null }),
      tile('Data age', F.isNum(n.age_s) ? F.duration(n.age_s) : F.DASH),
    );
  }

  function renderGps(status) {
    const g = status && status.gps;
    const units = store.settings.units;
    clear(gpsBanner); clear(gpsKv); clear(gpsTiles);
    if (!g) return;
    if (g.available === false) {
      gpsBanner.append(banner(g.error || 'gpsd data unavailable', { error: true }));
      return;
    }
    const p = g.position || {};
    const m = g.motion || {};
    const a = g.accuracy || {};
    const fix = g.fix || {};
    const to = g.time_offset || {};

    gpsKv.append(kv([
      ['Latitude', F.latLonDec(p.lat, 'lat'), F.latLonDMS(p.lat, 'lat')],
      ['Longitude', F.latLonDec(p.lon, 'lon'), F.latLonDMS(p.lon, 'lon')],
      ['Grid square', p.grid_square || F.DASH],
    ]));

    gpsTiles.append(
      tile('Altitude (MSL)', F.altitude(p.alt_msl_m, units)),
      tile('Altitude (HAE)', F.altitude(p.alt_hae_m, units),
        { sub: F.isNum(p.geoid_sep_m) ? `geoid ${F.distance(p.geoid_sep_m, units)}` : null }),
      tile('Speed', F.speed(m.speed_mps, units)),
      tile('Track (true)', F.degrees(m.track_deg),
        { sub: F.isNum(m.mag_track_deg) ? `mag ${F.degrees(m.mag_track_deg)}` : null }),
      tile('Climb', F.isNum(m.climb_mps) ? F.speed(m.climb_mps, units) : F.DASH),
      tile('2D error (EPH)', F.distance(a.eph_m, units)),
      tile('3D error (SEP)', F.distance(a.sep_m, units)),
      tile('Fix mode', fix.fix_text || fix.mode_text || F.DASH,
        { sub: fix.status_text || null }),
      tile('GPS time', fix.time ? `${F.utcClock(new Date(fix.time), false)} UTC` : F.DASH,
        { sub: F.isNum(fix.ept_s) ? `ept ${F.siSeconds(fix.ept_s)}` : null }),
      tile('Fix age (cgps)', F.isNum(fix.time_age_s) ? F.siSeconds(fix.time_age_s, { sign: true }) : F.DASH,
        { sub: g.cgps_time_offset_text || g.raw_time_offset_text || null,
          title: "Server send time minus the GPS fix timestamp — the line cgps -s labels 'Time offset'" }),
      tile('GPS→system offset', F.siSeconds(to.offset_s, { sign: true }),
        { sub: to.source ? `source ${to.source}` : 'no PPS/TOFF',
          title: 'System clock minus GPS time, from gpsd PPS/TOFF messages' }),
      tile('Leap seconds', F.isNum(fix.leapseconds) ? String(fix.leapseconds) : F.DASH),
    );
  }

  function renderSats(status) {
    const g = status && status.gps;
    const sats = g && g.satellites;
    clear(satSummary); clear(satBars); clear(dopChips);
    if (!sats || !Array.isArray(sats.list) || !sats.list.length) {
      satSummary.append(el('span.muted', { text: 'No satellites reported' }));
      return;
    }
    satSummary.append(el('span', { text: `${sats.used ?? 0} used of ${sats.seen ?? sats.list.length} seen` }));

    // Strongest first: the bars are read top-down as a signal-quality ranking.
    const list = sats.list.slice().sort((x, y) => (y.snr_db ?? -1) - (x.snr_db ?? -1)).slice(0, 16);
    for (const s of list) {
      const color = constColor(s.gnss);
      const pct = F.isNum(s.snr_db) ? Math.max(2, Math.min(100, (s.snr_db / 50) * 100)) : 0;
      const fill = el(`span.fill${s.used ? '' : '.unused'}`, {
        style: { width: `${pct.toFixed(0)}%`, ...(s.used ? { background: color } : { color }) },
      });
      satBars.append(el('div.satbar', {
        title: `${constName(s.gnss, s.gnss_name)} ${satNumber(s) ?? ''} · el ${s.el_deg ?? '—'}° az ${s.az_deg ?? '—'}° · ${s.used ? 'used' : 'not used'}`,
      },
        el('span.who',
          el('span.sat-swatch', { style: { background: color }, 'aria-hidden': 'true' }),
          String(satNumber(s) ?? '—')),
        el('span.track', fill),
        el('span.val', { text: F.isNum(s.snr_db) ? `${s.snr_db.toFixed(0)} dB` : F.DASH })));
    }

    const d = (g && g.dop) || {};
    for (const [k, label] of [['hdop', 'HDOP'], ['vdop', 'VDOP'], ['pdop', 'PDOP'], ['tdop', 'TDOP'], ['gdop', 'GDOP']]) {
      dopChips.append(chip(label, F.isNum(d[k]) ? d[k].toFixed(2) : F.DASH));
    }
  }

  function renderAll() {
    const status = store.status;
    if (!status) {
      // Skeleton state: the very first request is still in flight.
      clear(pillsRow);
      for (let i = 0; i < 3; i++) pillsRow.append(el('div.pill.skeleton', { style: { width: '9rem', height: '30px' } }));
      return;
    }
    renderPills(status);
    renderNtp(status);
    renderGps(status);
    renderSats(status);
  }

  renderSync();
  renderAll();

  const unsub = store.subscribe((_s, reason) => {
    if (reason === 'status' || reason === 'error') { renderAll(); renderSync(); }
    else if (reason === 'settings') { renderSync(); renderAll(); }
  });

  // The sync line changes as new samples land even when the payload is identical.
  const syncTimer = setInterval(renderSync, 1000);

  return function unmount() {
    cancelAnimationFrame(raf);
    clearInterval(syncTimer);
    document.removeEventListener('visibilitychange', onVisible);
    unsub();
    clear(root);
  };
}

export default { mount };
