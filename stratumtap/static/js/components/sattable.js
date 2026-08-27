// Sortable satellite table. Columns follow cgps: the primary satellite number
// is `svid` (the conventional PRN — SBAS 133 etc.); gpsd's internal `prn` is a
// secondary column.

import { el, clear } from './tiles.js';
import { constColor, constName, satNumber } from './skyplot.js';
import { DASH, isNum, num } from '../format.js';

const COLUMNS = [
  { key: 'gnss', label: 'GNSS', txt: true, get: (s) => s.gnss || '??' },
  { key: 'sat', label: 'Sat', get: satNumber, title: 'svid — the conventional satellite number (SBAS 133 etc.)' },
  { key: 'prn', label: 'PRN', get: (s) => s.prn, title: "gpsd's internal PRN" },
  { key: 'sigid', label: 'Sig', get: (s) => s.sigid, title: 'signal id' },
  { key: 'el_deg', label: 'Elev', get: (s) => s.el_deg },
  { key: 'az_deg', label: 'Azim', get: (s) => s.az_deg },
  { key: 'snr_db', label: 'SNR', get: (s) => s.snr_db },
  { key: 'used', label: 'Used', get: (s) => (s.used ? 1 : 0) },
  { key: 'health', label: 'Health', get: (s) => s.health },
];

/** Stable comparison that always sinks nulls to the bottom. */
function cmp(a, b, dir) {
  const an = a === null || a === undefined || Number.isNaN(a);
  const bn = b === null || b === undefined || Number.isNaN(b);
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  if (typeof a === 'string' || typeof b === 'string') {
    return String(a).localeCompare(String(b)) * dir;
  }
  return (a - b) * dir;
}

function healthText(h) {
  if (h === 1) return 'OK';
  if (h === 0) return 'bad';
  if (isNum(h)) return String(h);
  return DASH;
}

export class SatTable {
  constructor(root) {
    this.root = root;
    this.sortKey = 'snr_db';
    this.sortDir = -1;      // descending SNR by default: the useful ones first
    this.list = [];
    this.build();
  }

  build() {
    clear(this.root);
    const table = el('table.data', { id: 'sat-table' });
    const thead = el('thead');
    const tr = el('tr');
    for (const col of COLUMNS) {
      const th = el('th', { scope: 'col', class: col.txt ? 'txt' : '', title: col.title || null });
      const btn = el('button', {
        type: 'button',
        onclick: () => this.#sortBy(col.key),
      }, col.label, el('span.sort-ind', { 'aria-hidden': 'true' }));
      th.append(btn);
      tr.append(th);
      col._th = th;
    }
    thead.append(tr);
    this.tbody = el('tbody');
    table.append(thead, this.tbody);
    const cap = el('caption.visually-hidden', { text: 'Satellites in view; click a column header to sort' });
    table.prepend(cap);
    this.root.classList.add('table-wrap');
    this.root.append(table);
    this.#markSort();
  }

  #sortBy(key) {
    if (this.sortKey === key) this.sortDir = -this.sortDir;
    else { this.sortKey = key; this.sortDir = key === 'gnss' ? 1 : -1; }
    this.#markSort();
    this.render();
  }

  #markSort() {
    for (const col of COLUMNS) {
      if (!col._th) continue;
      const active = col.key === this.sortKey;
      if (active) col._th.setAttribute('aria-sort', this.sortDir === 1 ? 'ascending' : 'descending');
      else col._th.removeAttribute('aria-sort');
      const ind = col._th.querySelector('.sort-ind');
      if (ind) ind.textContent = active ? (this.sortDir === 1 ? '▲' : '▼') : '';
    }
  }

  update(satellites) {
    this.list = (satellites && Array.isArray(satellites.list)) ? satellites.list.slice() : [];
    this.render();
  }

  render() {
    const col = COLUMNS.find((c) => c.key === this.sortKey) || COLUMNS[0];
    // Secondary key = satellite number, so equal SNRs keep a stable order.
    const rows = this.list.slice().sort((a, b) => {
      const primary = cmp(col.get(a), col.get(b), this.sortDir);
      return primary !== 0 ? primary : cmp(satNumber(a), satNumber(b), 1);
    });

    clear(this.tbody);
    if (!rows.length) {
      this.tbody.append(el('tr', el('td', {
        colspan: COLUMNS.length, class: 'txt muted', text: 'No satellites reported',
      })));
      return;
    }
    for (const s of rows) {
      const tr = el(`tr${s.used ? '.is-used' : ''}`);
      tr.append(el('td.txt',
        el('span.sat-swatch', { style: { background: constColor(s.gnss) }, 'aria-hidden': 'true' }),
        `${s.gnss || '??'} ${constName(s.gnss, s.gnss_name)}`));
      tr.append(el('td.num', { text: satNumber(s) ?? DASH }));
      tr.append(el('td.num', { text: isNum(s.prn) ? s.prn : DASH }));
      tr.append(el('td.num', { text: isNum(s.sigid) ? s.sigid : DASH }));
      tr.append(el('td.num', { text: isNum(s.el_deg) ? `${num(s.el_deg, 0)}°` : DASH }));
      tr.append(el('td.num', { text: isNum(s.az_deg) ? `${num(s.az_deg, 0)}°` : DASH }));

      const pct = isNum(s.snr_db) ? Math.max(0, Math.min(100, (s.snr_db / 50) * 100)) : 0;
      tr.append(el('td',
        el('div.snrcell',
          el('span.bar', el('i', {
            style: { width: `${pct.toFixed(0)}%`, background: constColor(s.gnss) },
          })),
          el('span.num', { text: isNum(s.snr_db) ? s.snr_db.toFixed(0) : DASH }))));

      tr.append(el('td.num', { text: s.used ? 'Y' : '·' }));
      tr.append(el('td.num', { text: healthText(s.health) }));
      this.tbody.append(tr);
    }
  }

  destroy() { clear(this.root); }
}

export default SatTable;
