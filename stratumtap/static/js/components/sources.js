// chrony sources + sourcestats tables (detail view).

import { el, clear, banner } from './tiles.js';
import { DASH, isNum, siSeconds, duration, num, reach, reachTitle } from '../format.js';

/** chronyc's state character → a word. The word is what carries the meaning. */
const STATE_TEXT = {
  '*': 'current best', '+': 'combined', '-': 'not combined',
  x: 'falseticker', '~': 'too variable', '?': 'unusable', ' ': '',
};
const MODE_TEXT = { '^': 'server', '=': 'peer', '#': 'refclock' };

function stateLevel(ch) {
  if (ch === '*') return 'good';
  if (ch === '+') return null;
  if (ch === 'x' || ch === '?') return 'critical';
  return 'warning';
}

export class SourcesPanel {
  constructor(root) {
    this.root = root;
    this.build();
  }

  build() {
    clear(this.root);
    this.bannerSlot = el('div');
    this.sourcesWrap = el('div.table-wrap');
    this.statsWrap = el('div.table-wrap');
    this.root.append(
      this.bannerSlot,
      el('h3', { style: { fontSize: '12px', color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }, text: 'Sources' }),
      this.sourcesWrap,
      el('h3', { style: { fontSize: '12px', color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: '6px' }, text: 'Source statistics' }),
      this.statsWrap,
    );
  }

  update(src) {
    clear(this.bannerSlot);
    if (!src) {
      clear(this.sourcesWrap).append(el('p.muted', { text: 'Loading…' }));
      clear(this.statsWrap);
      return;
    }
    if (src.available === false) {
      this.bannerSlot.append(banner(src.error || 'chronyc sources unavailable', { error: true }));
    }
    this.#renderSources(Array.isArray(src.sources) ? src.sources : []);
    this.#renderStats(Array.isArray(src.sourcestats) ? src.sourcestats : []);
  }

  #renderSources(rows) {
    const head = ['Mode', 'State', 'Name / IP', 'Str', 'Poll', 'Reach', 'LastRx', 'Last sample', '± error'];
    const t = table(head, [0, 1, 2]);
    if (!rows.length) {
      t.tbody.append(emptyRow(head.length));
    }
    for (const s of rows) {
      const st = s.state || ' ';
      const lvl = stateLevel(st);
      const tr = el(`tr${st === '*' ? '.is-used' : ''}`);
      tr.append(el('td.txt', { title: s.mode_text || MODE_TEXT[s.mode] || '' },
        el('span.mono', { text: s.mode || DASH }), ' ',
        el('span.muted', { text: s.mode_text || MODE_TEXT[s.mode] || '' })));
      tr.append(el('td.txt', { dataset: lvl ? { level: lvl } : {} },
        el('span.mono', { text: st.trim() || DASH }), ' ',
        el('span', { text: s.state_text || STATE_TEXT[st] || '' })));
      tr.append(el('td.txt.mono', { text: s.name || DASH }));
      tr.append(el('td.num', { text: isNum(s.stratum) ? s.stratum : DASH }));
      tr.append(el('td.num', { text: isNum(s.poll) ? `2^${s.poll}` : DASH, title: isNum(s.poll) ? `${2 ** s.poll} s` : null }));
      tr.append(el('td.num', { text: reach(s.reach), title: reachTitle(s.reach) }));
      tr.append(el('td.num', { text: isNum(s.last_rx_s) ? duration(s.last_rx_s) : DASH }));
      tr.append(el('td.num', { text: siSeconds(s.last_sample_offset_s, { sign: true }) }));
      tr.append(el('td.num', { text: siSeconds(s.last_sample_error_s) }));
      t.tbody.append(tr);
    }
    clear(this.sourcesWrap).append(t.table);
  }

  #renderStats(rows) {
    const head = ['Name / IP', 'NP', 'NR', 'Span', 'Frequency', 'Freq skew', 'Offset', 'Std dev'];
    const t = table(head, [0]);
    if (!rows.length) t.tbody.append(emptyRow(head.length));
    for (const s of rows) {
      const tr = el('tr');
      tr.append(el('td.txt.mono', { text: s.name || DASH }));
      tr.append(el('td.num', { text: isNum(s.np) ? s.np : DASH }));
      tr.append(el('td.num', { text: isNum(s.nr) ? s.nr : DASH }));
      tr.append(el('td.num', { text: isNum(s.span_s) ? duration(s.span_s) : DASH }));
      tr.append(el('td.num', { text: isNum(s.frequency_ppm) ? `${s.frequency_ppm >= 0 ? '+' : ''}${num(s.frequency_ppm, 3)} ppm` : DASH }));
      tr.append(el('td.num', { text: isNum(s.freq_skew_ppm) ? `${num(s.freq_skew_ppm, 3)} ppm` : DASH }));
      tr.append(el('td.num', { text: siSeconds(s.offset_s, { sign: true }) }));
      tr.append(el('td.num', { text: siSeconds(s.std_dev_s) }));
      t.tbody.append(tr);
    }
    clear(this.statsWrap).append(t.table);
  }

  destroy() { clear(this.root); }
}

function table(head, textCols = []) {
  const tbl = el('table.data');
  const tr = el('tr');
  head.forEach((h, i) => tr.append(el('th', { scope: 'col', class: textCols.includes(i) ? 'txt' : '', text: h })));
  const tbody = el('tbody');
  tbl.append(el('thead', tr), tbody);
  return { table: tbl, tbody };
}

function emptyRow(cols) {
  return el('tr', el('td', { colspan: cols, class: 'txt muted', text: 'No sources reported' }));
}

export default SourcesPanel;
