// Small DOM helpers shared by every view: element creation, stat tiles,
// status pills, chips, key/value lists. All DOM work happens inside functions.

import { DASH } from '../format.js';

/** el('div.card', {id:'x'}, child, 'text') — tag[.class][#id] shorthand. */
export function el(spec, attrs, ...children) {
  const m = /^([a-zA-Z0-9-]+)?((?:[.#][\w-]+)*)$/.exec(spec) || [];
  const tag = m[1] || 'div';
  const node = document.createElement(tag);
  for (const token of (m[2] || '').match(/[.#][\w-]+/g) || []) {
    if (token[0] === '.') node.classList.add(token.slice(1));
    else node.id = token.slice(1);
  }
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node)) {
    children.unshift(attrs);
  } else if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'text') node.textContent = String(v);
      else if (k === 'html') node.innerHTML = v;
      else if (k === 'class') node.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  add(node, children);
  return node;
}

function add(node, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) add(node, c);
    else node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** A card shell with a title and an optional trailing note element. */
export function card(title, { note = null, cls = '', id = null } = {}) {
  const noteEl = el('span.hdr-note');
  if (note) noteEl.textContent = note;
  const body = el('div.card-body', { style: { display: 'contents' } });
  const root = el(`section.card${cls ? '.' + cls.split(' ').join('.') : ''}`,
    { id, 'aria-label': title },
    el('header', el('h2', { text: title }), noteEl),
    body);
  return { root, body, note: noteEl };
}

/**
 * One stat tile. `value` is already formatted; `sub` is an optional second line.
 * level: null | 'good' | 'warn'
 */
export function tile(label, value, { sub = null, level = null, title = null } = {}) {
  return el(`div.tile${level ? '.is-' + level : ''}`, { title },
    el('span.label', { text: label }),
    el('span.value', { text: value == null ? DASH : String(value) }),
    sub ? el('span.sub', { text: sub }) : null);
}

export function tileGrid(items) {
  const g = el('div.tiles');
  for (const t of items) if (t) g.append(t);
  return g;
}

/** Status pill: color is backed by an always-present text label. */
export function pill(key, value, { state = null, title = null } = {}) {
  return el('div.pill', { dataset: state ? { state } : {}, title },
    el('span.mark', { 'aria-hidden': 'true' }),
    el('span.k', { text: key }),
    el('span.v', { text: value == null ? DASH : String(value) }));
}

export function chip(key, value, { title = null } = {}) {
  return el('div.chip', { title },
    el('b', { text: key }),
    el('span', { text: value == null ? DASH : String(value) }));
}

/** Definition list of label → value (value may carry a second muted line). */
export function kv(rows) {
  const dl = el('dl.kv');
  for (const r of rows) {
    if (!r) continue;
    const [k, v, sub] = r;
    dl.append(el('dt', { text: k }));
    dl.append(el('dd', { text: v == null ? DASH : String(v) },
      sub ? el('span.sub', { text: sub }) : null));
  }
  return dl;
}

/** Muted in-card banner used when a domain reports available:false. */
export function banner(text, { error = false } = {}) {
  return el(`div.card-banner${error ? '.is-error' : ''}`, { text, role: error ? 'status' : null });
}

/** Gray shimmering placeholder of roughly `chars` width. */
export function skeleton(chars = 8) {
  return el('span.skeleton', { style: { display: 'inline-block', width: `${chars}ch` }, text: ' ' });
}

/** Severity of an absolute time offset, independent of any gauge scale. */
export function offsetLevel(sec) {
  if (typeof sec !== 'number' || !Number.isFinite(sec)) return null;
  const a = Math.abs(sec);
  if (a < 1e-4) return 'good';        // < 100 µs
  if (a < 1e-2) return 'warning';     // < 10 ms
  return 'critical';
}

/** CSS color for a severity level, for canvas/SVG code. */
export function levelColor(level, fallback = 'var(--ink)') {
  return level === 'good' ? 'var(--good)'
    : level === 'warning' ? 'var(--warning)'
      : level === 'critical' ? 'var(--critical)' : fallback;
}

/** Read a CSS custom property off :root (canvas cannot use var()). */
export function cssVar(name, fallback = '#888') {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
