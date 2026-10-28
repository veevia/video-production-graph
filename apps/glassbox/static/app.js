/* Glass Box UI: a vanilla ES module served by the app.
   Untrusted text only ever goes through textContent or setAttribute; nothing is parsed as HTML. */

import { filmHref, hrefs, parseRoute, redirectOf } from './js/routes.js';

/* Node statuses from the server, read as one mark (glyph and word) of the shared vocabulary. */
const STATUS = {
  failed: { label: 'Rework', mark: 'rework' },
  waiting: { label: 'Needs you', mark: 'needs' },
  running: { label: 'In progress', mark: 'in_progress' },
  warnings: { label: 'Passed, P4 notes', mark: 'passed' },
  unknown: { label: 'Unknown', mark: 'unknown' },
  passed: { label: 'Passed', mark: 'passed' },
  direct: { label: 'Passed \u00b7 built outside the engine', mark: 'passed' },
  not_run: { label: 'Not run', mark: 'not_run' },
  superseded: { label: 'Superseded', mark: 'superseded' },
  unrecorded: { label: 'Passed (not recorded by the engine run)', mark: 'passed' },
  decided: { label: 'Decided by you \u00b7 engine waits', mark: 'decided_changes' },
};
const UNRECORDED_WORDS = 'Passed (not recorded by the engine run)';
const VERDICT = {
  pass: { label: 'Pass', mark: 'passed' },
  rework: { label: 'Rework', mark: 'rework' },
  unknown: { label: 'Unknown', mark: 'unknown' },
  none: { label: 'Not reviewed', mark: 'not_reviewed' },
};
const MARK_WORDS = {
  needs: 'Needs you', passed: 'Passed', rework: 'Rework', in_progress: 'In progress', not_run: 'Not run',
  not_reviewed: 'Not reviewed', partial: 'Partly reviewed', superseded: 'Superseded', unknown: 'Unknown', direct: 'Outside the engine',
  decided_ok: 'You approved', decided_changes: 'You requested changes',
};
const ICONS = {
  check: 'M3 8.5l3.2 3.2L13 4.8',
  cross: 'M4 4l8 8M12 4l-8 8',
  clock: 'M8 2.2a5.8 5.8 0 1 0 0 11.6A5.8 5.8 0 1 0 8 2.2zM8 4.8V8l2.4 1.6',
  running: 'M13.5 8A5.5 5.5 0 1 1 8 2.5M13.5 2.5v3.3h-3.3',
  warn: 'M8 2.2l6.3 11.3H1.7zM8 6.4v3.4M8 11.6v.4',
  question: 'M5.8 6a2.2 2.2 0 1 1 3.4 1.8c-.8.5-1.2 1-1.2 1.9M8 11.8v.4',
  arrow: 'M2.5 8h10M9 4.5L12.5 8 9 11.5',
  circle: 'M8 2.6a5.4 5.4 0 1 0 0 10.8A5.4 5.4 0 1 0 8 2.6z',
  person: 'M8 2.4a2.6 2.6 0 1 0 0 5.2a2.6 2.6 0 1 0 0-5.2zM2.8 14c.4-2.9 2.6-4.6 5.2-4.6s4.8 1.7 5.2 4.6',
  diamond: 'M8 1.8L14.2 8 8 14.2 1.8 8z',
  spark: 'M8 1.8v4M8 10.2v4M1.8 8h4M10.2 8h4',
  wrench: 'M13.6 4.6a3.4 3.4 0 0 1-4.5 4L4 13.7a1.4 1.4 0 0 1-2-2L7.1 6.6a3.4 3.4 0 0 1 4-4.5L9 4.2l.6 2.2 2.2.6z',
  eye: 'M1.5 8S4 3.6 8 3.6 14.5 8 14.5 8 12 12.4 8 12.4 1.5 8 1.5 8zM8 6.2a1.8 1.8 0 1 0 0 3.6a1.8 1.8 0 1 0 0-3.6z',
  play: 'M5 3.2v9.6L12.8 8z',
  pause: 'M5.5 3.5v9M10.5 3.5v9',
  left: 'M10 3L5 8l5 5',
  right: 'M6 3l5 5-5 5',
  close: 'M4 4l8 8M12 4l-8 8',
  seek: 'M5 3.2v9.6L12.8 8z',
  flag: 'M4 14.2V1.8M4 2.4h8.2L10.4 5.4l1.8 3H4',
  note: 'M3.2 1.8h6.6l3 3v9.4H3.2zM9.8 1.8v3h3',
  copy: 'M5.5 5.5h7.7v8.7H5.5zM2.8 10.5V1.8h7.7',
  download: 'M8 2v8.2M4.6 7l3.4 3.4L11.4 7M2.8 13.6h10.4',
  ack: 'M2 8.6l2.6 2.6L9.8 6M7.4 11.2l.2.2L14 5',
  up: 'M8 13.2V3M3.8 7.2L8 3l4.2 4.2',
  down: 'M8 2.8v10.2M3.8 8.8L8 13l4.2-4.2',
  equals: 'M3.5 6h9M3.5 10h9',
  dash: 'M4 8h8',
  info: 'M8 2.2a5.8 5.8 0 1 0 0 11.6A5.8 5.8 0 1 0 8 2.2zM8 7.2v4M8 5v.4',
  film: 'M2.2 3.4h11.6v9.2H2.2zM5 3.4v9.2M11 3.4v9.2M2.2 6.4H5M2.2 9.6H5M11 6.4h2.8M11 9.6h2.8',
  search: 'M7 2.4a4.6 4.6 0 1 0 0 9.2A4.6 4.6 0 1 0 7 2.4zM10.4 10.4l3.4 3.4',
  undo: 'M5.5 3.2L2.6 6.1l2.9 2.9M2.8 6.1h6.6a3.6 3.6 0 0 1 0 7.2H6.4',
};
const POLL_MS_ROOM = 15000;
const DECIDE_TAB = 'decide';
const ROOM_TABS = [
  { id: 'watch', label: 'Watch' },
  { id: 'decide', label: 'Decide' },
  { id: 'compare', label: 'Compare' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'files', label: 'Files' },
];
const PHASE_WORDS = { understand: 'Understand', design: 'Design', build: 'Build', evaluate: 'Evaluate', learn: 'Learn', review: 'Review', deliver: 'Deliver' };
const UNDO_MS = 10000;
const SHEET_PAGE = 12;
const FINDINGS_FIRST = 3;

/* ------------------------------------------------------------------ shared renderers */

const POLL_MS = 15000;

const PREFS_KEY = 'vpg-glassbox:prefs:v1';

const SVG_NS = 'http://www.w3.org/2000/svg';

const TAGS = ['look', 'type', 'colour', 'motion', 'story', 'voice', 'music', 'mix', 'accuracy', 'accessibility'];

const LEVELS = ['P1', 'P2', 'P3', 'P4', 'P?'];

const KIND_LABEL = {
  approve: 'Approve', request_changes: 'Request changes', agree: 'Agree', disagree: 'Disagree',
  note: 'Note', flag: 'Flag', prefer: 'Prefer', ack: 'Acknowledged', retract: 'Retracted',
};

const LEDGER_LABELS = {
  run_created: 'run created', node_dispatched: 'node dispatched', node_passed: 'node passed', node_failed: 'node failed',
  awaiting_human: 'waiting for a human', decision: 'human decision', gate: 'review gate decided', memo_hit: 'memo hit',
  escalated: 'escalated', invalidated: 'invalidated', evaluation: 'evaluation recorded', direct_run: 'direct run recorded',
  provisional_expired: 'provisional approval expired', version_created: 'version created', config_accepted: 'configuration accepted',
};

const POLARITY = {
  better: { label: 'better', icon: 'up', cls: 'pol-better' },
  worse: { label: 'worse', icon: 'down', cls: 'pol-worse' },
  unchanged: { label: 'no change', icon: 'equals', cls: 'pol-same' },
  neutral: { label: 'no change', icon: 'equals', cls: 'pol-same' },
  'n/a': { label: 'not comparable', icon: 'question', cls: 'pol-na' },
};

const KIND_ICON = { agent: 'spark', tool: 'wrench', review: 'eye' };

const $ = (selector, root = document) => root.querySelector(selector);

const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const enc = (value) => encodeURIComponent(value ?? '');

let idCounter = 0;

const nextId = (prefix) => `${prefix}-${++idCounter}`;

const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'muted', 'selected', 'indeterminate', 'open']);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = String(value);
    else if (key === 'on') for (const [event, fn] of Object.entries(value)) el.addEventListener(event, fn);
    else if (key === 'dataset') for (const [k, v] of Object.entries(value)) { if (v != null) el.dataset[k] = String(v); }
    else if (key === 'style') for (const [k, v] of Object.entries(value)) el.style.setProperty(k, v);
    else if (PROPS.has(key)) el[key] = value;
    else if (key === 'href') el.setAttribute('href', safeHref(value));
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function frag(...kids) { return append(document.createDocumentFragment(), kids); }

function sr(text) { return h('span', { class: 'visually-hidden', text }); }

function safeHref(value) {
  const text = String(value || '');
  if (text.startsWith('#/') || text.startsWith('/media/') || text.startsWith('/api/') || text === '#') return text;
  return '#';
}

function icon(name, extra = '') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('class', `icon ${extra}`.trim());
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', ICONS[name] || ICONS.question);
  svg.append(path);
  return svg;
}

function fmtT(t) {
  if (t === null || t === undefined || !Number.isFinite(Number(t))) return 'no time';
  const value = Number(t);
  const minutes = Math.floor(value / 60);
  const seconds = value - minutes * 60;
  return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(1).padStart(4, '0')}`;
}

function fmtDuration(seconds) { return seconds === null || seconds === undefined ? 'unknown' : fmtT(seconds); }

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return 'unknown';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmtWhen(iso) {
  if (!iso) return 'unknown time';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const options = { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };
  if (date.getFullYear() !== new Date().getFullYear()) options.year = 'numeric';
  return date.toLocaleString(undefined, options);
}
/* A <time> with the friendly local time as text and the raw ISO value in the tooltip. */

function timeEl(iso, prefix = '') {
  if (!iso) return h('span', { class: 'sub', text: `${prefix}unknown time` });
  return h('time', { datetime: iso, title: iso, text: `${prefix}${fmtWhen(iso)}` });
}

const ENGINE_WORDS = { passed: 'passed', waiting: 'waiting for you', running: 'running', not_run: 'not reached', failed: 'failed' };
/* Raw engine status (STEP_PASS, PLANNED, ...) shown as words, with the raw value in a tooltip. */

function engineStatus(raw, label) {
  if (!raw) return h('span', { class: 'sub', text: 'no record' });
  return h('span', { class: 'engine-status', title: `engine status: ${raw}`, text: label || engineWord(raw) });
}

function engineWord(raw) {
  const upper = String(raw || '').toUpperCase();
  if (!upper) return 'no record';
  if (/(^|_)PASS(ED)?$|DONE$|^APPROVED$|^COMPLETED?$/.test(upper)) return ENGINE_WORDS.passed;
  if (upper.includes('WAITING')) return ENGINE_WORDS.waiting;
  if (upper.includes('RUNNING') || ['IN_PROGRESS', 'DISPATCHED', 'STARTED', 'SUBMITTED'].includes(upper)) return ENGINE_WORDS.running;
  if (['PLANNED', 'PENDING', 'QUEUED', 'READY', 'NOT_STARTED', 'IDLE', 'NEW', 'SKIPPED'].includes(upper)) return ENGINE_WORDS.not_run;
  if (/FAIL|ERROR|REJECT|REWORK|ABORT|ESCALAT/.test(upper)) return ENGINE_WORDS.failed;
  return upper.toLowerCase().replace(/_/g, ' ');
}

function fmtAgo(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return 'unknown';
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return fmtWhen(iso);
}

function fmtNum(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'unknown';
  return Number(value).toFixed(digits).replace(/\.0+$/, '');
}

function nodeTag(num) { return num ? `N${String(num).padStart(2, '0')}` : ''; }

function lvlClass(level) { return level === 'P?' ? 'Pq' : (level || 'Pq'); }

function oneLine(text, limit = 160) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1)}\u2026` : flat;
}

function plural(count, word, many) { return `${count} ${count === 1 ? word : (many || `${word}s`)}`; }
/* A finding's one-line title; the server sends `title`, this repeats its fallback order for older payloads. */

function findingTitle(finding) {
  const pick = [finding.title, finding.what, finding.description].find((value) => typeof value === 'string' && value.trim());
  if (pick) return pick.replace(/\s+/g, ' ').trim();
  const evidence = typeof finding.evidence === 'string' && /[A-Za-z]{3,}/.test(finding.evidence) ? finding.evidence.replace(/\s+/g, ' ').trim() : '';
  if (evidence) return (/^(.+?[.!?])(?=\s|$)/.exec(evidence) || [null, evidence])[1];
  if (typeof finding.dimension === 'string' && finding.dimension.trim()) return finding.dimension.trim();
  return `Untitled finding (${finding.id || finding.uid || 'no id'})`;
}

/* A finding's short title: its first sentence, without asides in parentheses, cut at a word within 80 characters. */
function shortClause(text, max = 80) {
  const full = String(text || '').replace(/\s+/g, ' ').trim();
  const plain = full.replace(/\s*\([^()]*\)/g, '').replace(/\s+([,.;:])/g, '$1').trim() || full;
  const sentence = (/^(.+?[.!?])(?=\s|$)/.exec(plain) || [null, plain])[1];
  if (sentence.length <= max) return sentence.replace(/[.!?]$/, '');
  const cut = sentence.slice(0, max + 1);
  const space = cut.lastIndexOf(' ');
  return `${cut.slice(0, space > max / 2 ? space : max).replace(/[\s,;:]+$/, '')}\u2026`;
}

/* The owner's answers to each finding, by finding uid. */
function findingAnswers(reactions) {
  const answers = new Map();
  for (const record of reactions || []) {
    if (!record.target || record.target.type !== 'finding' || record.retracted || (record.actor && record.actor !== 'owner')) continue;
    answers.set(record.target.id, [...(answers.get(record.target.id) || []), record]);
  }
  return answers;
}

function splitNeeds(items) {
  const decisions = items.filter((item) => (item.group || 'decision') === 'decision');
  return [decisions, items.filter((item) => (item.group || 'decision') !== 'decision')];
}

function kindIcon(kind) {
  const name = KIND_ICON[kind];
  return name ? h('span', { class: 'kind-icon', title: kind }, icon(name), sr(` ${kind}`)) : null;
}

function definitionList(rows) {
  const dl = h('dl');
  for (const [term, value] of rows) {
    if (value === null || value === undefined || value === '') continue;
    dl.append(h('dt', { text: term }), h('dd', {}, value instanceof Node ? value : String(value)));
  }
  return dl;
}

function textOf(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 1);
}

async function getJSON(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { message = (await response.json()).error || message; } catch { /* keep status text */ }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function postReaction(body) {
  const response = await fetch('/api/reactions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function loadPrefs() {
  try {
    const value = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
    return { lastPid: null, versions: {}, lanes: {}, activity: {}, ...value };
  } catch {
    return { lastPid: null, versions: {}, lanes: {}, activity: {} };
  }
}

const prefs = loadPrefs();

function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* private mode */ }
}

function announce(message) {
  const live = $('#live');
  live.textContent = '';
  window.setTimeout(() => { live.textContent = message; }, 40);
}

function confirmDialog(title, message, confirmLabel) {
  return new Promise((resolve) => {
    const titleId = nextId('confirm');
    const dialog = h('dialog', { class: 'dialog', 'aria-labelledby': titleId },
      h('h2', { id: titleId, text: title }),
      h('p', { text: message }),
      h('div', { class: 'actions' },
        h('button', { type: 'button', class: 'btn', text: 'Cancel', on: { click: () => dialog.close('cancel') } }),
        h('button', { type: 'button', class: 'btn primary', text: confirmLabel, on: { click: () => dialog.close('ok') } })));
    dialog.addEventListener('close', () => { resolve(dialog.returnValue === 'ok'); dialog.remove(); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

function modal(title, body, { wide = false } = {}) {
  const titleId = nextId('modal');
  const dialog = h('dialog', { class: `dialog${wide ? ' wide' : ''}`, 'aria-labelledby': titleId },
    h('div', { class: 'dialog-head' },
      h('h2', { id: titleId, text: title }),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', on: { click: () => dialog.close() } }, icon('close'))),
    body);
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

const OPTION_ICON = { approve: 'check', request_changes: 'cross', agree: 'check', disagree: 'cross', note: 'note', flag: 'flag' };

function cleanTarget(target) {
  const out = {};
  for (const [key, value] of Object.entries(target || {})) {
    if (value !== null && value !== undefined && value !== '') out[key] = value;
  }
  return out;
}

/* One composer for every target: the target chip, kind buttons adapted to the target, quick tags,
   a note and Save. Approve and Request changes ask for confirmation. Until a kind is chosen only
   the kind buttons show, which keeps gate cards short. */

function nodeLabel(id) {
  const match = /^n(\d{2})(?:_|$)/.exec(id || '');
  return match ? `N${match[1]}` : String(id || '');
}

/* One display name per node, everywhere: the engine's short name, with the "and" its full name uses
   ("Objective audience" + "Objective and audience definition" reads "Objective and audience"). */
function nodeDisplay(node) {
  if (!node) return '';
  const short = String(node.short || node.node_short || '').trim();
  const name = String(node.name || node.node_name || '').trim();
  if (!short) return name;
  const words = short.split(/\s+/);
  const joined = name.toLowerCase().match(/(\S+)\s+and\s+(\S+)/);
  if (words.length === 2 && joined) {
    const [w1, w2] = words.map((word) => word.toLowerCase());
    if (joined[1] === w1 && (joined[2] === w2 || `${joined[2]}s` === w2 || joined[2] === `${w2}s`)) return `${words[0]} and ${words[1]}`;
  }
  return short;
}

/* "Objective and audience (N03)" */
function nodeTitle(node) {
  const num = node && (node.num ?? node.node_num);
  const tag = num ? nodeTag(num) : nodeLabel(node && (node.id || node.node));
  const name = nodeDisplay(node);
  return tag && name ? `${name} (${tag})` : (name || tag);
}

function tabs(name, items, activeId, onChange) {
  const base = nextId('tabs');
  const wrap = h('div', { class: 'tabs' });
  const list = h('div', { class: 'tablist', role: 'tablist', 'aria-label': name });
  const panel = h('div', { role: 'tabpanel', id: `${base}-panel`, tabindex: '0' });
  let current = items.find((item) => item.id === activeId) || items[0];
  const buttons = items.map((item) => h('button', {
    type: 'button', role: 'tab', id: `${base}-${item.id}`, 'aria-controls': `${base}-panel`,
    'aria-selected': 'false', tabindex: '-1', 'data-focus-key': `tab:${name}:${item.id}`,
    'aria-label': item.units ? `${item.label}, ${item.units}` : null,
    on: { click: () => activate(item, true), keydown: (event) => onKey(event, item) },
  }, h('span', { class: 'tab-label', text: item.label }),
  item.units ? h('span', { class: 'tab-units', 'aria-hidden': 'true', text: item.units }) : null));

  function activate(item, focus) {
    current = item;
    buttons.forEach((button, index) => {
      const on = items[index] === item;
      button.setAttribute('aria-selected', String(on));
      button.tabIndex = on ? 0 : -1;
    });
    panel.setAttribute('aria-labelledby', `${base}-${item.id}`);
    panel.replaceChildren(item.render());
    if (focus) {
      buttons[items.indexOf(item)].focus();
      buttons[items.indexOf(item)].scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
    onChange?.(item.id);
  }
  function onKey(event, item) {
    const index = items.indexOf(item);
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % items.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length - 1;
    if (next !== null) { event.preventDefault(); activate(items[next], true); }
  }
  list.append(...buttons);
  wrap.append(list, panel);
  activate(current, false);
  wrap.activate = (id) => { const item = items.find((candidate) => candidate.id === id); if (item) activate(item, false); };
  wrap.panel = panel;
  return wrap;
}

function preserveFocus(fn) {
  const active = document.activeElement;
  const key = active?.dataset?.focusKey;
  fn();
  if (key && !(active && active.isConnected)) {
    const replacement = document.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
    if (replacement) replacement.focus({ preventScroll: true });
  }
}

const TYPING = 'input, textarea, select, [contenteditable="true"], [contenteditable=""]';

const INTERACTIVE = 'button, a[href], summary, video, audio, [role="tab"], [role="toolbar"], input, select, textarea, [tabindex]:not([tabindex="-1"])';

function seekTo(t, { quiet = false } = {}) {
  const video = state.video;
  if (!video) return;
  const value = Math.max(0, Number(t) || 0);
  const apply = () => {
    video.currentTime = Number.isFinite(video.duration) ? Math.min(value, video.duration) : value;
  };
  if (video.readyState >= 1) apply(); else video.addEventListener('loadedmetadata', apply, { once: true });
  if (!quiet) announce(`Seeked to ${fmtT(value)}`);
}

function telemetryLine(telemetry) {
  if (!telemetry) return null;
  if (!telemetry.present) {
    return telemetry.ledger_evaluations
      ? h('span', { class: 'sub telemetry-line' }, `telemetry: ${plural(telemetry.ledger_evaluations, 'evaluation event')} in the ledger`)
      : null;
  }
  if (telemetry.ok === true) {
    return h('span', { class: 'telemetry-line tl-ok', title: 'from .vpg/telemetry/summary.json' }, icon('check'), 'telemetry coverage: ok');
  }
  if (telemetry.missing_count !== null && telemetry.missing_count !== undefined) {
    return h('span', { class: 'telemetry-line tl-missing', title: (telemetry.missing || []).join('; ') || 'from .vpg/telemetry/summary.json' },
      icon('warn'), `telemetry coverage: missing ${telemetry.missing_count}`);
  }
  return h('span', { class: 'sub telemetry-line' }, `telemetry: ${plural(telemetry.evaluations || 0, 'evaluation event')}, no summary.json`);
}

function ledgerType(type) {
  return h('span', { title: `ledger event type: ${type || 'unknown'}`, text: LEDGER_LABELS[type] || String(type || 'unknown').replace(/_/g, ' ') });
}

function runCard(run, telemetry) {
  const title = h('h3', { class: 'h3-like', text: 'Engine run' });
  const coverage = telemetryLine(telemetry);
  if (!run) {
    return persistentDetails('run', [title, runPill(null), coverage],
      [h('p', { class: 'empty', text: 'No .vpg/state.json in this job: everything shown comes from builds outside the engine (candidate folders and reviews).' })]);
  }
  const ledger = run.ledger || { count: 0, last: [] };
  const linked = ledger.linked === true ? ' (prev links consistent)' : ledger.linked === false ? ' (prev links broken)' : '';
  const counts = telemetry && telemetry.counts ? Object.entries(telemetry.counts).map(([key, value]) => `${key.replace(/_/g, ' ')} ${value}`).join(', ') : '';
  return persistentDetails('run', [title, runPill(run), coverage], [
    definitionList([
      ['Run id', run.run_id],
      ['Status', engineStatus(run.status)],
      ['Current version', String(run.current_version ?? 'unknown')],
      ['Waiting at', run.waiting_at && run.waiting_at.length ? run.waiting_at.map(nodeLabel).join(', ') : 'none'],
      ['Ledger events', `${ledger.count}${linked}`],
      ['Telemetry', telemetry && telemetry.present
        ? `${telemetry.ok ? 'coverage ok' : `coverage missing ${telemetry.missing_count ?? 'unknown'}`}${counts ? ` (${counts})` : ''}${telemetry.stale ? '; summary is from another run' : ''}${telemetry.skipped_lines ? `; ${plural(telemetry.skipped_lines, 'unreadable line')} skipped` : ''}`
        : 'none in .vpg/telemetry/'],
    ]),
    telemetry && telemetry.missing && telemetry.missing.length
      ? h('details', {}, h('summary', { text: `Missing from telemetry (${telemetry.missing_count})` }), bulletList(telemetry.missing))
      : null,
    ledger.last && ledger.last.length
      ? h('div', { class: 'scroll-x' }, h('table', { class: 'ledger' },
        h('caption', { class: 'sub', text: `Last ${ledger.last.length} ledger events, newest first` }),
        h('thead', {}, h('tr', {}, ['Seq', 'Time', 'Event', 'Node', 'Detail'].map((label) => h('th', { scope: 'col', text: label })))),
        h('tbody', {}, ledger.last.map((event) => h('tr', {},
          h('td', { text: String(event.seq ?? '') }), h('td', {}, timeEl(event.ts)),
          h('td', {}, ledgerType(event.type)), h('td', { text: event.node ? nodeLabel(event.node) : '' }),
          h('td', { class: 'ledger-detail', text: event.detail || '' }))))))
      : null,
  ]);
}

function readmeCard(text) {
  return persistentDetails('readme', [h('h3', { class: 'h3-like', text: 'README' }), h('span', { class: 'sub', text: 'first 40 lines' })],
    [text ? h('pre', { text }) : h('p', { class: 'empty', text: 'No README.md in this job.' })]);
}

function refreshSoon() {
  window.setTimeout(() => { clearTimeout(state.timer); poll(); }, 250);
}

function joinList(value) {
  if (Array.isArray(value)) return value.length ? value.map((item) => textOf(item)).join(', ') : 'none';
  if (value === null || value === undefined || value === '') return 'none';
  return textOf(value);
}

function bulletList(items) {
  if (!Array.isArray(items) || !items.length) return h('p', { class: 'sub', text: 'none' });
  return h('ul', {}, items.map((item) => h('li', { text: textOf(item) })));
}

function fileTable(files) {
  if (!files || !files.length) return h('p', { class: 'empty', text: 'Empty folder.' });
  return h('div', { class: 'scroll-x' }, h('table', {},
    h('thead', {}, h('tr', {}, h('th', { scope: 'col', text: 'Name' }), h('th', { scope: 'col', text: 'Size' }))),
    h('tbody', {}, files.map((file) => h('tr', {},
      h('td', {}, file.url
        ? h('a', { href: file.url, target: '_blank', rel: 'noopener', text: file.name })
        : h('span', { class: 'mono', text: file.dir ? `${file.name}/` : file.name })),
      h('td', { class: 'mono', text: file.dir ? (file.count !== null && file.count !== undefined ? plural(file.count, 'entry', 'entries') : 'folder') : fmtBytes(file.size) }))))));
}

function contextBlock(context) {
  return frag(
    h('h4', { text: 'context.json' }),
    definitionList([
      ['Node', context.node],
      ['Attempt', context.attempt],
      ['Producer command', context.producer && context.producer.command ? h('code', { text: context.producer.command }) : null],
      ...Object.entries(context.controls || {}).map(([key, value]) => [`Control: ${key}`, value]),
      ...Object.entries(context.inputs || {}).map(([key, value]) => [`Input: ${key}`, typeof value === 'number' ? plural(value, 'item') : value]),
    ]));
}

function readableFinding(finding) {
  if (finding.text !== undefined) return h('p', { class: 'reaction-text', text: finding.text });
  return definitionList((finding.fields || []).map(([key, value]) => [key, value]));
}

function gateBlock(gate, { onOpenNode } = {}) {
  const findings = gate.findings || [];
  return h('div', { class: 'review' },
    h('div', { class: 'review-head' },
      h('strong', { class: 'mono', text: gate.name }),
      statusPill(gate.pass ? 'passed' : 'failed', gate.pass ? 'pass' : `fail, ${plural(findings.length, 'finding')}`),
      gate.ok === false ? h('span', { class: 'sub', text: 'ok: false' }) : null,
      onOpenNode ? h('button', { type: 'button', class: 'btn small', on: { click: onOpenNode } }, `Open ${nodeTag(gate.node_num)}`) : null),
    findings.length ? h('ol', {}, findings.map((finding) => h('li', {}, readableFinding(finding)))) : h('p', { class: 'empty', text: 'No findings.' }),
    gate.messages && gate.messages.length
      ? h('details', {}, h('summary', { text: `Messages (${gate.messages.length})` }), h('ul', {}, gate.messages.map((message) => h('li', { text: message }))))
      : null,
    gate.extra && Object.keys(gate.extra).length
      ? h('details', {}, h('summary', { text: 'Gate data' }), definitionList(Object.entries(gate.extra).map(([key, value]) => [key, h('pre', { text: value })])))
      : null);
}

function scoresTable(scores, withReviewer) {
  const hasThreshold = scores.some((score) => score.threshold !== null && score.threshold !== undefined && score.threshold !== '');
  return h('div', { class: 'scroll-x' }, h('table', { class: 'scores' },
    h('thead', {}, h('tr', {},
      withReviewer ? h('th', { scope: 'col', text: 'Reviewer' }) : null,
      h('th', { scope: 'col', text: 'Dimension' }),
      h('th', { scope: 'col', text: 'Score' }),
      hasThreshold ? h('th', { scope: 'col', text: 'Threshold' }) : null,
      h('th', { scope: 'col', text: 'Evidence' }))),
    h('tbody', {}, scores.map((score) => h('tr', {},
      withReviewer ? h('td', { text: score.reviewer_label || score.reviewer || '' }) : null,
      h('td', {}, h('span', { class: 'mono', text: score.dimension }), score.name ? h('div', { class: 'sub', text: score.name }) : null,
        score.kind ? h('div', { class: 'sub', text: score.kind }) : null),
      h('td', { class: 'mono' }, score.score !== null && score.score !== undefined ? String(score.score) : (score.result || 'none'),
        score.score !== null && score.score !== undefined && score.result ? h('div', { class: 'sub', text: score.result }) : null),
      hasThreshold ? h('td', { class: 'mono', text: score.threshold ?? '' }) : null,
      h('td', {}, score.evidence
        ? h('details', {}, h('summary', { text: oneLine(score.evidence, 90) }), h('p', { class: 'reaction-text', text: score.evidence }))
        : h('span', { class: 'sub', text: 'none' })))))));
}

function findingSource(finding) {
  if (finding.gate) return `automatic gate ${finding.gate}`;
  if (finding.source === 'run') return 'engine defect';
  return finding.reviewer_label || finding.reviewer || '';
}

function seenRange(finding) {
  const first = finding.first_seen;
  const last = finding.last_seen;
  if (first === null || first === undefined) return null;
  if (last === null || last === undefined) return `v${first} onwards (still open)`;
  return String(first) === String(last) ? `v${first}` : `v${first} to v${last}`;
}

/* Findings rows: level, id, node, time, one-line what; actions Seek, Agree, Disagree, Note. */

function actionGroup(primary, more, label) {
  const wrap = h('div', { class: 'action-group' });
  if (primary) {
    wrap.append(h('button', {
      type: 'button', class: `btn small${primary.primary ? ' primary' : ''}`, 'aria-label': primary.ariaLabel || null,
      'data-focus-key': primary.focusKey || null, on: { click: primary.onClick },
    }, primary.label));
  }
  if (more && more.length) {
    const menuId = nextId('more');
    const toggle = h('button', {
      type: 'button', class: 'btn small more-toggle', 'aria-expanded': 'false', 'aria-controls': menuId,
      'aria-label': `More actions${label ? ` for ${label}` : ''}`,
    }, 'More');
    const menu = h('div', { class: 'more-menu', id: menuId, hidden: true });
    // Fixed positioning keeps the menu visible inside scrolling tables and the drawer.
    const place = () => {
      if (!toggle.isConnected) { close(false); return; }
      const box = toggle.getBoundingClientRect();
      const width = menu.offsetWidth;
      const height = menu.offsetHeight;
      const below = box.bottom + 4;
      const top = below + height > window.innerHeight - 8 && box.top - 4 - height > 8 ? box.top - 4 - height : below;
      const left = Math.max(8, Math.min(box.right - width, window.innerWidth - width - 8));
      menu.style.setProperty('top', `${Math.round(top)}px`);
      menu.style.setProperty('left', `${Math.round(left)}px`);
    };
    const close = (focus) => {
      if (menu.hidden) return;
      menu.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
      if (focus) toggle.focus();
    };
    for (const item of more) {
      menu.append(h('button', {
        type: 'button', class: 'more-item', 'aria-label': item.ariaLabel || null,
        on: { click: () => { close(false); item.onClick(); } },
      }, item.label));
    }
    toggle.addEventListener('click', () => {
      if (!menu.hidden) { close(false); return; }
      menu.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      place();
      window.addEventListener('scroll', place, true);
      window.addEventListener('resize', place);
      const first = menu.querySelector('button');
      if (first) first.focus({ preventScroll: true });
    });
    menu.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); }
    });
    wrap.addEventListener('focusout', (event) => { if (!wrap.contains(event.relatedTarget)) close(false); });
    wrap.append(toggle, menu);
  }
  return wrap;
}

const DECIDED_WORDS = 'Decided by you \u00b7 engine waits';

function decidedMark(record) {
  return record && record.kind === 'approve' ? 'decided_ok' : 'decided_changes';
}

/* The owner's verdict on a version: "You requested changes · picked up for v29", or "Changes · v29" in the rail. */
function ownerWords(owner, { compact = false } = {}) {
  if (!owner) return '';
  const approve = owner.kind === 'approve';
  if (compact) {
    const v = owner.resolved_in || owner.picked_for;
    return `${approve ? 'Approved' : 'Changes'}${v ? ` \u00b7 ${v}` : ''}`;
  }
  const where = owner.state === 'resolved' ? `addressed in ${owner.resolved_in}`
    : owner.state === 'picked_up' ? `picked up${owner.picked_for ? ` for ${owner.picked_for}` : ''}`
      : owner.reopened ? 'reopened, waiting for the producer' : 'waiting for the producer';
  return `${approve ? 'You approved' : 'You requested changes'} \u00b7 ${where}`;
}

function ownerMark(owner, { compact = false } = {}) {
  return h('span', { class: `owner-mark${compact ? ' compact' : ''}`, title: compact ? ownerWords(owner) : null },
    h('span', { class: `mark m-${decidedMark(owner)}`, 'aria-hidden': 'true' }, markGlyph(decidedMark(owner))),
    h('span', { class: 'owner-words', text: ownerWords(owner, { compact }) }));
}

/* "You requested changes Oct 27, 4:04 PM; the producer picked it up for v29." */
function ownerClause(owner) {
  const verb = owner.kind === 'approve' ? 'You approved' : 'You requested changes';
  const tail = owner.state === 'resolved' ? `the producer addressed it in ${owner.resolved_in}`
    : owner.state === 'picked_up' ? `the producer picked it up${owner.picked_for ? ` for ${owner.picked_for}` : ''}`
      : owner.reopened ? 'you reopened it, and it is waiting for the producer' : 'it is waiting for the producer';
  return `${verb} ${fmtWhen(owner.ts)}; ${tail}.`;
}

/* Needs you means only the owner: a gate he has decided reads as his, even while the engine waits. */
function needsOwner(gate) {
  return canDecide(gate) && gate.state !== 'recorded';
}

function gateStatus(gate) {
  if (gate.superseded_by) return 'superseded';
  switch (gate.state) {
    case 'waiting': return 'waiting';
    case 'open': return gate.gates_passed ? 'waiting' : 'not_run';
    case 'recorded': return 'decided';
    case 'engine_decided': return /^(approv|accept|pass)/i.test((gate.engine_decision || {}).decision || '') ? 'passed' : 'failed';
    case 'not_reached': return 'not_run';
    default: return gate.state in STATUS ? gate.state : 'unknown';
  }
}

function gateStateLabel(gate) {
  if (gate.superseded_by) return `Superseded by ${gate.superseded_by}`;
  switch (gate.state) {
    case 'waiting': return gate.provisional_expired ? 'Needs you again: the provisional approval expired' : 'Needs you';
    case 'open': return gate.gates_passed ? 'Needs you: checks passed' : 'Checks not passed yet';
    case 'recorded': return gate.engine_waits ? DECIDED_WORDS : MARK_WORDS[decidedMark(gate.decision_record)];
    case 'engine_decided': return `${(gate.engine_decision || {}).decision || 'decided'} (engine record)`;
    case 'not_reached': return 'Not reached';
    default: return STATUS[gate.state] ? STATUS[gate.state].label : String(gate.state);
  }
}

function gateLabelText(gate) {
  if (gate.superseded_by) return `${gate.superseded_by} is the latest fully reviewed version, so its release decision is the one that waits.`;
  if (gate.state === 'engine_decided' || gate.state === 'recorded') return null;
  if (gate.state === 'open') return gate.gates_passed ? null : `Not passed yet: ${gate.gates_reason || 'see the gate under Pipeline'}.`;
  return gate.label;
}

function canDecide(gate) {
  return Boolean(gate && !gate.superseded_by && gate.actions && gate.actions.includes('approve'));
}

/* One status source per version: a release gate that is not the live decision is superseded (server field, or the
   live release decision of this film when the server predates it). */
function annotateGate(gate, version, decisions) {
  if (!gate || !gate.release || gate.superseded_by !== undefined) return gate;
  const live = (decisions || []).find((d) => d.kind === 'release');
  const open = ['open', 'waiting'].includes(gate.state);
  gate.superseded_by = open && live && live.version !== version && compareVersions(live.version, version) > 0 ? live.version : null;
  return gate;
}

/* A node's reason in words; a superseded release gate explains itself instead of inviting a decision. */
function nodeReason(node) {
  const gate = node.gate_state;
  if (gate && gate.superseded_by) return gateLabelText(gate);
  return plainWords(node.status_reason);
}

/* What a node truly is, the same words in the map, the gates list and the drawer. */
function nodeTruth(node) {
  const gate = node.gate_state;
  if (gate && gate.superseded_by) return { status: 'superseded', label: `Superseded by ${gate.superseded_by}`, mark: 'superseded' };
  if (node.status === 'decided') return { status: 'decided', label: DECIDED_WORDS, mark: decidedMark(gate && gate.decision_record) };
  // A status that is the owner's own recorded verdict reads as his, in the same words and glyph as the gates list.
  const own = gate && gate.state === 'recorded' && gate.decision_record ? decidedMark(gate.decision_record) : null;
  if (own && node.status === (own === 'decided_ok' ? 'passed' : 'failed')) return { status: node.status, label: MARK_WORDS[own], mark: own };
  if (node.provenance === 'none' && ['passed', 'warnings'].includes(node.status)) return { status: 'unrecorded', label: UNRECORDED_WORDS, mark: 'passed' };
  const meta = STATUS[node.status] || STATUS.unknown;
  return { status: node.status in STATUS ? node.status : 'unknown', label: meta.label, mark: meta.mark };
}

function provisionalBadge(engine) {
  if (!engine || !engine.provisional) return null;
  if (!engine.expires_at) return h('span', { class: 'badge prov', text: 'provisional (assumed)' });
  return h('span', { class: `badge prov${engine.expired ? ' expired' : ''}` },
    engine.expired ? icon('warn') : null, engine.expired ? 'provisional (assumed), expired ' : 'provisional (assumed), expires ', timeEl(engine.expires_at));
}

function recordedDecision(gate, { compact = false } = {}) {
  const parts = [];
  const engine = gate.engine_decision;
  if (engine && compact) {
    // One line in the decision bar; the actor text stays verbatim, in full, inside the disclosure.
    const actor = engine.actor || 'unknown actor';
    const short = actor.length > 48 ? `${actor.slice(0, 46)}\u2026` : actor;
    const summary = h('summary', {},
      h('span', { class: 'label', text: 'Recorded decision ' }), `${engine.decision || 'decided'} by `,
      h('span', { class: 'actor', text: short }), engine.at ? [', ', timeEl(engine.at)] : null,
      provisionalBadge(engine) ? [' ', provisionalBadge(engine)] : null);
    if (short === actor && !engine.reason) {
      parts.push(h('p', { class: 'recorded' }, [...summary.childNodes]));
    } else {
      parts.push(h('details', { class: 'recorded' }, summary,
        short !== actor ? h('p', {}, 'Actor, verbatim: ', h('span', { class: 'actor', text: actor })) : null,
        engine.reason ? h('p', { class: 'reaction-text', text: `Reason: ${engine.reason}` }) : null));
    }
  } else if (engine) {
    parts.push(h('p', { class: 'recorded' },
      h('span', { class: 'label', text: 'Recorded decision ' }),
      `${engine.decision || 'decided'} by `, h('span', { class: 'actor', text: engine.actor || 'unknown actor' }),
      engine.at ? [', ', timeEl(engine.at)] : null,
      engine.count > 1 ? h('span', { class: 'sub', text: ` (latest of ${engine.count})` }) : null,
      provisionalBadge(engine) ? [' ', provisionalBadge(engine)] : null));
    if (engine.reason) parts.push(h('details', {}, h('summary', { text: 'Reason recorded with the decision' }), h('p', { class: 'reaction-text', text: engine.reason })));
  }
  const owner = gate.decision_record;
  if (owner) {
    const record = { ...owner, actor: 'owner', pid: owner.pid || gate.pid || (state.room && state.room.kind === 'job' ? state.room.key : null) };
    parts.push(h('div', { class: 'recorded owner-decision' },
      h('p', {}, h('span', { class: 'label', text: 'Your decision ' }), `${KIND_LABEL[owner.kind] || owner.kind}, `, timeEl(owner.ts), ' ', stateChip(record)),
      owner.text ? h('p', { class: 'reaction-text', text: owner.text }) : null,
      threadBlock(record)));
  }
  return parts;
}

function trapFocus(event) {
  if (event.key !== 'Tab') return;
  const focusables = $$('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, audio[controls], video[controls], [tabindex]:not([tabindex="-1"])', event.currentTarget)
    .filter((el) => el.getClientRects().length > 0);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const outside = !focusables.includes(document.activeElement);
  if (event.shiftKey && (document.activeElement === first || outside)) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function provenanceText(node) {
  const provenance = node.provenance;
  if (provenance === 'governed') return [h('strong', { text: 'Governed run' }), ': the engine ledger recorded this node.'];
  if (provenance === 'direct' && node.direct_recorded) {
    const run = (node.direct_runs || [])[0] || {};
    const who = [run.id, run.label ? `for ${run.label}` : null, run.actor ? `by ${run.actor}` : null].filter(Boolean).join(' ');
    const parts = [];
    if (who) parts.push(who);
    if (run.at) parts.push(timeEl(run.at));
    return [h('strong', { text: 'Direct run (recorded in ledger)' }),
      ': built outside the governed path; the engine recorded the run',
      parts.length ? [' (', parts.flatMap((part, index) => (index ? [', ', part] : [part])), ')'] : null,
      '. No gate or review judged its outputs.'];
  }
  if (provenance === 'direct') {
    return [h('strong', { text: 'Direct run (not in engine ledger)' }),
      ': evidence comes from candidate folders and reviews that the governed run never reached.'];
  }
  return [h('strong', { text: 'No evidence' }), ' for this node and version.'];
}

function previewsBlock(data) {
  const previews = data.previews || {};
  const kids = [];
  if (previews.poster) {
    kids.push(h('figure', { class: 'preview-poster' }, h('img', { src: previews.poster, alt: `Poster for ${data.version}` }), h('figcaption', { class: 'sub', text: 'Poster' })));
  } else if (previews.video) {
    kids.push(h('figure', { class: 'preview-poster' },
      h('video', { src: `${previews.video}#t=0.1`, preload: 'metadata', muted: true, playsinline: true, controls: true, 'aria-label': `First frame of the ${data.version} master` }),
      h('figcaption', { class: 'sub', text: 'No poster file; showing the first frame of the master' })));
  }
  if (previews.keyframes && previews.keyframes.length) {
    kids.push(h('ol', { class: 'filmstrip small', 'aria-label': 'Key frames' }, previews.keyframes.map((frame) => h('li', {},
      h('a', { href: links.version(data.pid, data.version, frame.t !== null && frame.t !== undefined ? { t: frame.t } : null), 'aria-label': `Open the version at ${fmtT(frame.t)}: ${frame.label}` },
        h('img', { src: frame.url, alt: '', loading: 'lazy', decoding: 'async' }),
        h('span', { class: 'mono', text: fmtT(frame.t) }))))));
  }
  for (const track of previews.audio || []) {
    kids.push(h('div', { class: 'audio-row' }, h('span', { class: 'mono', text: track.label }),
      h('audio', { src: track.url, controls: true, preload: 'none', 'aria-label': `Play ${track.label}` })));
  }
  return kids.length ? h('section', { class: 'previews', 'aria-label': 'Previews' }, h('h3', { text: 'Previews' }), kids) : null;
}

function evidenceTab(data) {
  const kids = [previewsBlock(data)];
  const governed = data.governed;
  kids.push(h('h3', { text: 'Governed run' }));
  if (!governed) {
    kids.push(h('p', { class: 'empty', text: 'No governed run record for this node.' }));
  } else if (!governed.reached) {
    kids.push(h('p', { class: 'empty' }, "The governed run hasn't reached this node (engine status ", engineStatus(governed.status, governed.status_label), ')'));
  } else {
    kids.push(definitionList([
      ['Status', engineStatus(governed.status, governed.status_label)], ['Attempts', governed.attempts], ['Outcome', governed.outcome ?? 'none'],
      ['Started', governed.started_at ? timeEl(governed.started_at) : null], ['Finished', governed.finished_at ? timeEl(governed.finished_at) : null],
      ['Executions', governed.executions], ['Failures', governed.failures], ['Fail streak', governed.fail_streak], ['Task packet', governed.task_packet],
    ]));
    if (governed.task_packet_text) {
      kids.push(h('details', {}, h('summary', { text: `Task packet text${governed.task_packet_truncated ? ' (truncated)' : ''}` }),
        h('pre', { text: governed.task_packet_text })));
    }
    if (governed.work_folder) {
      kids.push(h('details', {}, h('summary', { text: `${governed.work_folder} (${plural(governed.work_files.length, 'entry', 'entries')})` }),
        fileTable(governed.work_files)));
    }
  }
  const evidence = data.evidence;
  const recorded = Boolean(data.node.direct_recorded);
  kids.push(h('h3', { text: 'Files for this version' }));
  if (evidence.folder) {
    if (evidence.provenance !== 'governed') {
      kids.push(h('p', { class: 'notice break', text: `Direct run (${recorded ? 'recorded in ledger' : 'not in engine ledger'}): ${evidence.folder}` }));
    } else {
      kids.push(h('p', { class: 'sub mono break', text: evidence.folder }));
    }
    kids.push(fileTable(evidence.files));
    if (evidence.context) kids.push(contextBlock(evidence.context));
  } else {
    kids.push(h('p', { class: 'empty', text: data.version ? `No work files from ${nodeTag(data.node.num)} for ${data.version}.` : 'No version selected.' }));
  }
  if (data.node.direct_runs && data.node.direct_runs.length) {
    kids.push(h('h3', { text: 'Direct runs recorded by the engine' }), h('ul', { class: 'direct-runs' }, data.node.direct_runs.map((run) => h('li', {},
      h('span', { class: 'mono', text: run.id || 'direct run' }), run.label ? ` for ${run.label}` : '', run.actor ? ` by ${run.actor}` : '',
      run.at ? [', ', timeEl(run.at)] : '', run.reason ? h('div', { class: 'sub', text: `Reason: ${run.reason}` }) : null,
      run.outputs && run.outputs.length ? h('div', { class: 'sub', text: `Outputs: ${run.outputs.join(', ')}` }) : null,
      h('div', { class: 'sub', text: `Source: ${run.source === 'ledger' ? 'ledger direct_run event' : 'state history.direct_runs'}` })))));
  }
  if (evidence.review_files.length || evidence.qa_file) {
    kids.push(h('h3', { text: 'Review and QA files' }), h('ul', {},
      evidence.review_files.map((file) => h('li', {}, h('a', { href: file.url, target: '_blank', rel: 'noopener', text: file.name }), ` (${file.reviewer})`)),
      evidence.qa_file ? h('li', {}, h('a', { href: evidence.qa_file.url, target: '_blank', rel: 'noopener', text: evidence.qa_file.name })) : null));
  }
  return frag(kids);
}

function checksTab(data) {
  return frag(
    data.gates.length ? h('div', {}, data.gates.map((gate) => gateBlock(gate)))
      : h('p', { class: 'empty', text: 'No automatic gates map to this node for this version.' }),
    telemetrySection(data));
}

const TELEMETRY_LABEL_CLASS = { pass: 'pass', passed: 'pass', approved: 'pass', ok: 'pass', fail: 'rework', failed: 'rework', rework: 'rework', blocked: 'rework', rejected: 'rework' };

function evidenceRow(item, data, eventVersion) {
  const version = eventVersion || data.version;
  const hasTime = item.t0 !== null && item.t0 !== undefined;
  const parts = [h('span', { class: 'label ev-kind', text: String(item.kind || 'evidence').replace(/_/g, ' ') })];
  if (hasTime && version) {
    parts.push(h('button', {
      type: 'button', class: 'finding-time seek', title: `Seek to this moment in ${version}`,
      'aria-label': `Seek to ${fmtT(item.t0)} in ${version}: ${oneLine(item.text || item.kind, 80)}`,
      on: { click: () => go(links.version(data.pid, version, { t: item.t0 })) },
    }, icon('seek'), fmtT(item.t0)));
  } else if (hasTime) {
    parts.push(h('span', { class: 'mono', text: fmtT(item.t0) }));
  }
  if (item.frame !== null && item.frame !== undefined) parts.push(h('span', { class: 'mono', text: `frame ${item.frame}` }));
  if (item.metric) parts.push(h('span', { class: 'mono', text: `${item.metric} = ${item.value ?? 'n/a'}${item.unit ? ` ${item.unit}` : ''}` }));
  if (item.text) parts.push(h('span', { class: 'ev-text', text: oneLine(item.text, 400) }));
  if (item.path) {
    parts.push(item.url
      ? h('a', { href: item.url, target: '_blank', rel: 'noopener', class: 'mono break', text: item.key ? `${item.key}: ${item.path}` : item.path })
      : h('span', { class: 'mono break', title: 'not a servable file for this viewer', text: item.key ? `${item.key}: ${item.path}` : item.path }));
  }
  if (item.sha256) parts.push(h('span', { class: 'sub mono', text: `sha256 ${item.sha256}` }));
  const fields = (item.fields || []).map(([key, value]) => `${key}: ${value}`).join('; ');
  if (fields) parts.push(h('span', { class: 'sub', text: fields }));
  return h('li', { class: 'ev-item' }, parts);
}

function telemetryEvent(ev, data) {
  const labelClass = TELEMETRY_LABEL_CLASS[String(ev.label || '').toLowerCase()] || 'unknown';
  const result = [ev.label, ev.score !== null && ev.score !== undefined ? fmtNum(ev.score, 2) : null].filter((part) => part !== null && part !== undefined && part !== '').join(' \u00b7 ');
  return h('li', { class: 'tel-event' },
    h('div', { class: 'tel-head' },
      h('span', { class: 'mono tel-name', text: ev.short || ev.name }),
      result ? h('span', { class: `verdict v-${labelClass}` }, icon(VERDICT[labelClass] ? VERDICT[labelClass].icon : 'question'), result) : null,
      ev.evidence_tag ? h('span', { class: `badge tag-${ev.evidence_tag}`, title: 'evidence tag', text: ev.evidence_tag }) : null,
      ev.attempt !== null && ev.attempt !== undefined ? h('span', { class: 'sub', text: `attempt ${ev.attempt}` }) : null,
      ev.evaluator ? h('span', { class: 'sub', text: ev.evaluator }) : null,
      ev.time ? timeEl(ev.time) : null),
    ev.explanation ? h('p', { class: 'reaction-text tel-expl', text: ev.explanation }) : null,
    ev.evidence && ev.evidence.length
      ? h('ul', { class: 'ev-list', 'aria-label': `Evidence for ${ev.name}` }, ev.evidence.map((item) => evidenceRow(item, data, ev.version)),
        ev.evidence_more ? h('li', { class: 'sub', text: `${ev.evidence_more} more not shown` }) : null)
      : h('p', { class: 'sub', text: 'No evidence recorded with this event.' }),
    ev.source ? h('p', { class: 'sub', text: `Source: ${ev.source}` }) : null);
}

function telemetrySection(data) {
  const tel = data.telemetry || {};
  const head = h('h3', { class: 'telemetry-h', text: 'Telemetry' });
  if (!tel.present) {
    return h('section', { class: 'telemetry', 'aria-label': 'Telemetry' }, head,
      h('p', { class: 'empty', text: 'No engine telemetry for this job: .vpg/telemetry/ is absent and the ledger has no evaluation events.' }));
  }
  const evaluations = tel.evaluations || [];
  const spans = tel.spans || [];
  const source = tel.source === 'ledger' ? 'evaluation events written to the ledger' : '.vpg/telemetry/ (derived by the engine)';
  return h('section', { class: 'telemetry', 'aria-label': 'Telemetry' }, head,
    h('p', { class: 'sub' }, `${plural(tel.total || 0, 'evaluation event')} for this node from ${source}`,
      tel.total > evaluations.length ? `, newest ${evaluations.length} shown` : '',
      tel.skipped_lines ? `; ${plural(tel.skipped_lines, 'unreadable line')} skipped` : '', '.'),
    evaluations.length ? h('ol', { class: 'tel-events' }, evaluations.map((ev) => telemetryEvent(ev, data)))
      : h('p', { class: 'empty', text: 'No evaluation events for this node.' }),
    spans.length
      ? h('details', {}, h('summary', { text: `Spans (${tel.spans_total || spans.length})` }),
        h('div', { class: 'scroll-x' }, h('table', {}, tableHead(['Span', 'Attempt', 'Status', 'Outcome', 'Duration']),
          h('tbody', {}, spans.map((span) => h('tr', {},
            h('td', { class: 'mono', text: span.name }), h('td', { text: span.attempt ?? '' }), h('td', { text: span.status || '' }),
            h('td', { text: span.outcome || '' }), h('td', { class: 'mono', text: span.duration_s !== null && span.duration_s !== undefined ? `${fmtNum(span.duration_s)} s` : '' })))))))
      : null);
}

function reviewSummaryLine(review) {
  const parts = [review.verdict || 'no verdict'];
  if (review.worst_level) parts.push(`worst ${review.worst_level}`);
  if (review.score_count !== undefined) parts.push(plural(review.score_count, 'score'));
  parts.push(plural(review.defect_count || 0, 'finding'));
  return parts.join(', ');
}

function nodeReviewsTab(data) {
  const kids = [];
  if (data.reviews.length) {
    kids.push(h('h3', { text: 'Reviewer verdicts' }), h('ul', { class: 'review-list' }, data.reviews.map((review) => h('li', {},
      verdictChip(review.verdict_class, review.verdict || undefined, review.worst_level, review.defect_count), ' ',
      h('strong', { text: review.label }), h('span', { class: 'sub', text: ` ${reviewSummaryLine(review)}` }),
      review.governed ? h('span', { class: 'badge', text: 'governed' }) : null,
      review.summary ? h('p', { class: 'sub', text: oneLine(review.summary, 500) }) : null,
      review.pending_human && review.pending_human.length ? h('p', { class: 'sub', text: `Waiting on a human: ${review.pending_human.join('; ')}` }) : null))));
  } else {
    kids.push(h('p', { class: 'sub', text: 'No reviewer verdict maps to this node for this version.' }));
  }
  const reacted = new Set(data.reactions.filter((r) => r.target && r.target.type === 'finding').map((r) => r.target.id));
  kids.push(h('h3', { class: 'after-summary', text: `Findings (${data.defects.length})` }));
  kids.push(data.defects.length
    ? findingsList(data.defects, { pid: data.pid, version: data.version, fromDrawer: true, reacted, answers: findingAnswers(data.reactions) })
    : h('p', { class: 'empty', text: 'No findings map to this node.' }));
  kids.push(h('h3', { text: `Scores (${data.scores.length})` }));
  kids.push(data.scores.length ? scoresTable(data.scores, true) : h('p', { class: 'empty', text: 'No scores map to this node.' }));
  return frag(kids);
}

function contractTab(contract) {
  if (!contract) return h('p', { class: 'empty', text: 'No contract.' });
  const outputs = Array.isArray(contract.outputs) && contract.outputs.length
    ? h('div', { class: 'scroll-x' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Key', 'Kind', 'Schema', 'Description'].map((label) => h('th', { scope: 'col', text: label })))),
      h('tbody', {}, contract.outputs.map((output) => h('tr', {},
        h('td', { class: 'mono', text: textOf(output && output.key) }), h('td', { text: textOf(output && output.kind) }),
        h('td', { class: 'mono', text: textOf(output && output.schema) }), h('td', { text: textOf(output && output.description) }))))))
    : h('p', { class: 'sub', text: 'none' });
  const completion = Array.isArray(contract.completion) && contract.completion.length
    ? h('ul', {}, contract.completion.map((item) => h('li', {},
      h('span', { class: 'mono', text: `${textOf(item && item.type)} ${textOf(item && (item.key || item.fn))}`.trim() }),
      item && item.description ? ` ${item.description}` : '')))
    : h('p', { class: 'sub', text: 'none' });
  const controls = contract.controls || {};
  const review = contract.human_review || {};
  const edges = contract.edges || { in: [], out: [] };
  const edgeList = (items) => (items.length
    ? h('ul', {}, items.map((edge) => h('li', { class: 'mono', text: `${edge.id}: ${edge.from} \u2192 ${edge.to} (${edge.kind}${edge.when ? `, when ${edge.when}` : ''})` })))
    : h('p', { class: 'sub', text: 'none' }));
  return frag(
    definitionList([['Purpose', contract.purpose], ['Agent', contract.agent], ['Prompt', contract.prompt], ['Engine gate', contract.gate]]),
    h('h3', { text: 'Inputs' }), bulletList(contract.inputs),
    h('h3', { text: 'Outputs' }), outputs,
    h('h3', { text: 'Completion checks' }), completion,
    h('h3', { text: 'Rubric' }), bulletList(contract.rubric),
    h('h3', { text: 'Failure conditions' }), bulletList(contract.failure_conditions),
    h('h3', { text: 'Retry and escalation' }), definitionList([
      ['Max attempts', contract.retry && contract.retry.max_attempts], ['Retry behaviour', contract.retry && contract.retry.behavior],
      ['Escalate after', contract.escalation && contract.escalation.after], ['Escalate to', contract.escalation && contract.escalation.to],
      ['Report', contract.escalation && contract.escalation.report],
    ]),
    h('h3', { text: 'Controls' }), definitionList([
      ['Read', joinList(controls.read)], ['Write', joinList(controls.write)], ['Locks on approval', joinList(controls.lock_on_approval)],
    ]),
    h('h3', { text: 'Human review' }), definitionList([
      ['Required', review.required === true ? 'yes' : 'no'], ['Decision', review.decision], ['Approver', review.approver],
      ['Outcomes', review.outcomes ? joinList(review.outcomes) : null],
    ]),
    h('h3', { text: 'Edges' }), h('p', { class: 'sub', text: 'Incoming' }), edgeList(edges.in || []),
    h('p', { class: 'sub', text: 'Outgoing' }), edgeList(edges.out || []),
    contract.instructions ? h('details', {}, h('summary', { text: 'Instructions' }), h('p', { class: 'reaction-text', text: textOf(contract.instructions) })) : null,
    contract.context ? h('details', {}, h('summary', { text: 'Context' }), bulletList(contract.context)) : null);
}

function nodeReactionsTab(data) {
  const live = liveReactions(data.reactions, 'No reactions on this node yet.');
  const box = composer({
    pid: data.pid, version: data.version, set: 'node',
    target: cleanTarget({ type: 'node', id: data.node.id, version: data.version }),
    label: `${nodeTitle(data.node)}${data.version ? ` on ${data.version}` : ''}`,
    onPending: live.pending,
    onSaved: (record, pending) => { live.saved(record, pending); refreshSoon(); },
    onFailed: live.failed,
  });
  return frag(box, h('h3', { text: 'Reactions on this node' }), live.el);
}

const CAVEAT_TEXT = "Automated checks find defects; they don't judge taste or regressions against earlier versions.";

function buildVideo(data, label) {
  const video = h('video', {
    controls: true, preload: 'metadata', playsinline: true, poster: data.media.poster || null,
    'aria-label': label || `Master film for ${data.version}`,
  });
  if (data.media.master) video.src = data.media.poster ? data.media.master : `${data.media.master}#t=0.1`;
  if (data.media.vtt) {
    video.append(h('track', { kind: 'captions', srclang: 'en', label: 'English', src: data.media.vtt, default: true }));
    video.addEventListener('loadedmetadata', () => { for (const track of video.textTracks) track.mode = 'showing'; });
  }
  return video;
}

function noMedia(data) {
  if (data.media.poster) return h('img', { src: data.media.poster, alt: `Poster for ${data.version}; this version has no master file` });
  return h('p', { class: 'no-media', text: data.source === 'governed' ? 'The master file for this governed version is no longer on disk.' : 'No master.mp4 for this version yet.' });
}

function timelineDuration(view) {
  const { data, video } = view;
  if (data.duration_s) return data.duration_s;
  if (video && Number.isFinite(video.duration) && video.duration > 0) return video.duration;
  const times = [
    ...data.findings.map((f) => f.t0), ...data.keyframes.map((k) => k.t), ...data.captions.map((c) => c.end),
  ].filter((t) => t !== null && t !== undefined);
  return Math.max(60, ...times);
}

function rovingKeys(event, items) {
  const index = items.indexOf(document.activeElement);
  if (index < 0) return;
  let next = null;
  if (event.key === 'ArrowRight') next = Math.min(items.length - 1, index + 1);
  else if (event.key === 'ArrowLeft') next = Math.max(0, index - 1);
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = items.length - 1;
  if (next === null) return;
  event.preventDefault();
  items[index].tabIndex = -1;
  items[next].tabIndex = 0;
  items[next].focus();
}

function nodeName(nodeId) {
  const row = (state.job && state.job.graph || []).find((node) => node.id === nodeId);
  return row ? nodeTitle(row) : nodeLabel(nodeId);
}

function versionReviewsTab(data) {
  if (!data.reviews.length) return h('p', { class: 'empty', text: 'No reviews for this version.' });
  return frag(data.reviews.map((review) => h('article', { class: 'review' },
    h('div', { class: 'review-head' },
      h('h3', { text: review.label }),
      verdictChip(review.verdict_class, review.verdict || undefined, review.worst_level, review.defect_count),
      h('span', { class: 'sub', text: `${plural(review.scores.length, 'score')}, ${plural(review.defect_count, 'finding')}` }),
      h('button', { type: 'button', class: 'btn small', on: { click: () => openNode(review.node) } }, `Open ${nodeTag(review.node_num)} in Pipeline`),
      review.url ? h('a', { href: review.url, target: '_blank', rel: 'noopener', class: 'sub', text: review.file }) : h('span', { class: 'badge', text: 'engine run' })),
    review.summary ? h('p', { class: 'reaction-text', text: review.summary }) : null,
    review.pending_human && review.pending_human.length ? h('p', { class: 'sub', text: `Waiting on a human: ${review.pending_human.join('; ')}` }) : null,
    h('details', {}, h('summary', { text: `Scores (${review.scores.length})` }),
      review.scores.length ? scoresTable(review.scores, false) : h('p', { class: 'empty', text: 'No scores.' })),
    review.defects.length
      ? h('details', {}, h('summary', { text: `Findings (${review.defects.length})` }), findingsList(review.defects, { pid: data.pid, version: data.version }))
      : null,
    review.tool_findings && review.tool_findings.length
      ? h('details', {}, h('summary', { text: `Tool findings (${review.tool_findings.length})` }), h('ol', {}, review.tool_findings.map((f) => h('li', {}, readableFinding(f)))))
      : null,
    review.carried.length
      ? h('details', {}, h('summary', { text: `Carried over from earlier rounds (${review.carried.length})` }),
        h('ul', { class: 'carried' }, review.carried.map((item) => h('li', {},
          h('span', { class: 'mono', text: `${item.from} ${item.id}` }), item.status ? `: ${item.status}` : '',
          item.what ? h('div', { class: 'sub', text: item.what }) : null,
          item.evidence ? h('div', { class: 'sub', text: oneLine(item.evidence, 300) }) : null))))
      : null)));
}

function notesTab(data) {
  if (!data.notes.length) return h('p', { class: 'empty', text: `No work/film-${data.version}/*.md notes.` });
  return frag(data.notes.map((note) => h('details', { open: data.notes.length <= 2 },
    h('summary', {}, h('span', { class: 'mono', text: note.name }), note.truncated ? ' (first 200 KB)' : ''),
    h('pre', { text: note.text }))));
}

function tableHead(labels) {
  return h('thead', {}, h('tr', {}, labels.map((label) => h('th', { scope: 'col', text: label }))));
}

const METRIC_ROWS = [
  ['duration_s', 'Duration', fmtDuration],
  ['integrated_lufs', 'Integrated loudness (LUFS)', (v) => fmtNum(v)],
  ['true_peak_dbtp', 'True peak (dBTP)', (v) => fmtNum(v)],
  ['bytes', 'File size', fmtBytes],
  ['voice_over_music_median', 'Voice over music, median (LU)', (v) => fmtNum(v, 2)],
  ['voice_over_music_p10', 'Voice over music, p10 (LU)', (v) => fmtNum(v, 2)],
  ['bpm', 'Tempo (bpm)', (v) => fmtNum(v)],
];

function deltaText(key, delta) {
  if (delta === null || delta === undefined) return 'n/a';
  if (delta === 0) return 'no change';
  if (key === 'bytes') return `${delta < 0 ? '-' : '+'}${fmtBytes(Math.abs(delta))}`;
  if (key === 'duration_s') return `${delta > 0 ? '+' : ''}${fmtNum(delta, 1)} s`;
  return `${delta > 0 ? '+' : ''}${fmtNum(delta, 2)}`;
}

function levelsText(levels) {
  const parts = ['P1', 'P2', 'P3', 'P4', 'P?'].filter((level) => levels && levels[level]).map((level) => `${levels[level]} ${level}`);
  return parts.length ? parts.join(', ') : 'none';
}

function polarityChip(change) {
  const meta = POLARITY[change.polarity] || POLARITY['n/a'];
  return h('span', { class: `pol ${meta.cls}` }, icon(meta.icon), meta.label);
}

const CHANGE_FORMAT = {
  duration_s: (v) => fmtDuration(v),
  integrated_lufs: (v) => fmtNum(v),
  voice_over_music_p10: (v) => fmtNum(v, 2),
  open_defects: (v) => levelsText(v),
  verdicts_passing: (v, change) => `${v ?? 0} of ${change.total ?? 0}`,
  findings: (v) => (v === null || v === undefined ? 'unknown' : String(v)),
};

function changeDelta(change) {
  if (change.key === 'open_defects' || change.delta === null || change.delta === undefined) return null;
  if (change.key === 'verdicts_passing' || change.key === 'findings') return change.delta === 0 ? 'no change' : `${change.delta > 0 ? '+' : ''}${change.delta}`;
  return deltaText(change.key, change.delta);
}

function basisText(basis) {
  return basis && basis.text ? basis.text : 'not stated';
}

/* A metric measured on a different basis on A and B: the values and polarity stay, the chip says why to doubt them. */

function basisChip(basis) {
  if (!basis) return null;
  const a = basisText(basis.a);
  const b = basisText(basis.b);
  const where = [basis.a && basis.a.path, basis.b && basis.b.path].filter(Boolean)[0];
  return h('span', { class: 'basis-note' },
    h('span', { class: 'pol pol-basis', title: `Measurement basis differs${where ? ` (${where})` : ''}. A: ${a}. B: ${b}.` }, icon('warn'), 'basis differs'),
    h('span', { class: 'sub basis-texts' }, h('span', { title: `A: ${a}`, text: `A: ${a}` }), h('span', { title: `B: ${b}`, text: `B: ${b}` })));
}

const ROLE_TEXT = {
  master: 'master', review: 'review cut', silent: 'silent', picture: 'picture only', rough: 'rough or proof',
  clip: 'clip', recording: 'recording', eval: 'evaluation clip', source: 'source capture', published: 'published copy', other: 'other',
};

/* replaceChildren() would print null and arrays as text; append() skips null and flattens. */

function fill(el, ...kids) { el.replaceChildren(); return append(el, kids); }

function fmtCount(n) { return Number(n || 0).toLocaleString(); }

function roleChip(role) { return h('span', { class: `role-chip r-${role}`, text: ROLE_TEXT[role] || role }); }

function libMedia(id, kind) {
  if (kind === 'poster') return `/media/lib/${enc(id)}/poster`;
  if (kind === 'captions') return `/media/lib/${enc(id)}/captions.vtt`;
  return `/media/lib/${enc(id)}`;
}

function libName(entry) {
  if (!entry) return 'unknown video';
  const where = [entry.film, entry.version].filter(Boolean).join(' / ');
  return where ? `${where}: ${entry.name}` : String(entry.name || entry.id || 'unknown video');
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function libraryPlayer(item, label, preload = 'metadata') {
  if (!item.playable) {
    return h('p', { class: 'no-media', text: `This ${item.ext || ''} file does not play in a browser. It is at ${item.path}.` });
  }
  const video = h('video', {
    controls: true, preload, playsinline: true, 'aria-label': label,
    poster: item.has_poster ? libMedia(item.id, 'poster') : null,
  });
  video.src = item.has_poster || preload === 'none' ? libMedia(item.id) : `${libMedia(item.id)}#t=0.1`;
  if (item.has_captions) {
    video.append(h('track', { kind: 'captions', srclang: 'en', label: 'English', src: libMedia(item.id, 'captions'), default: true }));
    video.addEventListener('loadedmetadata', () => { for (const track of video.textTracks) track.mode = 'showing'; });
  }
  return video;
}

function itemFacts(item) {
  return h('div', { class: 'lib-facts' },
    h('p', {}, roleChip(item.role), ' ', h('strong', { class: 'break', text: item.name }),
      h('span', { class: 'sub', text: ` \u00b7 ${fmtDuration(item.duration_s)} \u00b7 ${fmtBytes(item.size)} \u00b7 ` }), timeEl(item.mtime)),
    h('p', { class: 'why', text: `Role: ${item.role_why}. Version: ${item.version_why || 'not part of a film'}.` }),
    item.duplicate_of ? h('p', { class: 'why' }, h('span', { class: 'badge dup', text: 'copy' }), ` Same file as ${item.duplicate_of.place} (${item.duplicate_of.name}).`) : null,
    item.copies ? h('p', { class: 'why', text: `${plural(item.copies, 'other copy', 'other copies')} of this file exist elsewhere on this Mac.` }) : null,
    h('p', { class: 'path', text: item.path }));
}

async function copyForChat(pid, box) {
  return copyMarkdownFrom(`/api/reactions/export?format=md${pid ? `&pid=${enc(pid)}` : ''}`, box, 'Copied for chat');
}

async function copyMarkdownFrom(url, box, done = 'Copied') {
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const text = await response.text();
    try {
      await navigator.clipboard.writeText(text);
      box.replaceChildren(h('p', { class: 'sub', text: 'Copied the Markdown to the clipboard.' }));
      announce(done);
    } catch {
      const area = h('textarea', { readonly: true, rows: 12, 'aria-label': 'Markdown export', value: text, class: 'export-text' });
      box.replaceChildren(h('p', { class: 'sub', text: 'The clipboard is not available here; the Markdown is selected below for copying.' }), area);
      area.focus();
      area.select();
      announce('Clipboard unavailable; the export is selected below');
    }
  } catch (error) {
    announce(`Export failed: ${error.message}`);
  }
}

/* ------------------------------------------------------------------ marks, chips, small components */

const MARK_SHAPES = {
  needs: [['circle', { class: 'dot', cx: 6, cy: 6, r: 3.6 }]],
  passed: [['path', { d: 'M2.4 6.4l2.4 2.4 4.8-5.2' }]],
  rework: [['path', { d: 'M3 3l6 6M9 3l-6 6' }]],
  in_progress: [['circle', { cx: 6, cy: 6, r: 4.3, 'stroke-dasharray': '2.2 1.8' }]],
  not_run: [['rect', { x: 1.8, y: 1.8, width: 8.4, height: 8.4, rx: 1.6, 'stroke-dasharray': '1.8 1.6' }]],
  not_reviewed: [['rect', { x: 1.8, y: 1.8, width: 8.4, height: 8.4, rx: 1.6, 'stroke-dasharray': '1.8 1.6' }]],
  partial: [['rect', { x: 1.8, y: 1.8, width: 8.4, height: 8.4, rx: 1.6, 'stroke-dasharray': '1.8 1.6' }], ['path', { d: 'M4 6h4' }]],
  superseded: [['path', { d: 'M2.2 6h7.6' }]],
  unknown: [['path', { d: 'M4.3 4.4a1.8 1.8 0 1 1 2.6 1.6c-.6.3-.9.7-.9 1.4M6 9.3v.2' }]],
  direct: [['path', { d: 'M2 6h7M6.6 3.6L9 6 6.6 8.4', 'stroke-dasharray': '2 1.4' }]],
  /* The owner's own decision: a ring around his check or cross, never the ink dot of Needs you. */
  decided_ok: [['circle', { cx: 6, cy: 6, r: 4.8 }], ['path', { d: 'M3.9 6.2l1.4 1.4 2.8-3.1' }]],
  decided_changes: [['circle', { cx: 6, cy: 6, r: 4.8 }], ['path', { d: 'M4.4 4.4l3.2 3.2M7.6 4.4l-3.2 3.2' }]],
};

function markGlyph(mark) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 12 12');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const [tag, attrs] of MARK_SHAPES[mark] || MARK_SHAPES.unknown) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
    svg.append(el);
  }
  return svg;
}

/* A status mark: glyph and word, with weight and line style; never colour alone. */
function statusMark(mark, word, title) {
  if (word && String(word).length > 28) return longMark(mark, word, title);
  const known = mark in MARK_SHAPES ? mark : 'unknown';
  return h('span', { class: `mark m-${known}`, title: title || null },
    markGlyph(known), h('span', { class: 'mark-word', text: word || MARK_WORDS[known] || known }));
}

/* A long status phrase keeps its glyph and words but reads in sentence case; uppercase is for short status words. */
function longMark(mark, word, title) {
  const known = mark in MARK_SHAPES ? mark : 'unknown';
  return h('span', { class: `mark m-${known} long`, title: title || null }, markGlyph(known), h('span', { class: 'mark-word', text: word }));
}

function statusPill(status, label) {
  const meta = STATUS[status] || STATUS.unknown;
  return statusMark(meta.mark, label || meta.label, label && label !== meta.label ? meta.label : null);
}

function levelChip(level) {
  const value = level || 'P?';
  const cls = lvlClass(value);
  return h('span', {
    class: `lvl lvl-${cls}`, title: value === 'P?' ? 'Severity not stated in the review' : `Severity ${value}`,
    text: value === 'P?' ? 'Unrated' : value,
  });
}

function verdictChip(cls, text, worst, defects) {
  const meta = VERDICT[cls] || VERDICT.unknown;
  return h('span', { class: 'verdict' },
    statusMark(meta.mark, text ? String(text).replace(/_/g, ' ') : meta.label),
    worst ? levelChip(worst) : null,
    defects ? h('span', { class: 'meta', text: plural(defects, 'finding') }) : null);
}

function runPill(run) {
  if (!run) return statusMark('not_run', 'No engine run');
  const waiting = run.waiting_at && run.waiting_at.length;
  if (waiting) return statusMark('needs', `Waiting at ${run.waiting_at.map(nodeLabel).join(', ')}`, run.label);
  const status = run.status_class || 'unknown';
  return statusPill(status, engineWord(run.status));
}

const openDetails = new Set();
function persistentDetails(key, summaryKids, bodyKids, cls = 'card') {
  return h('details', {
    class: cls, open: openDetails.has(key),
    on: { toggle: (event) => { if (event.currentTarget.open) openDetails.add(key); else openDetails.delete(key); } },
  }, h('summary', {}, summaryKids), bodyKids);
}

/* Node in words with its number secondary: "Motion and scenes · N13". */
function nodeWords(nodeId, rows) {
  const graph = rows || (state.room && state.room.job && state.room.job.graph) || [];
  const row = graph.find((node) => node.id === nodeId);
  if (!row) return nodeLabel(nodeId);
  return nodeTitle(row);
}

function fmtDay(iso) {
  if (!iso) return 'an unknown date';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const options = { month: 'short', day: 'numeric' };
  if (date.getFullYear() !== new Date().getFullYear()) options.year = 'numeric';
  return date.toLocaleDateString(undefined, options);
}

/* A frame: the version's poster when one exists, else a typographic frame (label, duration, date). */
/* ------------------------------------------------------------------ stills: a real frame for versions without a poster */

const STILL_WIDTH = 240;
const STILL_CONCURRENCY = 2;
/* Stills come from a third of the way in: past the opening title card that most versions share. */
const STILL_POSITION = 'third';
const stills = { memory: new Map(), failed: new Set(), pending: new Map(), queue: [], running: 0, db: null, observer: null };

function stillKey(frame) {
  return `${frame.media}|${frame.size ?? ''}|${frame.mtime ?? ''}|t=${STILL_POSITION}`;
}

function openStillsDb() {
  if (stills.db) return stills.db;
  stills.db = new Promise((resolve) => {
    if (!('indexedDB' in window)) { resolve(null); return; }
    let request;
    try { request = indexedDB.open('glassbox-stills', 2); } catch { resolve(null); return; }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (db.objectStoreNames.contains('stills')) request.transaction.objectStore('stills').clear();
      else db.createObjectStore('stills', { keyPath: 'key' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
  return stills.db;
}

async function stillFromDb(key) {
  const db = await openStillsDb();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const request = db.transaction('stills').objectStore('stills').get(key);
      request.onsuccess = () => resolve(request.result ? request.result.url : null);
      request.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
}

async function stillToDb(key, url) {
  const db = await openStillsDb();
  if (!db) return;
  try { db.transaction('stills', 'readwrite').objectStore('stills').put({ key, url, at: Date.now() }); } catch { /* memory only */ }
}

function dataUrlBlob(url) {
  const comma = url.indexOf(',');
  const bytes = atob(url.slice(comma + 1));
  const out = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i += 1) out[i] = bytes.charCodeAt(i);
  return new Blob([out], { type: 'image/jpeg' });
}

/* The page allows only same-origin images, so a still is decoded in script and painted on a canvas. */
async function paintStill(canvas, url) {
  const bitmap = await createImageBitmap(dataUrlBlob(url));
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext('2d').drawImage(bitmap, 0, 0);
  if (bitmap.close) bitmap.close();
  const frame = canvas.closest('.frame');
  if (frame) frame.classList.add('has-still');
}

/* One frame a third of the way into the master, drawn small; the video element is dropped right after. */
function captureStill(media, durationHint) {
  return new Promise((resolve) => {
    const video = document.createElement('video');
    let done = false;
    let timer = null;
    const finish = (url) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      video.removeAttribute('src');
      video.load();
      resolve(url);
    };
    timer = window.setTimeout(() => finish(null), 10000);
    video.muted = true;
    video.preload = 'metadata';
    video.playsInline = true;
    video.addEventListener('loadedmetadata', () => {
      const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : Number(durationHint) || 0;
      video.currentTime = duration > 0 ? duration / 3 : 1;
    }, { once: true });
    video.addEventListener('seeked', () => {
      try {
        const width = STILL_WIDTH;
        const height = Math.max(1, Math.round(width * (video.videoHeight || 9) / (video.videoWidth || 16)));
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        canvas.getContext('2d').drawImage(video, 0, 0, width, height);
        finish(canvas.toDataURL('image/jpeg', 0.72));
      } catch { finish(null); }
    }, { once: true });
    video.addEventListener('error', () => finish(null), { once: true });
    video.src = media;
  });
}

function stillVisible(job) {
  return [...job.canvases].some((canvas) => canvas.isConnected && canvas.dataset.visible === '1');
}

function pumpStills() {
  while (stills.running < STILL_CONCURRENCY && stills.queue.length) {
    const job = stills.queue.shift();
    if (!stillVisible(job)) { stills.pending.delete(job.key); continue; }
    stills.running += 1;
    captureStill(job.frame.media, job.frame.duration_s).then((url) => {
      stills.running -= 1;
      stills.pending.delete(job.key);
      if (url) {
        stills.memory.set(job.key, url);
        stillToDb(job.key, url);
        for (const canvas of job.canvases) {
          if (!canvas.isConnected) continue;
          canvas.dataset.painted = job.key;
          if (stills.observer) stills.observer.unobserve(canvas);
          paintStill(canvas, url).catch(() => {});
        }
      } else {
        stills.failed.add(job.key);
      }
      pumpStills();
    });
  }
}

async function requestStill(canvas) {
  const frame = canvas._frame;
  if (!frame) return;
  const key = stillKey(frame);
  if (canvas.dataset.painted === key || stills.failed.has(key)) return;
  const done = (url) => {
    canvas.dataset.painted = key;
    if (stills.observer) stills.observer.unobserve(canvas);
    paintStill(canvas, url).catch(() => {});
  };
  const known = stills.memory.get(key);
  if (known) { done(known); return; }
  const cached = await stillFromDb(key);
  if (cached) { stills.memory.set(key, cached); done(cached); return; }
  if (canvas.dataset.visible !== '1') return;
  let job = stills.pending.get(key);
  if (!job) {
    job = { key, frame, canvases: new Set() };
    stills.pending.set(key, job);
    stills.queue.push(job);
  }
  job.canvases.add(canvas);
  pumpStills();
}

/* Only frames in the viewport ask for a still; frames scrolled away keep their typographic face. */
function observeStill(canvas, frame) {
  if (!('IntersectionObserver' in window) || !window.createImageBitmap) return;
  if (!stills.observer) {
    stills.observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        entry.target.dataset.visible = entry.isIntersecting ? '1' : '0';
        if (entry.isIntersecting) requestStill(entry.target);
      }
    }, { rootMargin: '0px' });
  }
  canvas._frame = frame;
  stills.observer.observe(canvas);
}

function frameBox(frame, { label, alt, meta: metaText, preferStill = false } = {}) {
  const capturable = frame.media && frame.has_master !== false && frame.playable !== false;
  if (frame.poster) {
    const box = h('span', { class: 'frame' }, h('img', { src: frame.poster, alt: alt || '', loading: 'lazy', decoding: 'async' }));
    /* In a version rail every poster is the same title card, so a still from inside the film replaces it once drawn. */
    if (preferStill && capturable) {
      const canvas = h('canvas', { class: 'still', 'aria-hidden': 'true' });
      box.append(canvas);
      observeStill(canvas, frame);
    }
    return box;
  }
  const meta = metaText || [frame.duration_s ? fmtDuration(frame.duration_s) : null, frame.mtime ? fmtDay(frame.mtime) : null].filter(Boolean).join(' \u00b7 ');
  const box = h('span', { class: `frame typo${frame.has_master === false ? ' empty-frame' : ''}` },
    h('span', { class: 'ft' }, h('span', { class: 'ft-label', text: label || frame.label }), h('span', { class: 'ft-meta', text: meta || 'no master yet' })));
  if (capturable) {
    const canvas = h('canvas', { class: 'still', 'aria-hidden': 'true' });
    box.append(canvas);
    observeStill(canvas, frame);
  }
  return box;
}

function frameMark(frame) {
  if (frame.mark === 'file') return roleChip(frame.role || 'other');
  return statusMark(frame.mark, frame.mark_word);
}

/* ------------------------------------------------------------------ reactions: list, undo */

function reactionKindText(record) {
  if (record.kind === 'prefer') {
    const side = (key) => (record.pid === 'library' ? ((record.library || {})[key] || {}).version || record[key] : record[key]);
    return record.choice === 'tie' ? `Tie (${side('a')} and ${side('b')})` : `Prefer ${String(record.choice).toUpperCase()} (${side(record.choice)})`;
  }
  return KIND_LABEL[record.kind] || record.kind;
}

function targetText(record) {
  if (record.target_words) return oneLine(record.target_words, 90);
  if (record.pid === 'library') {
    const lib = record.library || {};
    if (record.kind === 'prefer') return `${libName(lib.a)} and ${libName(lib.b)}`;
    return `library video ${libName(lib.id ? lib : { name: (record.target || {}).id })}`;
  }
  if (record.kind === 'prefer') return `${record.a} and ${record.b}`;
  const target = record.target || {};
  const on = record.version ? ` on ${record.version}` : '';
  switch (target.type) {
    case 'version': return `version ${target.id}`;
    case 'node': return `${nodeLabel(target.id)}${on}`;
    case 'gate': return `gate ${nodeLabel(target.id)}${on}`;
    case 'finding': return `finding ${target.id}${on}`;
    case 'time': return `moment${on}`;
    default: return '';
  }
}

/* ------------------------------------------------------------------ the producer's answers (threads) */

function threadEvents(record) {
  if (Array.isArray(record.thread)) return record.thread;
  return (record.acks || []).map((ack) => ({ ...ack, kind: 'ack' }));
}

function threadState(record) {
  if (record.state) return record.state;
  return (record.acks || []).length ? 'picked_up' : 'open';
}

function waitingSince(record) {
  const reopen = [...threadEvents(record)].reverse().find((event) => event.kind === 'reopen');
  return threadState(record) === 'open' && reopen ? reopen.ts : record.ts;
}

/* "15 min", "3 h", "2 d" */
function ageWords(ts) {
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 60000));
  if (!Number.isFinite(minutes)) return '';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} d`;
}

/* "3 notes: 2 picked up, 1 waiting 1 h." */
function pickupSummary(records) {
  const mine = records.filter((r) => !r.retracted && (!r.actor || r.actor === 'owner'));
  if (!mine.length) return 'No notes yet.';
  const counts = { open: 0, picked_up: 0, resolved: 0 };
  let oldest = null;
  for (const record of mine) {
    const current = threadState(record);
    counts[current] = (counts[current] || 0) + 1;
    const since = waitingSince(record);
    if (current === 'open' && since && (!oldest || since < oldest)) oldest = since;
  }
  const parts = [];
  if (counts.picked_up) parts.push(`${counts.picked_up}\u00a0picked\u00a0up`);
  if (counts.resolved) parts.push(`${counts.resolved}\u00a0addressed`);
  if (counts.open) parts.push(`${counts.open}\u00a0waiting for the producer${oldest ? ` for\u00a0${ageWords(oldest)}` : ''}`);
  return `${plural(mine.length, 'note')}: ${joinList(parts)}.`;
}

function producerOf(event) {
  return String(event.producer || '').trim() || 'the producer';
}

/* "Waiting for the producer", "Picked up", "Addressed in v29", "Reopened": icon and words, never colour alone. */
function stateWords(state, reopened, resolvedIn) {
  if (state === 'resolved') return `Addressed in ${resolvedIn || 'a later version'}`;
  if (state === 'picked_up') return 'Picked up';
  return reopened ? 'Reopened' : 'Waiting for the producer';
}

function stateChipFor(state, reopened, resolvedIn) {
  const glyph = { open: reopened ? 'arrow' : 'clock', picked_up: 'ack', resolved: 'check' }[state] || 'clock';
  return h('span', { class: `pickup state-${state}` }, icon(glyph), ` ${stateWords(state, reopened, resolvedIn)}`);
}

function stateChip(record) {
  const events = threadEvents(record);
  const state = threadState(record);
  const resolve = [...events].reverse().find((event) => event.kind === 'resolve');
  return stateChipFor(state, state === 'open' && events.some((event) => event.kind === 'reopen'), resolve && resolve.version);
}

/* The thread under an owner reaction: picked up (with the plan), addressed in a version (linked), reopened; and
   Reopen when the latest word is a resolve. */
function threadBlock(record, { onChanged = null } = {}) {
  const events = threadEvents(record);
  const film = record.pid && record.pid !== 'library' ? `job-${record.pid}` : null;
  const lines = events.map((event) => {
    const said = event.text ? `: ${event.text}` : '.';
    if (event.kind === 'ack') {
      return h('li', { class: 'th-line th-ack' }, icon('ack'), h('span', {}, `Picked up by ${producerOf(event)}, `, timeEl(event.ts), said));
    }
    if (event.kind === 'resolve') {
      const where = film && event.version ? h('a', { href: filmHref(film, { v: event.version, tab: 'watch' }), text: event.version }) : (event.version || 'a later version');
      return h('li', { class: 'th-line th-resolve' }, icon('check'),
        h('span', {}, 'Addressed in ', where, event.producer ? ` by ${event.producer}` : '', ', ', timeEl(event.ts), said));
    }
    if (event.kind === 'reopen') {
      return h('li', { class: 'th-line th-reopen' }, icon('arrow'), h('span', {}, 'Reopened by you, ', timeEl(event.ts), said));
    }
    return null;
  }).filter(Boolean);
  const resolve = [...events].reverse().find((event) => event.kind === 'resolve');
  const canReopen = threadState(record) === 'resolved' && resolve && record.actor === 'owner' && film && !record.retracted && !record._pending;
  if (!lines.length && !canReopen) return null;
  return h('div', { class: 'thread' },
    lines.length ? h('ul', { class: 'thread-lines', 'aria-label': 'The producer\u2019s answers' }, lines) : null,
    canReopen ? reopenControl(record, resolve, onChanged) : null);
}

function reopenControl(record, resolve, onChanged) {
  const id = nextId('reopen');
  const status = h('p', { class: 'meta', role: 'status' });
  const text = h('textarea', { id: `${id}-text`, rows: 2, maxlength: 4000, 'data-focus-key': `reopen:${record.id}:text` });
  const send = h('button', { type: 'button', class: 'btn small' }, icon('arrow'), 'Reopen for the producer');
  const cancel = h('button', { type: 'button', class: 'btn small quiet' }, 'Cancel');
  const form = h('div', { class: 'reopen-form', id, hidden: true },
    h('label', { for: `${id}-text`, text: `What did not land in ${resolve.version}? (needed)` }), text, h('div', { class: 'btn-row' }, send, cancel));
  const toggle = h('button', {
    type: 'button', class: 'btn small quiet', 'aria-expanded': 'false', 'aria-controls': id, 'data-focus-key': `reopen:${record.id}`,
  }, 'Reopen');
  const show = (open) => { form.hidden = !open; toggle.setAttribute('aria-expanded', String(open)); if (open) text.focus(); };
  toggle.addEventListener('click', () => show(form.hidden));
  cancel.addEventListener('click', () => { show(false); toggle.focus(); });
  text.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.stopPropagation(); show(false); toggle.focus(); } });
  send.addEventListener('click', async () => {
    const value = text.value.trim();
    if (!value) { status.textContent = 'Say what did not land first.'; text.focus(); return; }
    send.disabled = true;
    status.textContent = 'Saving\u2026';
    try {
      await postReaction({ pid: record.pid, kind: 'reopen', ref: resolve.id, text: value });
      announce('Reopened for the producer');
      status.textContent = 'Reopened: the producer sees it as waiting again.';
      form.hidden = true;
      toggle.hidden = true;
      if (onChanged) onChanged(); else refreshSoon();
    } catch (error) {
      status.textContent = `Not saved: ${error.message}`;
      send.disabled = false;
    }
  });
  return h('div', { class: 'reopen' }, toggle, form, status);
}

/* Where a reaction's target opens: a decision on a gate opens Decide (its decided state and thread), a note on a gate
   or node the node details, a moment or finding Watch at its time, a preference Compare. */
function reactionHref(record) {
  const target = record.target || {};
  if (record.pid === 'library') {
    const lib = record.library || {};
    return lib.film_id ? filmHref(`lib-${lib.film_id}`, { v: lib.version, i: target.id, t: target.t }) : hrefs.video(target.id || record.a, {});
  }
  if (!record.pid) return null;
  const film = `job-${record.pid}`;
  if (record.kind === 'prefer') return filmHref(film, { tab: 'compare', a: record.a, b: record.b });
  if (['approve', 'request_changes'].includes(record.kind) && target.type === 'gate') return filmHref(film, { v: record.version, tab: 'decide', g: target.id });
  if (['node', 'gate'].includes(target.type)) return filmHref(film, { v: record.version, tab: 'pipeline', n: target.id });
  if (target.type === 'finding') return filmHref(film, { v: record.version, tab: 'watch', f: target.id, t: target.t });
  if (target.type === 'time') return filmHref(film, { v: record.version, tab: 'watch', t: target.t ?? record.t });
  return filmHref(film, { v: record.version || target.id, tab: 'watch' });
}

function reactionItem(record, { links = false } = {}) {
  let pickup;
  if (record._pending) pickup = h('span', { class: 'pickup', text: 'Saving\u2026' });
  else if (record._failed) pickup = h('span', { class: 'pickup done', text: 'Not saved' });
  else if (record.retracted) pickup = h('span', { class: 'pickup' }, 'Retracted ', timeEl(record.retracted.ts));
  else pickup = stateChip(record);
  const href = links ? reactionHref(record) : null;
  return h('li', { class: `reaction${record._pending ? ' pending' : ''}${record.retracted ? ' retracted' : ''}`, 'data-rid': record.id || null, tabindex: links ? '-1' : null },
    h('div', { class: 'reaction-head' },
      h('span', { class: 'kind-word', text: reactionKindText(record) }),
      href ? h('a', { class: 'sub target-link', href, text: targetText(record) }) : h('span', { class: 'sub', text: targetText(record) }),
      record.t !== null && record.t !== undefined ? h('span', { class: 'meta', text: fmtT(record.t) }) : null,
      links && record.id ? h('a', { class: 'thread-link', href: `#/activity?r=${enc(record.id)}`, 'aria-label': `Link to this thread, ${fmtWhen(record.ts)}` }, timeEl(record.ts)) : timeEl(record.ts),
      record.project ? h('span', { class: 'meta', text: record.project }) : null,
      record.actor && record.actor !== 'owner' ? h('span', { class: 'badge', text: record.actor }) : null,
      pickup),
    record.text ? h('p', { class: 'reaction-text', text: record.text }) : null,
    record.tags && record.tags.length ? h('p', {}, record.tags.map((tag) => h('span', { class: 'tag', text: tag }))) : null,
    record.retracted || record._pending ? null : threadBlock(record));
}

function reactionList(records, empty = 'No notes yet.', options = {}) {
  return records.length ? h('ol', { class: 'reactions' }, records.map((record) => reactionItem(record, options))) : h('p', { class: 'empty', text: empty });
}

/* A list that shows a reaction while it saves, and drops it again when it is undone. */
function liveReactions(initial, empty) {
  let records = [...initial];
  const box = h('div', { class: 'reaction-box' });
  const draw = () => fill(box, reactionList(records, empty));
  draw();
  return {
    el: box,
    pending(record) { records = [record, ...records]; draw(); },
    saved(record, pending) { records = records.map((r) => (r === pending ? { ...record, acks: [] } : r)); draw(); },
    failed(pending) { records = records.map((r) => (r === pending ? { ...r, _pending: false, _failed: true } : r)); draw(); },
    remove(id) { records = records.filter((r) => r.id !== id); draw(); },
  };
}

async function retractReaction(record) {
  return postReaction({ pid: record.pid, kind: 'retract', ref: record.id });
}

/* "Saved. Undo" for ten seconds after a note, agree, disagree, flag or preference. */
function undoLine(records, { onUndone, word = 'Saved' } = {}) {
  const list = Array.isArray(records) ? records : [records];
  const line = h('span', { class: 'undo-line', role: 'status' }, h('span', { text: `${word}.` }));
  const button = h('button', { type: 'button', class: 'btn small quiet' }, icon('undo'), 'Undo');
  let timer = null;
  button.addEventListener('click', async () => {
    clearTimeout(timer);
    button.disabled = true;
    try {
      for (const record of list) await retractReaction(record);
      fill(line, h('span', { text: list.length > 1 ? `Undone: ${list.length} reactions retracted.` : 'Undone: retracted.' }));
      announce('Retracted');
      onUndone?.(list);
      refreshSoon();
    } catch (error) {
      button.disabled = false;
      line.append(h('span', { class: 'composer-status error', text: ` Undo failed: ${error.message}` }));
    }
  });
  line.append(button);
  timer = window.setTimeout(() => { if (button.isConnected && !button.disabled) button.remove(); }, UNDO_MS);
  return line;
}

/* ------------------------------------------------------------------ the composer (notes and reactions) */

const COMPOSER_KINDS = {
  finding: [
    { id: 'agree', kind: 'agree', label: 'Agree' },
    { id: 'disagree', kind: 'disagree', label: 'Disagree' },
    { id: 'note', kind: 'note', label: 'Note' },
  ],
  gate: [
    { id: 'agree', kind: 'agree', label: 'Agree' },
    { id: 'disagree', kind: 'disagree', label: 'Disagree' },
    { id: 'note', kind: 'note', label: 'Note' },
  ],
  node: [
    { id: 'note', kind: 'note', label: 'Note' },
    { id: 'flag', kind: 'flag', label: 'Flag' },
  ],
  comment_only: [{ id: 'note', kind: 'note', label: 'Note' }],
  version_note: [{ id: 'note_time', kind: 'note', label: 'Note at this moment', timed: true }],
  library: [
    { id: 'note_time', kind: 'note', label: 'Note at this moment', timed: true },
    { id: 'flag', kind: 'flag', label: 'Flag' },
  ],
};

/* DOM append drops nothing on its own: a null child would print "null". */
function appendKids(parent, ...kids) {
  parent.append(...kids.filter((kid) => kid !== null && kid !== undefined && kid !== false));
  return parent;
}

function composer(opts) {
  const options = COMPOSER_KINDS[opts.set] || COMPOSER_KINDS.node;
  let selected = options.find((option) => option.id === opts.select) || (options.length === 1 ? options[0] : null);
  const selectedTags = new Set();
  const textId = nextId('note-text');
  const keyBase = `${opts.set}:${opts.target ? `${opts.target.type}:${opts.target.id}` : 'none'}`;
  const form = h('form', { class: 'composer', novalidate: true, 'aria-label': `Note on ${opts.label}` });
  const statusLine = h('span', { class: 'composer-status', role: 'status' });
  const timeHint = h('span', { class: 'meta' });
  const save = h('button', { type: 'submit', class: 'btn primary small', 'data-focus-key': `save:${keyBase}` }, 'Save');
  const textarea = h('textarea', { id: textId, maxlength: 4000, rows: 3, 'data-focus-key': `text:${keyBase}` });
  const kindButtons = options.length > 1 ? options.map((option) => [option, h('button', {
    type: 'button', class: 'kind', 'aria-pressed': String(option === selected), 'data-focus-key': `kind:${keyBase}:${option.id}`,
    on: { click: () => choose(option) },
  }, icon(OPTION_ICON[option.kind] || 'note'), option.label)]) : [];
  const tagButtons = TAGS.map((tag) => h('button', {
    type: 'button', class: 'tag-toggle', 'aria-pressed': 'false',
    on: {
      click: (event) => {
        const on = !selectedTags.has(tag);
        if (on) selectedTags.add(tag); else selectedTags.delete(tag);
        event.currentTarget.setAttribute('aria-pressed', String(on));
      },
    },
  }, tag));
  const details = h('div', { class: 'composer-details', hidden: !selected });
  const setStatus = (message, isError = false) => {
    fill(statusLine, typeof message === 'string' ? document.createTextNode(message) : message);
    statusLine.classList.toggle('error', isError);
  };
  const noteLabel = h('label', { for: textId, class: 'label', text: 'Note' });
  /* One label for a timed note everywhere: "Note at 00:12.3". */
  const updateHint = () => {
    const at = opts.getTime ? `Note at ${fmtT(opts.getTime())}` : null;
    noteLabel.textContent = selected && selected.timed && at ? at : 'Note';
    for (const [option, button] of kindButtons) if (option.timed && at) button.lastChild.textContent = at;
  };
  function choose(option) {
    selected = option;
    for (const [candidate, button] of kindButtons) button.setAttribute('aria-pressed', String(candidate === option));
    details.hidden = false;
    updateHint();
  }
  details.append(
    noteLabel,
    textarea,
    h('div', { class: 'tags', role: 'group', 'aria-label': 'Quick tags' }, tagButtons),
    h('div', { class: 'composer-foot' }, save,
      opts.onCancel ? h('button', { type: 'button', class: 'btn small quiet', 'data-focus-key': `cancel:${keyBase}`, on: { click: () => opts.onCancel() } }, 'Cancel') : null,
      timeHint, statusLine));
  if (opts.onCancel) {
    form.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      opts.onCancel();
    });
  }
  appendKids(form,
    h('p', { class: 'target', text: opts.label }),
    kindButtons.length ? h('div', { class: 'kinds', role: 'group', 'aria-label': 'Reaction' }, kindButtons.map(([, button]) => button)) : null,
    details);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selected) { setStatus('Choose Agree, Disagree or Note first.', true); return; }
    const text = textarea.value.trim();
    if (selected.kind === 'note' && !text) { setStatus('Write the note first.', true); textarea.focus(); return; }
    const body = { pid: opts.pid, kind: selected.kind, tags: [...selectedTags], text };
    if (selected.timed) {
      const t = Math.max(0, Number(opts.getTime ? opts.getTime() : 0) || 0);
      const rounded = Math.round(t * 1000) / 1000;
      body.target = cleanTarget(opts.timedTarget ? opts.timedTarget(rounded) : { type: 'time', id: fmtT(t), version: opts.version, t: rounded });
    } else {
      body.target = cleanTarget(opts.target);
    }
    const pending = {
      ...body, id: nextId('pending'), ts: new Date().toISOString(), actor: 'owner', project: null,
      version: body.target.version ?? opts.version ?? null, t: body.target.t ?? null, acks: [], _pending: true,
    };
    opts.onPending?.(pending);
    save.disabled = true;
    setStatus('Saving\u2026');
    try {
      const record = await postReaction(body);
      textarea.value = '';
      selectedTags.clear();
      tagButtons.forEach((button) => button.setAttribute('aria-pressed', 'false'));
      setStatus(undoLine(record, { onUndone: () => opts.onUndone?.(record) }));
      announce('Saved. Undo is available for ten seconds.');
      opts.onSaved?.(record, pending);
    } catch (error) {
      setStatus(`Not saved: ${error.message}`, true);
      announce(`Not saved: ${error.message}`);
      opts.onFailed?.(pending, error);
    } finally {
      save.disabled = false;
    }
  });
  form.selectOption = (id) => { const option = options.find((o) => o.id === id); if (option) choose(option); };
  form.focusText = ({ scroll = false } = {}) => textarea.focus({ preventScroll: !scroll });
  form.refreshHint = updateHint;
  updateHint();
  return form;
}

/* ------------------------------------------------------------------ findings: quiet one-line rows */

function findingSourceKey(finding) {
  return finding.gate ? 'checks' : finding.source === 'run' ? 'engine' : 'reviews';
}

function findingRow(finding, ctx) {
  const row = h('li', { class: 'finding', 'data-uid': finding.uid, tabindex: '-1' });
  const label = `${finding.level} ${finding.id || finding.gate || ''}`.trim();
  const hasTime = finding.t0 !== null && finding.t0 !== undefined;
  const bodyId = nextId('finding-body');
  const slot = h('div', { class: 'gi-slot' });
  const body = h('div', { class: 'f-body', id: bodyId, hidden: true });
  const mine = ctx.answers ? ctx.answers.get(finding.uid) || [] : [];
  const latest = mine[0];
  const select = h('button', {
    type: 'button', class: 'f-select', 'aria-expanded': 'false', 'aria-controls': bodyId, 'data-focus-key': `f:${finding.uid}`,
  }, h('span', { class: 'f-title', text: shortClause(findingTitle(finding)) }),
  h('span', { class: 'f-node', text: [finding.node ? nodeWords(finding.node) : null, finding.gate && !finding.node ? `check ${finding.gate}` : null].filter(Boolean).join(' \u00b7 ') }),
  latest ? h('span', { class: 'f-answer' }, `You answered: ${KIND_LABEL[latest.kind] || latest.kind}. `, stateChip(latest)) : null);
  const seek = () => {
    if (ctx.fromDrawer || !state.video) go(links.version(ctx.pid, ctx.version, { t: finding.t0, f: finding.uid }));
    else seekTo(finding.t0);
  };
  const openComposer = (optionId) => {
    const current = slot.firstChild;
    if (current && current.dataset.option === optionId) { fill(slot); return; }
    const box = composer({
      pid: ctx.pid, version: ctx.version, set: 'finding', select: optionId,
      target: cleanTarget({ type: 'finding', id: finding.uid, version: ctx.version, t: hasTime ? finding.t0 : null }),
      label: `Finding ${finding.id || finding.uid} (${finding.level}) on ${ctx.version}: ${oneLine(findingTitle(finding), 90)}`,
      onSaved: () => { ctx.onReacted?.(finding); },
    });
    box.dataset.option = optionId;
    fill(slot, box);
    box.focusText();
  };
  const detailsId = nextId('finding-details');
  const details = h('div', { id: detailsId, hidden: true }, definitionList([
    ['Id', finding.id], ['Finding', findingTitle(finding) !== String(finding.what || '').replace(/\s+/g, ' ').trim() ? findingTitle(finding) : null],
    ['What', finding.what], ['Where', finding.at_text], ['Fix', finding.fix], ['Fix node', finding.fix_node],
    ['Acceptance check', finding.acceptance], ['Severity as written', finding.severity], ['Dimension', finding.dimension],
    ['Status', finding.status], ['Seen in', seenRange(finding)],
    ['Needs a human', finding.needs_human === true ? 'yes' : finding.needs_human === false ? 'no' : null],
    ['Evidence', finding.evidence], ['Source', findingSource(finding)],
  ]));
  const detailsToggle = h('button', {
    type: 'button', class: 'btn small quiet', 'aria-expanded': 'false', 'aria-controls': detailsId,
    on: { click: () => { details.hidden = !details.hidden; detailsToggle.setAttribute('aria-expanded', String(!details.hidden)); } },
  }, 'Details');
  appendKids(body,
    finding.evidence ? h('p', { class: 'why', text: oneLine(finding.evidence, 320) }) : null,
    h('div', { class: 'f-actions' },
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Agree with ${label}`, on: { click: () => openComposer('agree') } }, icon('check'), 'Agree'),
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Disagree with ${label}`, on: { click: () => openComposer('disagree') } }, icon('cross'), 'Disagree'),
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Note on ${label}`, on: { click: () => openComposer('note') } }, icon('note'), 'Note'),
      detailsToggle),
    mine.length ? h('div', { class: 'f-answers' }, h('p', { class: 'label', text: mine.length === 1 ? 'Your answer' : 'Your answers' }), reactionList(mine)) : null,
    details, slot);
  select.addEventListener('click', () => {
    const open = body.hidden;
    body.hidden = !open;
    row.classList.toggle('open', open);
    select.setAttribute('aria-expanded', String(open));
  });
  row.append(h('div', { class: 'f-line' },
    levelChip(finding.level), select,
    hasTime
      ? h('button', { type: 'button', class: 'finding-time', title: 'Play from this moment', 'aria-label': `Play from ${fmtT(finding.t0)}, ${label}`, on: { click: seek } }, icon('seek'), fmtT(finding.t0))
      : h('span', { class: 'meta', text: 'no time' })),
  body);
  return row;
}

function findingsList(findings, ctx) {
  return h('ol', { class: 'findings' }, findings.map((finding) => findingRow(finding, ctx)));
}


/* Server phrasing in the shared vocabulary: finding, check, in progress. */
function plainWords(text) {
  return String(text || '')
    .replace(/\bdefects\b/g, 'findings').replace(/\bdefect\b/g, 'finding')
    .replace(/\bautomatic gates\b/g, 'automatic checks').replace(/\bautomatic gate\b/g, 'automatic check')
    .replace(/\bin flight\b/g, 'in progress');
}

/* ------------------------------------------------------------------ state, links and the render loop */

const state = {
  meta: null,
  films: null,
  filmsAt: 0,
  route: null,
  baseKey: null,
  view: null,
  room: null,
  drawer: null,
  timer: null,
  scanTimer: null,
  video: null,
};
const main = () => $('#main');

/* Links the shared renderers use; they all land in the new review room. */
const links = {
  needs: () => hrefs.inbox(),
  activity: () => hrefs.activity(),
  job: (pid, v) => filmHref(`job-${pid}`, { v }),
  node: (pid, node, v) => filmHref(`job-${pid}`, { v, tab: 'pipeline', n: node }),
  version: (pid, v, query) => filmHref(`job-${pid}`, { v, tab: 'watch', ...(query || {}) }),
  gates: (pid, v) => filmHref(`job-${pid}`, { v, tab: 'pipeline' }),
  compare: (pid, a, b) => filmHref(`job-${pid}`, { tab: 'compare', a, b }),
  library: () => hrefs.films(),
  film: (id, query) => filmHref(`lib-${id}`, query || {}),
  libItem: (id, query) => hrefs.video(id, query || {}),
};

/* Where a library file opens. A file inside a job's folder opens in that job's Files tab, highlighted; the job is
   the film, so its library twin never gets a room of its own. Other files open in their library film, or alone. */
function jobVersionOf(pid, label) {
  const row = state.films ? state.films.rows.find((r) => r.kind === 'job' && r.pid === pid) : null;
  return row && label && row.versions.some((frame) => frame.label === label) ? label : null;
}

function libHref(item, { t = null } = {}) {
  const pid = item.job_pid || (item.film_summary && item.film_summary.job_pid) || null;
  if (pid) return filmHref(`job-${pid}`, { v: jobVersionOf(pid, item.version), tab: 'files', file: item.id });
  if (item.film_id) return filmHref(`lib-${item.film_id}`, { v: item.version, i: item.id, t });
  return hrefs.video(item.id, t !== null && t !== undefined ? { t } : {});
}

function go(hash) {
  if (location.hash === hash) render(); else location.hash = hash;
}

/* Change the current address without a new history entry and without re-rendering. */
function replaceQuery(updates) {
  const route = state.route;
  if (!route) return;
  const params = new URLSearchParams(route.query);
  for (const [key, value] of Object.entries(updates)) {
    if (value === null || value === undefined || value === '') params.delete(key); else params.set(key, String(value));
  }
  const text = params.toString();
  history.replaceState(null, '', `#${route.path}${text ? `?${text}` : ''}`);
  state.route = parseRoute(location.hash);
}

async function loadFilms(force = false) {
  if (!force && state.films && Date.now() - state.filmsAt < 20000) return state.films;
  state.films = await getJSON('/api/films');
  state.filmsAt = Date.now();
  return state.films;
}

function decisionCount() {
  return state.films ? state.films.decisions.length : 0;
}

let renderSeq = 0;

async function render() {
  const seq = ++renderSeq;
  const wanted = redirectOf(location.hash);
  if (wanted !== (location.hash || '')) history.replaceState(null, '', wanted);
  const route = parseRoute(location.hash);
  const previous = state.route;
  state.route = route;
  clearTimeout(state.timer);
  try {
    if (!state.meta) {
      state.meta = await getJSON('/api/meta');
      fillFooter();
    }
    await loadFilms(false);
    if (seq !== renderSeq) return;
    renderMasthead();
    const baseKey = route.name === 'film' ? `film|${route.filmId}` : `${route.name}|${route.itemId || ''}`;
    if (baseKey !== state.baseKey) {
      state.baseKey = baseKey;
      state.view = null;
      state.room = null;
      state.video = null;
      closeDrawer({ restore: false });
      await renderMain(route, seq);
      if (seq !== renderSeq) return;
      if (previous) focusHeading();
    } else if (state.view && state.view.applyRoute) {
      await state.view.applyRoute(route, seq);
    }
  } catch (error) {
    if (error.status === 404 && !state.films) {
      showMessage('Restart Glass Box', 'This page is the new interface, and the server running it predates it. Stop server.py and start it again; nothing is lost.', false);
    } else {
      showMessage('Something went wrong', `${error.message || error}. Reload the page; if it persists, check that the Glass Box server is running.`);
    }
  } finally {
    if (seq === renderSeq) schedulePoll();
  }
}

async function renderMain(route, seq) {
  const target = main();
  switch (route.name) {
    case 'inbox': return viewInbox(target, seq);
    case 'films': return viewFilms(target, route, seq);
    case 'film': return viewRoom(target, route, seq);
    case 'video': return viewVideo(target, route, seq);
    case 'activity': return viewActivity(target, seq);
    default:
      return showMessage('Page not found', 'Nothing lives at this address. The Inbox lists what waits for you; Films lists every film and version.', true);
  }
}

function showMessage(title, text, withLinks = true) {
  state.baseKey = null;
  state.view = null;
  main().replaceChildren(h('header', { class: 'page-head' },
    h('h1', { class: 'page-title', tabindex: '-1', text: title }),
    h('p', { class: 'deck', text }),
    withLinks ? h('p', {}, h('a', { href: hrefs.inbox(), text: 'Go to the Inbox' }), ' or ', h('a', { href: hrefs.films(), text: 'open Films' }), '.') : null));
}

function focusHeading() {
  const heading = $('#main h1');
  if (!heading) return;
  heading.setAttribute('tabindex', '-1');
  heading.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

function fillFooter() {
  const meta = state.meta;
  if (!meta) return;
  fill($('#footer-text'), 'Your notes are saved on this Mac, outside the projects. Where: ',
    h('button', { type: 'button', class: 'linkish', 'data-focus-key': 'footer:help', on: { click: openHelp } }, 'Help'), '.');
}

/* ------------------------------------------------------------------ masthead: nav, count, search, scan line */

function renderMasthead() {
  const route = state.route || {};
  const current = { inbox: 'inbox', films: 'films', film: 'films', video: 'films', activity: 'activity' }[route.name];
  for (const link of $$('.mast-nav a')) link.setAttribute('aria-current', link.dataset.nav === current ? 'page' : 'false');
  const count = decisionCount();
  const badge = $('#nav-count');
  badge.textContent = String(count);
  badge.classList.toggle('zero', !count);
  $('#nav-inbox').setAttribute('aria-label', `Inbox, ${plural(count, 'decision')} waiting`);
  renderScanline(state.films ? state.films.library : null);
}

function renderScanline(library) {
  const line = $('#scanline');
  const scanning = library && library.status === 'scanning';
  line.hidden = !scanning;
  clearTimeout(state.scanTimer);
  if (!scanning) return;
  const progress = library.progress || {};
  const reading = progress.phase === 'reading' && progress.to_read;
  line.className = `scanline ${reading ? 'reading' : 'walking'}`;
  line.style.setProperty('--p', reading ? String(Math.min(1, (progress.read || 0) / progress.to_read)) : '0');
  line.title = reading ? `Library scan: reading ${progress.read} of ${progress.to_read} new files` : `Library scan: ${fmtCount(progress.videos)} videos found so far`;
  state.scanTimer = window.setTimeout(async () => {
    try {
      const status = await getJSON('/api/library/status');
      if (state.films) state.films.library = status;
      if (status.status !== 'scanning') {
        await loadFilms(true);
        announce('Library scan finished');
        if (state.view && state.view.onScanDone) state.view.onScanDone();
      }
      renderMasthead();
    } catch { /* the regular poll marks staleness */ }
  }, 2000);
}

function searchWords(text) {
  return String(text || '').toLowerCase().replace(/[_\-./\\]+/g, ' ');
}

/* The one search rule: every word of the query, normalised like the text, appears in the text. */
function matchesSearch(text, query) {
  const haystack = searchWords(text);
  return searchWords(query).split(/\s+/).filter(Boolean).every((word) => haystack.includes(word));
}

function searchIndex() {
  const rows = state.films ? state.films.rows : [];
  const out = [];
  for (const row of rows) {
    out.push({ kind: 'film', text: searchWords(`${row.name} ${row.area} ${row.rel_path || ''}`), label: row.name,
      meta: `${row.area} \u00b7 ${row.counts_text}`, href: filmHref(row.id) });
    for (const frame of row.versions) {
      out.push({ kind: 'version', text: searchWords(`${frame.label} ${row.name}`), label: `${frame.label} \u00b7 ${row.name}`,
        meta: frame.mark === 'file' ? (ROLE_TEXT[frame.role] || frame.role || 'file') : (frame.mark_word || ''), href: filmHref(row.id, { v: frame.label }) });
    }
  }
  return out;
}

function buildSearch() {
  const box = $('#search');
  const listId = 'search-list';
  const input = h('input', {
    type: 'search', id: 'search-input', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false',
    'aria-controls': listId, autocomplete: 'off', spellcheck: 'false', placeholder: 'Search films and versions',
    'aria-label': 'Search films, versions and jobs',
  });
  const list = h('ul', { class: 'search-list', id: listId, role: 'listbox', 'aria-label': 'Matches', hidden: true });
  let matches = [];
  let active = -1;
  const close = () => {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  };
  const highlight = (index) => {
    active = index;
    $$('[role="option"]', list).forEach((option, i) => option.setAttribute('aria-selected', String(i === index)));
    const option = $(`#search-opt-${index}`, list);
    if (option) { input.setAttribute('aria-activedescendant', option.id); option.scrollIntoView({ block: 'nearest' }); }
  };
  const open = (item) => {
    close();
    input.value = '';
    if (item.kind === 'files') state.baseKey = null;
    go(item.href);
  };
  const update = () => {
    const query = input.value.trim();
    const words = searchWords(query).split(/\s+/).filter(Boolean);
    if (!words.length) { close(); return; }
    const all = searchIndex().filter((item) => words.every((w) => item.text.includes(w)));
    matches = [...all.filter((m) => m.kind === 'film').slice(0, 6), ...all.filter((m) => m.kind === 'version').slice(0, 8)];
    const files = { kind: 'files', label: `Search files for \u201c${query}\u201d in List`, meta: matches.length ? 'every video file on this Mac' : 'no film or version matches; files may', href: hrefs.films({ mode: 'list', q: query }) };
    matches.push(files);
    fill(list, ...matches.map((item, index) => h('li', {
      id: `search-opt-${index}`, role: 'option', 'aria-selected': 'false', class: item.kind === 'files' ? 'files-option' : null,
      on: { mousedown: (event) => { event.preventDefault(); open(item); } },
    }, h('span', { text: item.label }), h('span', { class: 'meta', text: item.meta }))));
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    highlight(matches.length ? 0 : -1);
  };
  input.addEventListener('input', update);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' && matches.length) { event.preventDefault(); if (list.hidden) update(); highlight(Math.min(matches.length - 1, active + 1)); }
    else if (event.key === 'ArrowUp' && matches.length) { event.preventDefault(); highlight(Math.max(0, active - 1)); }
    else if (event.key === 'Enter' && active >= 0 && matches[active]) { event.preventDefault(); open(matches[active]); }
    else if (event.key === 'Escape') {
      if (!list.hidden) { event.preventDefault(); event.stopPropagation(); close(); return; }
      if (narrowSearch()) { event.preventDefault(); setSearchOpen(false); $('#search-toggle').focus(); return; }
      input.blur();
    }
  });
  input.addEventListener('blur', () => window.setTimeout(() => {
    close();
    if (narrowSearch() && !input.value && !box.contains(document.activeElement)) setSearchOpen(false);
  }, 120));
  fill(box, icon('search'), input, h('kbd', { 'aria-hidden': 'true', text: '/' }), list);
  /* Below 760 px the field folds into an icon button; it opens full width under the masthead. */
  const toggle = h('button', {
    type: 'button', id: 'search-toggle', class: 'search-toggle', 'aria-label': 'Search films and versions', 'aria-expanded': 'false', 'aria-controls': 'search',
    on: { click: () => { const open = !$('#masthead').classList.contains('search-open'); setSearchOpen(open); if (open) input.focus(); } },
  }, icon('search'));
  box.before(toggle);
}

function narrowSearch() {
  return window.matchMedia('(max-width: 760px)').matches;
}

function setSearchOpen(open) {
  $('#masthead').classList.toggle('search-open', open);
  const toggle = $('#search-toggle');
  if (toggle) toggle.setAttribute('aria-expanded', String(open));
}

function focusSearch() {
  if (narrowSearch()) setSearchOpen(true);
  $('#search-input')?.focus();
}

/* ------------------------------------------------------------------ polling */

function schedulePoll() {
  clearTimeout(state.timer);
  if (document.hidden) return;
  state.timer = window.setTimeout(poll, POLL_MS_ROOM);
}

async function poll() {
  const seq = renderSeq;
  try {
    await loadFilms(true);
    if (seq !== renderSeq) return;
    preserveFocus(renderMasthead);
    if (state.view?.refresh) await state.view.refresh(seq);
    if (state.drawer?.refresh && seq === renderSeq) await state.drawer.refresh(seq);
    $('#stale').hidden = true;
  } catch {
    $('#stale').hidden = false;
  } finally {
    if (seq === renderSeq) schedulePoll();
  }
}

/* ------------------------------------------------------------------ keyboard */

const TYPING_FIELDS = 'input, textarea, select, [contenteditable="true"], [contenteditable=""]';
const CONTROLS = 'button, a[href], summary, video, audio, [role="tab"], [role="slider"], input, select, textarea, [tabindex]:not([tabindex="-1"])';

function roomShortcut(tab) {
  const room = state.room;
  if (!room || !room.tabs.some((t) => t.id === tab)) return false;
  room.focusAfter = 'tab';
  room.select(tab === 'decide' ? decideQuery(room) : { tab });
  return true;
}

function onKeydown(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  const target = event.target instanceof Element ? event.target : null;
  if (event.key === 'Escape') {
    if (state.drawer && !document.querySelector('dialog[open]')) { event.preventDefault(); closeNodeRoute(); }
    return;
  }
  if (document.querySelector('dialog[open]') || state.drawer) return;
  if (target && target.closest(TYPING_FIELDS)) return;
  const onControl = Boolean(target && target !== document.body && target.closest(CONTROLS));
  const video = state.video;
  switch (event.key) {
    case ' ':
      if (video && !onControl) { event.preventDefault(); if (video.paused) video.play().catch(() => {}); else video.pause(); }
      break;
    case 'ArrowLeft':
    case 'ArrowRight':
      if (video && !onControl) {
        event.preventDefault();
        seekTo(video.currentTime + (event.shiftKey ? 5 : 1) * (event.key === 'ArrowLeft' ? -1 : 1), { quiet: true });
      }
      break;
    case 'n': case 'N':
      if (state.view?.noteAtTime) { event.preventDefault(); state.view.noteAtTime(); }
      break;
    case 'g': case 'G':
      if (roomShortcut('pipeline')) event.preventDefault();
      break;
    case 'c': case 'C':
      if (roomShortcut('compare')) event.preventDefault();
      break;
    case 'd': case 'D':
      if (roomShortcut('decide')) event.preventDefault();
      break;
    case '/':
      event.preventDefault();
      focusSearch();
      break;
    case '?':
      event.preventDefault();
      openHelp();
      break;
    default:
      break;
  }
}

/* ------------------------------------------------------------------ the node drawer: a modal sheet */

function closeNodeRoute() {
  if (state.room && state.route && state.route.query.get('n')) state.room.select({ n: null });
  else closeDrawer();
}

async function openDrawer(pid, nodeId, version, seq) {
  const key = `${pid}|${nodeId}|${version || ''}`;
  if (state.drawer && state.drawer.key === key && state.drawer.el.isConnected) return;
  const returnFocus = state.drawer ? state.drawer.returnFocus : document.activeElement;
  closeDrawer({ restore: false });
  const url = `/api/projects/${enc(pid)}/nodes/${enc(nodeId)}?version=${enc(version || '')}`;
  let data;
  try { data = await getJSON(url); } catch (error) { data = { error: error.message }; }
  if (seq !== undefined && seq !== renderSeq) return;
  const titleId = nextId('drawer-title');
  const panel = h('aside', { class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId });
  const backdrop = h('div', { class: 'drawer-backdrop', on: { click: closeNodeRoute } });
  const drawer = { key, el: panel, data, tab: data.default_tab || 'evidence', returnFocus, returnKey: `node:${nodeId}`, titleId };
  drawer.refresh = async (pollSeq) => {
    let next;
    try { next = await getJSON(url); } catch { return; }
    if (pollSeq !== renderSeq || state.drawer !== drawer) return;
    if (JSON.stringify(next) !== JSON.stringify(drawer.data)) { drawer.data = next; preserveFocus(() => drawDrawer(drawer)); }
  };
  state.drawer = drawer;
  drawDrawer(drawer);
  panel.addEventListener('keydown', trapFocus);
  $('#drawer-root').replaceChildren(backdrop, panel);
  $('#shell').inert = true;
  document.body.classList.add('drawer-open');
  const title = $('.drawer-head h2', panel);
  if (title) title.focus({ preventScroll: true });
}

function closeDrawer({ restore = true } = {}) {
  const drawer = state.drawer;
  if (!drawer) return;
  state.drawer = null;
  $('#drawer-root').replaceChildren();
  $('#shell').inert = false;
  document.body.classList.remove('drawer-open');
  if (!restore) return;
  const target = document.querySelector(`[data-focus-key="${CSS.escape(drawer.returnKey)}"]`)
    || (drawer.returnFocus && drawer.returnFocus.isConnected ? drawer.returnFocus : null);
  if (target) target.focus();
}

function drawDrawer(drawer) {
  const data = drawer.data;
  const close = h('button', {
    type: 'button', class: 'icon-btn drawer-close', 'aria-label': 'Close the node details', 'data-focus-key': 'drawer-close',
    on: { click: closeNodeRoute },
  }, icon('close'));
  if (data.error) {
    fill(drawer.el, h('div', { class: 'drawer-head' },
      h('h2', { id: drawer.titleId, tabindex: '-1', text: 'This node is unavailable' }), close), h('p', { text: data.error }));
    return;
  }
  const node = data.node;
  if (node.gate_state) annotateGate(node.gate_state, data.version, state.room ? roomDecisions(state.room) : []);
  const truth = nodeTruth(node);
  const status = truth.status;
  /* The head says what, where and why, once; the tabs hold the evidence behind it. */
  const head = h('div', { class: 'drawer-head' },
    h('h2', { id: drawer.titleId, tabindex: '-1', text: nodeTitle(node) }),
    close,
    h('p', { class: 'meta', text: [node.phase, node.kind, data.version ? `version ${data.version}` : 'no version'].filter(Boolean).join(' \u00b7 ') }),
    h('div', { class: 'status-line' }, statusMark(truth.mark, truth.label), h('span', { text: nodeReason(node) })),
    h('p', { class: 'provenance' }, provenanceText(node)));
  const kids = [head];
  if (node.gate_state) kids.push(gateSummary(node.gate_state, data.pid, data.version, node));
  const failing = data.gates.filter((gate) => !gate.pass).length;
  const workFiles = (data.evidence.files || []).filter((file) => !file.dir).length;
  const reviewFiles = (data.evidence.review_files || []).length + (data.evidence.qa_file ? 1 : 0);
  const evidenceUnits = [workFiles ? plural(workFiles, 'work file') : null, reviewFiles ? plural(reviewFiles, 'review file') : null].filter(Boolean).join(', ') || 'no files';
  const events = data.telemetry && data.telemetry.total ? data.telemetry.total : 0;
  const order = ['evidence', 'checks', 'reviews', 'contract', 'notes'];
  const preferred = { evidence: 'evidence', checks: 'checks', reviews: 'reviews', contract: 'contract', reactions: 'notes' }[drawer.tab] || 'evidence';
  kids.push(tabs('Node details', [
    { id: 'evidence', label: 'Evidence', units: evidenceUnits, render: () => evidenceTab(data) },
    { id: 'checks', label: 'Checks', units: [plural(data.gates.length, 'check'), failing ? `${failing} failing` : null, events ? plural(events, 'event') : null].filter(Boolean).join(', '), render: () => checksTab(data) },
    { id: 'reviews', label: 'Reviews', units: `${plural(data.scores.length, 'score')}, ${plural(data.defects.length, 'finding')}`, render: () => nodeReviewsTab(data) },
    { id: 'contract', label: 'Contract', units: 'node spec', render: () => contractTab(data.contract) },
    { id: 'notes', label: 'Notes', units: String(data.reactions.length), render: () => nodeReactionsTab(data) },
  ].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id)), preferred, (id) => { drawer.tab = id === 'notes' ? 'reactions' : id; }));
  fill(drawer.el, ...kids);
}

/* A human gate in the drawer and the pipeline list: its state in words and the way to decide it. */
function gateSummary(gate, pid, version, node = null) {
  const decide = needsOwner(gate);
  const forVersion = gate.release ? (gate.version || version) : null;
  const words = gate.release && forVersion ? `Release ${forVersion}` : (gate.decision || nodeDisplay(gate) || 'Decision');
  return h('section', { class: `gate-card state-${gate.superseded_by ? 'superseded' : gate.state}`, 'aria-label': `Human gate ${nodeTag(gate.num)}` },
    h('h3', { text: words }),
    h('p', { class: 'meta', text: [`Human gate ${nodeTag(gate.num)}`, forVersion].filter(Boolean).join(' \u00b7 ') }),
    gateStatusLine(gate, node),
    recordedDecision(gate),
    definitionList([['Approver', gate.approver], ['Outcomes', joinList(gate.outcomes)], ['Engine status', engineStatus(gate.engine_status, gate.engine_status_label)]]),
    decide && pid ? h('p', {}, h('a', { class: 'btn decide', href: filmHref(`job-${pid}`, { v: forVersion || version, tab: 'decide', g: gate.node }) }, 'Decide')) : null,
    !decide && pid && gate.state === 'recorded' && !gate.superseded_by ? h('p', {}, h('a', { class: 'btn', href: filmHref(`job-${pid}`, { v: forVersion || version, tab: 'decide', g: gate.node }) }, 'Your decision')) : null);
}

/* ------------------------------------------------------------------ Inbox */

/* One factual sentence per decision: how long the engine has waited, or how the version did (reviews, open findings
   by level, runtime against the brief). */
function decisionSentence(decision, row = null, { runtime = true } = {}) {
  const brief = row && row.brief_runtime;
  if (decision.kind === 'gate') {
    if (decision.reason === 'provisional approval expired') return decision.detail || 'The provisional approval expired; confirm or reject it.';
    if (decision.decided) {
      return decision.engine_waits
        ? 'You have decided; the engine run still waits at this gate until it is approved in the engine.'
        : 'You have decided; your decision is recorded for the producer.';
    }
    const note = decision.run_note;
    let text = 'The engine run waits here for you';
    if (note && note.since) text = `The engine run has waited here since ${fmtDay(note.since)}${note.outside ? ` while ${plural(note.outside, 'version')} were built outside it` : ''}`;
    else if (decision.since) text = `The engine run has waited here since ${fmtDay(decision.since)}`;
    const window = briefWindow(brief);
    if (runtime && window && brief.node && brief.node === decision.node) {
      const latest = (row.versions || []).find((f) => f.has_master !== false && Number(f.duration_s) > 0);
      if (latest && outsideWindow(latest.duration_s, window)) text += `; this gate ${windowWords(window)}, and the latest version, ${latest.label}, runs ${fmtDuration(latest.duration_s)}`;
    }
    return `${text}.`;
  }
  if (decision.kind === 'release') {
    const facts = decision.facts;
    if (!facts) return `${decision.version} has a complete review set.`;
    const parts = [];
    if (facts.reviews) parts.push(`${facts.passing} of ${plural(facts.reviews, 'review')} ${facts.passing === 1 ? 'passes' : 'pass'}`);
    parts.push(openFindingsPhrase(facts.open_levels));
    const phrase = runtime ? runtimePhrase(facts.duration_s, brief) : null;
    if (phrase) parts.push(phrase);
    return `${sentenceCase(parts.join('; '))}.`;
  }
  const parts = [];
  for (const level of LEVELS) {
    const count = decision.levels[level];
    if (count) parts.push(`${count} ${level === 'P?' ? 'unrated' : level} finding${count === 1 ? '' : 's'}`);
  }
  if (decision.rework) parts.push(`${plural(decision.rework, 'reviewer')} asking for rework`);
  return parts.length ? `${joinList(parts)} wait for your answer.` : 'Findings wait for your answer.';
}

function decisionHref(decision) {
  return filmHref(decision.film_id, { v: decision.version, tab: 'decide', g: decision.kind === 'findings' ? null : decision.node });
}

function gateSubject(decision) {
  return nodeDisplay(decision) || decision.words;
}

/* What a decision asks, without the node tag the frame already shows. */
function askWords(decision) {
  if (decision.kind === 'release') return `Release ${decision.version}`;
  return String(decision.words || '').replace(/\s*\(N\d+\)\s*$/, '');
}

function decisionCard(decision, first = false) {
  const versionless = !decision.version;
  const row = state.films ? state.films.rows.find((r) => r.id === decision.film_id) : null;
  const versionFrame = row && decision.version ? row.versions.find((f) => f.label === decision.version) : null;
  const frame = versionless
    ? { poster: null, label: gateSubject(decision), has_master: true }
    : { ...(versionFrame || {}), poster: decision.poster || (versionFrame && versionFrame.poster) || null, label: decision.version, duration_s: decision.duration_s, has_master: true };
  return h('article', { class: `decision${versionless ? ' gate-card-frame' : ''}`, 'aria-label': `${decision.film}: ${decision.words}` },
    frameBox(frame, { label: versionless ? gateSubject(decision) : decision.version, meta: versionless ? nodeTag(decision.node_num) : null, preferStill: !versionless }),
    h('div', { class: 'decision-body' },
      h('h3', { text: askWords(decision) }),
      h('p', { class: 'film-name', text: decision.film }),
      h('p', { class: 'state', text: decisionSentence(decision, row) }),
      h('div', { class: 'btn-row' }, h('a', {
        class: `btn decide${first ? ' primary' : ''}`, href: decisionHref(decision), 'aria-label': `Decide: ${decision.words}, ${decision.film}`,
      }, statusMark('needs', 'Decide')))));
}

function fyiGroupOf(item) {
  if (String(item.reason || '').startsWith('superseded')) return 'superseded';
  if (item.kind === 'in_flight') return 'progress';
  if (item.kind === 'unreviewed') return 'unreviewed';
  return 'waiting';
}

const FYI_GROUPS = [
  { id: 'waiting', title: 'Waiting for the producer', why: 'Open findings on versions being reworked or reviewed, and P3 findings the producer fixes next round. Answer any of them if you disagree.' },
  { id: 'progress', title: 'In progress', why: 'Versions being built right now; their reviews are not complete.' },
  { id: 'unreviewed', title: 'Not reviewed', why: 'Versions with a master and no reviews yet; they join the decisions once reviewed.' },
  { id: 'superseded', title: 'Superseded', why: 'Findings on versions a newer version replaced. History, kept for reference.' },
];

function fyiItem(item) {
  const filmId = `job-${item.pid}`;
  const actionsId = nextId('gi-actions');
  const slot = h('div', { class: 'gi-slot' });
  const actions = h('div', { class: 'gi-actions', id: actionsId, hidden: true });
  const finding = item.kind === 'open_defect' && item.finding;
  const label = finding ? `${item.level || ''} finding on ${item.version}` : (item.version || item.project);
  const open = (option) => {
    const current = slot.firstChild;
    if (current && current.dataset.option === option) { fill(slot); return; }
    const box = finding
      ? composer({
        pid: item.pid, version: item.version, set: 'finding', select: option,
        target: cleanTarget({ type: 'finding', id: item.finding, version: item.version, t: item.t ?? null }),
        label: `${item.project}: ${item.title}`,
      })
      : composer({
        pid: item.pid, version: item.version, set: 'comment_only', select: 'note',
        target: cleanTarget(item.version ? { type: 'version', id: item.version } : { type: 'node', id: item.node, version: item.version }),
        label: `${item.project}: ${item.title}`,
      });
    box.dataset.option = option;
    fill(slot, box);
    box.focusText();
  };
  const watchHref = item.version ? filmHref(filmId, { v: item.version, tab: 'watch', f: item.finding || null, t: item.t ?? null }) : filmHref(filmId);
  if (finding) {
    actions.append(
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Agree with ${label}`, on: { click: () => open('agree') } }, icon('check'), 'Agree'),
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Disagree with ${label}`, on: { click: () => open('disagree') } }, icon('cross'), 'Disagree'),
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Note on ${label}`, on: { click: () => open('note') } }, icon('note'), 'Note'));
  } else if (item.version || item.node) {
    actions.append(h('button', { type: 'button', class: 'btn small', 'aria-label': `Note on ${label}`, on: { click: () => open('note') } }, icon('note'), 'Note'));
  }
  actions.append(h('a', { class: 'btn small quiet', href: watchHref }, item.version ? `Open ${item.version}` : 'Open the film'));
  const group = fyiGroupOf(item);
  const select = h('button', {
    type: 'button', class: 'gi-select', 'aria-expanded': 'false', 'aria-controls': actionsId,
    on: {
      click: () => {
        const show = actions.hidden;
        actions.hidden = !show;
        select.setAttribute('aria-expanded', String(show));
        if (!show) fill(slot);
      },
    },
  },
  item.level ? levelChip(item.level) : group === 'progress' ? markGlyphLabel('in_progress') : null,
  h('span', { class: 'gi-title', text: item.level ? plainWords(item.title).replace(/^\s*P[1-4?]\s*[:.\u2013-]\s*/i, '') : plainWords(item.title) }));
  return h('li', { class: 'group-item' },
    h('div', { class: 'gi-row' }, select,
      h('span', { class: 'meta' }, item.project, item.version ? ' \u00b7 ' : '', item.version
        ? h('span', { class: group === 'superseded' ? 'struck' : null, text: item.version }) : null,
      item.reason ? ` \u00b7 ${plainWords(item.reason)}` : ''),
      ownerOfVersion(item.pid, item.version) ? ownerMark(ownerOfVersion(item.pid, item.version)) : null),
    actions, slot);
}

/* The owner's mark on a job's version, from the contact sheet. */
function ownerOfVersion(pid, version) {
  const row = state.films && pid && version ? state.films.rows.find((r) => r.kind === 'job' && r.pid === pid) : null;
  const frame = row ? row.versions.find((f) => f.label === version) : null;
  return frame ? frame.owner || null : null;
}

/* "It runs 01:33.1 against the brief's 65 s cap; the script targets 90 s." Only when the version is outside the brief. */
function runtimeClause(brief, duration, scriptTarget, { sentence = true } = {}) {
  const window = briefWindow(brief);
  if (!window || !outsideWindow(duration, window)) return '';
  const limit = window.cap ? `the brief's ${fmtSeconds(window.cap)} cap` : `the brief's ${fmtSeconds(window.target)} target`;
  const script = Number(scriptTarget) > 0 && Math.abs(scriptTarget - (window.cap || window.target)) > 0.5 ? `; the script targets ${fmtSeconds(scriptTarget)}` : '';
  return `${sentence ? 'It runs' : 'runs'} ${fmtDuration(duration)} against ${limit}${script}${sentence ? '.' : ''}`;
}

function scriptTargetOf(window) {
  return window && /script target/.test(String(window.source || '')) ? window.target : null;
}

function markGlyphLabel(mark) {
  return h('span', { class: `mark m-${mark}`, 'aria-hidden': 'true' }, markGlyph(mark));
}

function fyiGroups(items) {
  const buckets = { waiting: [], progress: [], unreviewed: [], superseded: [] };
  for (const item of items) buckets[fyiGroupOf(item)].push(item);
  return h('div', { class: 'groups' }, FYI_GROUPS.filter((group) => buckets[group.id].length).map((group) => {
    const list = buckets[group.id];
    return h('details', { class: 'group', 'data-group': group.id },
      h('summary', {}, h('span', { class: 'g-title', text: group.title }), h('span', { class: 'count', text: String(list.length) }),
        h('span', { class: 'g-why', text: group.why })),
      list.length ? h('ul', { class: 'group-list' }, list.map(fyiItem)) : h('p', { class: 'empty group-list', text: 'Nothing here right now.' }));
  }));
}

const COPY_AFTER_MINUTES = 15;

/* Replies where the owner looks. Under the decision band one compact line sums up every producer; below Recent films
   each job lists its notes, one line each (state, target, the producer's first sentence), linking to the thread. */
function nb(count, words) {
  return `${count}\u00a0${words}`;
}

function activeProducerJobs(summary) {
  return summary && Array.isArray(summary.jobs) ? summary.jobs.filter((job) => job.open || job.picked_up || job.resolved) : [];
}

function producersLine(summary) {
  const jobs = activeProducerJobs(summary);
  if (!jobs.length) return null;
  const sum = (key) => jobs.reduce((total, job) => total + (job[key] || 0), 0);
  const oldest = jobs.map((job) => job.oldest_open_ts).filter(Boolean).sort()[0];
  const parts = [];
  if (sum('open')) parts.push(`${nb(sum('open'), sum('open') === 1 ? 'note' : 'notes')} waiting for the producer${oldest ? `, the oldest for\u00a0${ageWords(oldest)}` : ''}`);
  if (sum('picked_up')) parts.push(nb(sum('picked_up'), 'picked up'));
  if (sum('resolved')) parts.push(nb(sum('resolved'), 'addressed'));
  return h('p', { class: 'producers-line' }, h('span', { class: 'label', text: 'Producers' }), ' ', `${parts.join(' \u00b7 ')}.`, ' ',
    h('button', { type: 'button', class: 'linkish', 'data-focus-key': 'producers:jump',
      on: { click: () => { const box = document.getElementById('producers-h'); if (box) { box.scrollIntoView({ block: 'start' }); box.focus({ preventScroll: true }); } } } }, 'See each note'));
}

function producerNoteLine(item) {
  const chip = stateChipFor(item.state, item.reopened, item.resolved_in);
  const said = item.state === 'open' ? `${item.kind_label}: \u201c${item.text}\u201d` : item.reply ? `\u201c${item.reply}\u201d` : item.kind_label;
  return h('li', {}, h('a', { class: 'producer-note', href: `#/activity?r=${enc(item.id)}`, 'aria-label': `${item.target_words}: ${stateWords(item.state, item.reopened, item.resolved_in)}. Open the thread in Activity.` },
    chip, h('span', { class: 'pn-target', text: item.target_words }), h('span', { class: 'pn-said', text: said })));
}

function producersBlock(summary) {
  const jobs = activeProducerJobs(summary);
  if (!jobs.length) return null;
  return h('section', { class: 'section producers', 'aria-labelledby': 'producers-h' },
    h('div', { class: 'section-head' }, h('h2', { id: 'producers-h', tabindex: '-1', text: 'Producers' }),
      h('span', { class: 'meta', text: 'your notes, and where each producer is with them' })),
    jobs.map((job) => {
      const box = h('div', { class: 'copy-box' });
      const late = job.oldest_open_ts && Date.now() - Date.parse(job.oldest_open_ts) > COPY_AFTER_MINUTES * 60000;
      return h('div', { class: 'producer-job' },
        h('h3', { class: 'h3-like' }, h('a', { href: filmHref(`job-${job.pid}`), text: job.name })),
        h('ul', { class: 'producer-notes' }, (job.items || []).map(producerNoteLine)),
        late ? h('button', {
          type: 'button', class: 'btn small', 'data-focus-key': `producer-copy:${job.pid}`,
          on: { click: () => copyMarkdownFrom(`/api/inbox?pid=${enc(job.pid)}&format=md`, box, 'Copied for the producer') },
        }, icon('copy'), 'Copy for the producer') : null,
        box);
    }));
}

async function viewInbox(target, seq) {
  const [films, needs, producers] = await Promise.all([loadFilms(true), getJSON('/api/needs-you'), getJSON('/api/inbox/summary?items=1').catch(() => null)]);
  if (seq !== renderSeq) return;
  renderMasthead();
  const decisions = films.decisions;
  const fyi = needs.filter((item) => item.group === 'fyi');
  const count = decisions.length;
  const head = h('header', { class: 'page-head' },
    h('h1', { class: 'page-title', tabindex: '-1', text: count ? `${count} decision${count === 1 ? '' : 's'}` : 'No decisions' }),
    h('p', { class: 'deck', text: count
      ? 'Each one waits for you. Decide beside the film, its verdicts and what changed.'
      : 'Nothing waits for you. Below the recent films, the groups show what the producer is doing.' }));
  const band = count
    ? h('section', { 'aria-labelledby': 'band-h' }, h('h2', { id: 'band-h', class: 'visually-hidden', text: 'Decisions waiting for you' }), h('div', { class: 'band' }, decisions.map((decision, index) => decisionCard(decision, index === 0))))
    : h('p', { class: 'none-waiting sub', text: 'When a gate waits for you, or a release is ready, it appears here with its film.' });
  const recent = films.rows.slice(0, 4);
  fill(target, head, band, producersLine(producers),
    h('section', { class: 'section sheet-peek', 'aria-labelledby': 'recent-h' },
      h('div', { class: 'section-head' }, h('h2', { id: 'recent-h', text: 'Recent films' }),
        h('a', { href: hrefs.films(), text: `All ${films.counts.all} films` })),
      recent.map((row) => filmRow(row, { lazy: false, compact: true }))),
    producersBlock(producers),
    fyi.length ? h('section', { class: 'section', 'aria-labelledby': 'fyi-h' },
      h('div', { class: 'section-head' }, h('h2', { id: 'fyi-h', text: 'For your information' }),
        h('span', { class: 'meta', text: `${plural(fyi.length, 'item')}, not counted as decisions` })),
      fyiGroups(fyi)) : null);
  state.view = {
    refresh: async (pollSeq) => {
      if (pollSeq !== renderSeq) return;
      const changed = decisionCount() !== count;
      if (changed && !document.querySelector('.group-item .composer')) await viewInbox(target, pollSeq);
    },
  };
}

/* ------------------------------------------------------------------ Films: the contact sheet */

const VERB_NOUNS = { approve: 'approval', select: 'selection', choose: 'choice', confirm: 'confirmation', review: 'review', decide: 'decision' };

/* "Your approval of the objective ... (N03) is waiting." rather than an instruction with "waits for you" tacked on. */
function waitingSentence(decision) {
  if (decision.kind === 'release') return `Your release decision for ${decision.version} is waiting.`;
  if (decision.kind === 'findings') return `Your answer to the findings on ${decision.version} is waiting.`;
  const words = String(decision.words || 'this gate');
  const match = words.match(/^(\w+)\s+(.+)$/);
  const noun = match && VERB_NOUNS[match[1].toLowerCase()];
  return noun ? `Your ${noun} of ${match[2]} is waiting.` : `Your decision on ${words} is waiting.`;
}

const LATEST_WORDS = { passed: 'passed', rework: 'needs rework', not_reviewed: 'is not reviewed yet', partial: 'is partly reviewed', not_run: 'has no master', in_progress: 'is in progress' };

function rowSentence(row) {
  const parts = [];
  if (row.kind === 'job') {
    if (row.decisions.length) parts.push(waitingSentence(row.decisions[0]));
    if (row.in_progress.length) parts.push(`${joinList(row.in_progress)} ${row.in_progress.length === 1 ? 'is' : 'are'} in progress.`);
    if (!row.decisions.length && !row.in_progress.length && row.versions.length) {
      const latest = row.versions[0];
      parts.push(`The latest version, ${latest.label}, ${LATEST_WORDS[latest.mark] || String(latest.mark_word || 'has no status').toLowerCase()}.`);
    }
    const note = row.run_note;
    if (note && note.since) {
      parts.push(`The engine run has waited at ${nodeTag(note.num)} since ${fmtDay(note.since)}${note.outside ? ` while ${plural(note.outside, 'version')} were built outside it` : ''}.`);
    }
  } else {
    parts.push(row.latest ? `The latest version, ${row.latest}, is from ${fmtDay(row.latest_mtime)}.` : 'No version has video yet.');
    if (row.archived) parts.push('It is an archived copy.');
  }
  return parts.join(' ');
}

/* One frame of a strip: the poster or a typographic frame, with its label and mark underneath. */
/* The owner's verdict rides as a second mark: under the first in the sheet, on the frame in the room's rail, whose
   height is budgeted (the full words are in the cell's name, the Table and the version sentence). */
function frameCell(row, frame, { selected = false, latest = false, tabindex = -1, href = null, onPick = null, preferStill = true, ownerStyle = 'row' } = {}) {
  const box = frameBox(frame, { preferStill });
  if (frame.owner && ownerStyle === 'overlay') box.append(ownerMark(frame.owner, { compact: true }));
  const cell = h('a', {
    class: 'frame-cell', href: href || filmHref(row.id, { v: frame.label }), tabindex: String(tabindex),
    'aria-current': selected ? 'true' : null, 'data-label': frame.label,
    'aria-label': `${row.name}, ${frame.label}${frame.mark_word ? `, ${frame.mark_word}` : frame.role ? `, ${ROLE_TEXT[frame.role] || frame.role}` : ''}${frame.owner ? `, ${ownerWords(frame.owner)}` : ''}${latest ? ', latest' : ''}${selected ? ', selected' : ''}`,
    on: onPick ? { click: (event) => { event.preventDefault(); onPick(frame); } } : null,
  },
  box,
  h('span', { class: 'fc-cap' }, h('span', { class: 'fc-label', text: frame.label }), latest ? h('span', { class: 'latest', text: 'latest' }) : null),
  frameMark(frame),
  frame.owner && ownerStyle !== 'overlay' ? ownerMark(frame.owner) : null);
  return cell;
}

/* A strip never cuts a frame or its label without saying so: the edge that hides frames fades. */
function edgeFade(strip) {
  const update = () => {
    const before = strip.scrollLeft > 4;
    const after = strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 4;
    strip.dataset.fade = before && after ? 'both' : before ? 'left' : after ? 'right' : 'none';
  };
  strip.addEventListener('scroll', () => window.requestAnimationFrame(update), { passive: true });
  if (window.ResizeObserver) new ResizeObserver(update).observe(strip);
  window.requestAnimationFrame(update);
  return strip;
}

/* A sideways strip with one tab stop; arrow keys move between frames. */
function frameStrip(row, frames, { selected = null, label, className = 'strip', onPick = null, hrefOf = null, autoScroll = true, preferStill = true, ownerStyle = 'row' } = {}) {
  const strip = h('div', { class: className, role: 'group', 'aria-label': label || `${row.name}: versions, newest first` });
  const focusIndex = Math.max(0, frames.findIndex((frame) => frame.label === selected));
  const cells = frames.map((frame, index) => frameCell(row, frame, {
    selected: frame.label === selected, latest: index === 0, tabindex: index === focusIndex ? 0 : -1,
    href: hrefOf ? hrefOf(frame) : null, onPick, preferStill, ownerStyle,
  }));
  strip.append(...cells);
  strip.addEventListener('keydown', (event) => rovingKeys(event, cells));
  if (selected && autoScroll) window.requestAnimationFrame(() => { const cell = cells[focusIndex]; if (cell) strip.scrollLeft = Math.max(0, cell.offsetLeft - strip.offsetLeft - 8); });
  return strip;
}

let rowObserver = null;
function observeRow(placeholder, render) {
  if (!('IntersectionObserver' in window)) { render(); return; }
  if (!rowObserver) {
    rowObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        rowObserver.unobserve(entry.target);
        entry.target._render?.();
      }
    }, { rootMargin: '600px 0px' });
  }
  placeholder._render = render;
  rowObserver.observe(placeholder);
}

const NAME_STOP = new Set(['film', 'the', 'a', 'an', 'of', 'and', 'round', 'run', 'vpg', 'video', 'special', 'edition', 'summary', 'second', 'minute', 'one']);

function nameTokens(name) {
  return new Set(searchWords(name).split(/[^a-z0-9]+/).filter((token) => token.length > 1 && !NAME_STOP.has(token) && !/^\d+$/.test(token)));
}

/* Where a row comes from, said only when another row has a similar name (a job and the folder around it, say). */
function sourceNotes(rows) {
  if (rows._notes) return rows._notes;
  const tokens = new Map(rows.map((row) => [row.id, nameTokens(row.name)]));
  const similar = (a, b) => {
    const [small, large] = a.size <= b.size ? [a, b] : [b, a];
    return small.size >= 2 && [...small].every((token) => large.has(token));
  };
  const jobParents = rows.filter((row) => row.kind === 'job' && row.rel_path).map((row) => row.rel_path.split('/').slice(0, -1).join('/').toLowerCase());
  const notes = new Map();
  for (const row of rows) {
    if (!rows.some((other) => other !== row && similar(tokens.get(row.id), tokens.get(other.id)))) continue;
    let note;
    if (row.kind === 'job') note = 'VPG job';
    else if (row.archived) note = 'archive copy';
    else if (jobParents.some((parent) => parent && `${String(row.rel_path || '').toLowerCase()}/`.endsWith(`/${parent}/`))) note = 'video folder outside the VPG job';
    else note = `${String(row.area || 'library').toLowerCase()} folder`;
    notes.set(row.id, note);
  }
  Object.defineProperty(rows, '_notes', { value: notes, enumerable: false });
  return notes;
}

function filmRow(row, { lazy = true, compact = false } = {}) {
  const titleId = nextId('row-title');
  const section = h('section', { class: `film-row${compact ? ' compact' : ''}`, 'aria-labelledby': titleId });
  const note = state.films ? sourceNotes(state.films.rows).get(row.id) : null;
  const head = h('div', { class: 'film-row-head' },
    h(compact ? 'h3' : 'h2', { id: titleId }, h('a', { href: filmHref(row.id), text: row.name }), note ? h('span', { class: 'row-source', text: ` \u00b7 ${note}` }) : null),
    h('div', { class: 'row-meta' },
      row.needs ? statusMark('needs', plural(row.needs, 'decision')) : null,
      h('span', { class: 'label', text: row.area }),
      h('span', { class: 'meta', text: row.counts_text })),
    compact ? null : h('p', { class: 'status', text: rowSentence(row) }));
  const slot = h('div', { class: 'strip-placeholder' });
  section.append(head, slot);
  const draw = () => slot.replaceWith(row.versions.length
    ? edgeFade(frameStrip(row, row.versions, { selected: row.focus || row.latest }))
    : h('p', { class: 'empty', text: 'No version with video yet.' }));
  if (lazy) observeRow(slot, draw); else draw();
  return section;
}

function filmPrefs() {
  const saved = prefs.films && typeof prefs.films === 'object' ? prefs.films : {};
  prefs.films = { filter: 'all', q: '', sort: 'newest', mode: 'sheet', ...saved };
  return prefs.films;
}

function scanStatus(library, onRescan) {
  const box = h('div', { class: 'scan-status' });
  if (!library || library.status === 'disabled' || library.enabled === false) {
    box.append(h('span', { text: 'The library is turned off on this server, so only jobs are listed.' }));
    return box;
  }
  const counts = library.counts;
  if (library.status === 'scanning') {
    const p = library.progress || {};
    const phase = p.phase === 'reading' ? `reading new or changed files, ${fmtCount(p.read)} of ${fmtCount(p.to_read)}`
      : p.phase === 'grouping' ? 'grouping into films and versions' : `${fmtCount(p.videos)} videos found in ${fmtCount(p.dirs)} folders`;
    box.append(statusMark('in_progress', 'Scanning'), h('span', { text: `${phase}; the list shows the last scan until it ends.` }));
  } else if (counts) {
    appendKids(box, h('span', { text: `${fmtCount(counts.videos)} videos on this Mac` }), library.scanned_at ? h('span', { class: 'meta' }, 'scanned ', timeEl(library.scanned_at)) : null);
  }
  if (onRescan) {
    box.append(h('button', { type: 'button', class: 'btn small', disabled: library.status === 'scanning', 'data-focus-key': 'films:rescan', on: { click: onRescan } }, icon('running'), 'Rescan'));
  }
  return box;
}

async function viewFilms(target, route, seq) {
  const films = await loadFilms(true);
  if (seq !== renderSeq) return;
  renderMasthead();
  const ui = filmPrefs();
  if (['all', 'needs', 'jobs', 'library', 'archive'].includes(route.query.get('filter'))) ui.filter = route.query.get('filter');
  if (['list', 'sheet'].includes(route.query.get('mode'))) ui.mode = route.query.get('mode');
  if (['newest', 'name', 'size'].includes(route.query.get('sort'))) ui.sort = route.query.get('sort');
  if (route.query.has('q')) ui.q = route.query.get('q') || '';
  /* The address mirrors the view, so a copied link or Back returns to the same filter, search, layout and sort. */
  const syncUrl = () => {
    const wanted = hrefs.films({ filter: ui.filter !== 'all' ? ui.filter : '', q: ui.q, mode: ui.mode !== 'sheet' ? ui.mode : '', sort: ui.sort !== 'newest' ? ui.sort : '' });
    if (location.hash !== wanted) history.replaceState(null, '', wanted);
  };
  let limit = SHEET_PAGE;
  const results = h('div');
  const statusBox = h('div');
  const otherBox = h('div');
  const counts = films.counts;
  const filterDefs = [['all', 'All', counts.all], ['needs', 'Needs you', counts.needs], ['jobs', 'Jobs', counts.jobs], ['library', 'Library', counts.library], ['archive', 'Archive', counts.archive]];
  const filterButtons = filterDefs.map(([id, label, count]) => h('button', {
    type: 'button', 'aria-pressed': String(ui.filter === id), 'data-focus-key': `films:filter:${id}`,
    on: {
      click: () => {
        ui.filter = ui.filter === id && !['all', 'needs'].includes(id) ? 'all' : id;
        savePrefs();
        limit = SHEET_PAGE;
        filterButtons.forEach((b, i) => b.setAttribute('aria-pressed', String(filterDefs[i][0] === ui.filter)));
        draw();
      },
    },
  }, label, h('span', { class: 'count', text: String(count) })));
  const search = h('input', { type: 'search', id: 'films-q', class: 'search-films', value: ui.q, autocomplete: 'off', placeholder: 'Film, version or folder', 'data-focus-key': 'films:q' });
  let debounce = null;
  search.addEventListener('input', () => { clearTimeout(debounce); debounce = window.setTimeout(() => { ui.q = search.value.trim(); savePrefs(); limit = SHEET_PAGE; draw(); }, 200); });
  const sort = h('select', { id: 'films-sort', 'data-focus-key': 'films:sort', on: { change: (event) => { ui.sort = event.target.value; savePrefs(); draw(); } } },
    [['newest', 'Newest'], ['name', 'Name'], ['size', 'Size']].map(([value, text]) => h('option', { value, text, selected: ui.sort === value })));
  const modeButtons = [['sheet', 'Sheet'], ['list', 'List']].map(([id, label]) => h('button', {
    type: 'button', 'aria-pressed': String(ui.mode === id), 'data-focus-key': `films:mode:${id}`,
    on: { click: () => { ui.mode = id; savePrefs(); modeButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === id))); draw(); } },
    dataset: { mode: id },
  }, label));
  const libraryOn = films.library && films.library.status !== 'disabled' && films.library.enabled !== false;

  const matches = (row) => {
    if (ui.filter === 'needs' && !row.needs) return false;
    if (ui.filter === 'jobs' && row.kind !== 'job') return false;
    if (ui.filter === 'library' && row.kind !== 'library') return false;
    if (ui.filter === 'archive' && !row.archived) return false;
    return matchesSearch(`${row.search} ${row.name} ${row.versions.map((f) => f.label).join(' ')}`, ui.q);
  };
  const sorted = (rows) => {
    const out = [...rows];
    if (ui.sort === 'name') out.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
    else if (ui.sort === 'size') out.sort((a, b) => (b.total_size || 0) - (a.total_size || 0));
    else out.sort((a, b) => String(b.latest_mtime || '').localeCompare(String(a.latest_mtime || '')));
    return out;
  };
  const drawSheet = () => {
    const rows = sorted(films.rows.filter(matches));
    const shown = rows.slice(0, limit);
    fill(results,
      h('p', { class: 'meta', text: `${plural(rows.length, 'film')} of ${films.rows.length}` }),
      shown.length ? shown.map((row) => filmRow(row)) : h('p', { class: 'empty', text: ui.q ? 'No film matches the search.' : 'No film matches this filter.' }),
      rows.length > shown.length ? h('div', { class: 'btn-row more-rows' }, h('button', {
        type: 'button', class: 'btn', 'data-focus-key': 'films:more', on: { click: () => { limit += SHEET_PAGE; drawSheet(); } },
      }, `Show ${Math.min(SHEET_PAGE, rows.length - shown.length)} more of ${rows.length - shown.length}`)) : null);
  };
  let listState = { items: [], total: 0, key: null };
  const drawList = async (append = false) => {
    if (!libraryOn) { fill(results, h('p', { class: 'empty', text: 'The list of files needs the library; it is turned off on this server.' })); return; }
    const key = JSON.stringify([ui.q, ui.sort, films.library.scanned_at]);
    if (!append || listState.key !== key) listState = { items: [], total: 0, key };
    const params = new URLSearchParams({ q: ui.q, sort: ui.sort === 'name' ? 'name' : ui.sort === 'size' ? 'size' : 'newest', offset: String(listState.items.length), limit: '60' });
    const page = await getJSON(`/api/library/items?${params}`);
    if (listState.key !== key || seq !== renderSeq) return;
    listState.items = listState.items.concat(page.items);
    listState.total = page.total;
    preserveFocus(() => fill(results,
      h('p', { class: 'meta', text: `${fmtCount(listState.total)} files match, ${fmtCount(listState.items.length)} shown` }),
      h('div', { class: 'scroll-x' }, h('table', { class: 'list-table' }, tableHead(['File', 'Role', 'Film and version', 'Duration', 'Size', 'Date']),
        h('tbody', {}, listState.items.map((item) => h('tr', {},
          h('td', { class: 'break' }, h('a', { href: libHref(item), title: item.path, text: item.name }),
            item.duplicate_of ? h('div', { class: 'meta', text: `same file as ${item.duplicate_of.place}` }) : null),
          h('td', {}, roleChip(item.role)),
          h('td', { class: 'break', text: item.film ? `${item.film} / ${item.version}` : 'Other videos' }),
          h('td', { class: 'num', text: fmtDuration(item.duration_s) }),
          h('td', { class: 'num', text: fmtBytes(item.size) }),
          h('td', { class: 'num' }, timeEl(item.mtime))))))),
      listState.total > listState.items.length ? h('div', { class: 'btn-row more-rows' }, h('button', {
        type: 'button', class: 'btn', 'data-focus-key': 'films:more-list', on: { click: () => drawList(true) },
      }, `Show 60 more of ${fmtCount(listState.total - listState.items.length)}`)) : null));
  };
  const drawOther = () => {
    const colls = (films.other || []).filter((c) => matchesSearch(c.label, ui.q));
    if (!libraryOn || !colls.length) { fill(otherBox); return; }
    const total = colls.reduce((sum, c) => sum + c.videos, 0);
    fill(otherBox, h('details', { class: 'other-box' },
      h('summary', {}, `Other videos`, h('span', { class: 'meta', text: `${plural(colls.length, 'folder')}, ${fmtCount(total)} videos` })),
      h('p', { class: 'sub', text: 'Clips without a master, recordings, evaluation clips, source captures and copies published on the portfolio site. They are not films, and every one still plays.' }),
      h('div', {}, colls.map(collectionBlock))));
  };
  function draw() {
    syncUrl();
    viewLabel.textContent = viewWords();
    if (ui.mode === 'list') drawList().catch((error) => fill(results, h('p', { class: 'empty', text: `Could not list the files: ${error.message}` })));
    else drawSheet();
    drawOther();
  }
  async function rescan() {
    try {
      const reply = await postJSON('/api/library/rescan', {});
      films.library = reply.status;
      announce('Library rescan started');
    } catch (error) {
      announce(error.status === 409 ? 'A scan is already running' : `Rescan failed: ${error.message}`);
    }
    if (state.films) state.films.library = films.library;
    renderMasthead();
    fill(statusBox, scanStatus(films.library, rescan));
  }
  fill(statusBox, scanStatus(films.library, libraryOn ? rescan : null));
  const viewWords = () => {
    const parts = [];
    if (['jobs', 'library', 'archive'].includes(ui.filter)) parts.push(filterDefs.find(([id]) => id === ui.filter)[1]);
    if (ui.mode === 'list') parts.push('List');
    if (ui.sort !== 'newest') parts.push(`by ${ui.sort}`);
    return parts.length ? `View: ${parts.join(', ')}` : 'View';
  };
  const viewLabel = h('span', { text: viewWords() });
  const viewMenu = h('details', { class: 'view-menu' },
    h('summary', { class: 'btn', 'data-focus-key': 'films:view' }, viewLabel),
    h('div', { class: 'view-panel', role: 'group', 'aria-label': 'View options' },
      h('span', { class: 'field' }, h('span', { class: 'label', text: 'Show only' }), h('span', { class: 'seg', role: 'group', 'aria-label': 'Show only' }, filterButtons.slice(2))),
      h('span', { class: 'field' }, h('label', { for: 'films-sort', class: 'label', text: 'Sort' }), sort),
      h('span', { class: 'field' }, h('span', { class: 'label', text: 'Layout' }), h('span', { class: 'seg', role: 'group', 'aria-label': 'Layout' }, modeButtons)),
      statusBox));
  viewMenu.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && viewMenu.open) { event.stopPropagation(); viewMenu.open = false; viewMenu.querySelector('summary').focus(); }
  });
  fill(target,
    h('header', { class: 'page-head' },
      h('h1', { class: 'page-title', tabindex: '-1', text: 'Films' }),
      h('p', { class: 'deck', text: `${plural(counts.all, 'film')}: ${plural(counts.jobs, 'production job')} and ${plural(counts.library, 'film')} found among the videos on this Mac. Each row shows a film's versions, newest first.` })),
    h('div', { class: 'sheet-tools', role: 'group', 'aria-label': 'Filter, search and view the films' },
      h('span', { class: 'seg', role: 'group', 'aria-label': 'Show' }, filterButtons.slice(0, 2)),
      h('span', { class: 'field' }, h('label', { for: 'films-q', class: 'label', text: 'Search' }), search),
      viewMenu),
    results, otherBox);
  draw();
  state.view = {
    onScanDone: () => { if (state.route && state.route.name === 'films') { state.baseKey = null; render(); } },
  };
}

function collectionBlock(coll) {
  const body = h('div', { class: 'coll-body' }, h('p', { class: 'sub', text: 'Loading\u2026' }));
  let loaded = false;
  const details = h('details', { class: 'coll' },
    h('summary', {}, h('span', { class: 'coll-label', text: coll.label }), h('span', { class: 'meta', text: `${plural(coll.videos, 'video')} \u00b7 ${coll.why}` })),
    body);
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded) return;
    loaded = true;
    try {
      const full = await getJSON(`/api/library/collections/${enc(coll.id)}`);
      fill(body, h('ul', { class: 'lib-rows' }, full.items.map((item) => h('li', { class: 'lib-row' },
        h('div', { class: 'lib-row-main' },
          h('a', { href: libHref(item), title: item.path, text: item.name }), ' ', roleChip(item.role),
          item.duplicate_of ? h('p', { class: 'meta', text: `same file as ${item.duplicate_of.place}` }) : null,
          h('p', { class: 'path', text: item.folder })),
        h('div', { class: 'lib-row-meta' }, h('div', { text: fmtDuration(item.duration_s) }), h('div', { text: fmtBytes(item.size) }), timeEl(item.mtime))))));
    } catch (error) {
      loaded = false;
      fill(body, h('p', { class: 'empty', text: `Could not load this folder: ${error.message}` }));
    }
  });
  return details;
}

/* ------------------------------------------------------------------ the review room */

function roomFrames(room) {
  if (room.row) return room.row.versions;
  if (room.kind === 'job') {
    return (room.job.candidates || []).map((c) => ({
      label: c.version, mark: c.live ? 'in_progress' : 'not_reviewed', mark_word: c.live ? 'In progress' : 'Not reviewed',
      poster: null, duration_s: c.duration_s, mtime: c.mtime, has_master: c.has_master,
    }));
  }
  return room.lib.versions.map((v) => ({
    label: v.label, mark: 'file', role: v.primary.role, poster: v.primary.has_poster ? libMedia(v.primary.id, 'poster') : null,
    duration_s: v.primary.duration_s, mtime: v.mtime, item: v.primary.id,
  }));
}

function roomVersions(room) {
  return room.kind === 'job' ? [...room.job.versions].reverse() : room.lib.versions.map((v) => v.label);
}

function roomDecisions(room) {
  return room.row ? room.row.decisions : [];
}

/* Gates the owner has decided that still stand; they never count as decisions that wait. */
function roomDecided(room) {
  return room.row && Array.isArray(room.row.decided) ? room.row.decided : [];
}

function asDecision(room, item) {
  return item ? { ...item, decided: item.record, film_id: room.filmId, pid: room.key, film: room.name } : null;
}

function versionKey(label) {
  return (String(label || '').match(/\d+/g) || []).map(Number);
}

function compareVersions(a, b) {
  const x = versionKey(a);
  const y = versionKey(b);
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    const d = (x[i] ?? -1) - (y[i] ?? -1);
    if (d) return d;
  }
  return 0;
}

function openFindingsPhrase(levels) {
  const entries = LEVELS.map((level) => [level, (levels || {})[level] || 0]).filter(([, n]) => n);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  const name = (level) => (level === 'P?' ? 'unrated' : level);
  if (!total) return 'no open findings';
  if (entries.length === 1) return total === 1 ? `1 open finding at ${name(entries[0][0])}` : `${total} open findings, all ${name(entries[0][0])}`;
  return `${plural(total, 'open finding')}: ${entries.map(([level, n]) => `${n} at ${name(level)}`).join(', ')}`;
}

function sentenceCase(text) { return text ? `${text[0].toUpperCase()}${text.slice(1)}` : ''; }

function openFindingsWords(levels) { return `${sentenceCase(openFindingsPhrase(levels))}.`; }

/* Seconds as a brief writes them: "65 s". */
function fmtSeconds(value) { return `${fmtNum(Number(value), Number.isInteger(Number(value)) ? 0 : 1)} s`; }

/* The brief's runtime read as the server reads it (polarity.runtime_window): a cap is the upper bound; a target
   alone allows +/-10 %. */
function briefWindow(brief) {
  const cap = Number(brief && brief.runtime_max_s) > 0 ? Number(brief.runtime_max_s) : null;
  const target = Number(brief && brief.runtime_target_s) > 0 ? Number(brief.runtime_target_s) : null;
  if (cap) return { low: null, high: cap, cap, target };
  if (target) return { low: target * 0.9, high: target * 1.1, cap: null, target };
  return null;
}

function outsideWindow(duration, window) {
  const d = Number(duration);
  return Boolean(window && d > 0 && ((window.low !== null && d < window.low) || (window.high !== null && d > window.high)));
}

function windowWords(window) {
  return window.cap ? `caps the film at ${fmtSeconds(window.cap)}` : `targets ${fmtSeconds(window.target)}`;
}

/* "runs 01:33.1 against a 65 s cap": a version's runtime beside the brief, for one-line facts. */
function runtimePhrase(duration, brief) {
  if (!(Number(duration) > 0)) return null;
  const runs = `runs ${fmtDuration(duration)}`;
  const window = briefWindow(brief);
  if (!window) return runs;
  if (outsideWindow(duration, window)) return `${runs} against a ${fmtSeconds(window.cap || window.target)} ${window.cap ? 'cap' : 'target'}`;
  return window.cap ? `${runs}, within the ${fmtSeconds(window.cap)} cap` : `${runs}, near the ${fmtSeconds(window.target)} target`;
}

/* Measurable conflicts between the brief's runtime and the versions; nothing beyond the numbers is inferred. */
function runtimeConflicts({ brief, owner, version, duration, versionWindow, frames }) {
  const window = briefWindow(brief);
  if (!window) return [];
  const lines = [];
  const over = outsideWindow(duration, window);
  const scripted = versionWindow && /script target/.test(String(versionWindow.source || '')) && versionWindow.target
    && outsideWindow(versionWindow.target, window) ? versionWindow.target : null;
  if (over || scripted) {
    const facts = [over ? `${version} runs ${fmtDuration(duration)}` : null, scripted ? `${over ? 'its' : `${version}\u2019s`} script targets ${fmtSeconds(scripted)}` : null].filter(Boolean);
    lines.push(`${facts.join(' and ')}; ${owner} ${windowWords(window)}.`);
    const measured = (frames || []).filter((f) => f.has_master !== false && Number(f.duration_s) > 0);
    if (measured.length > 1) {
      const fits = measured.filter((f) => !outsideWindow(f.duration_s, window));
      const span = window.cap ? `within ${fmtSeconds(window.cap)}` : `within 10 % of ${fmtSeconds(window.target)}`;
      lines.push(fits.length
        ? `${fits.length} of ${measured.length} versions with a master ${fits.length === 1 ? 'runs' : 'run'} ${span}; the newest of them is ${fits[0].label} at ${fmtDuration(fits[0].duration_s)}.`
        : `None of the ${measured.length} versions with a master runs ${span}.`);
    }
  }
  return lines;
}

/* One sentence about the selected version only; the film's gate sentence lives in Decide and the Inbox. */
function versionSentence(room) {
  const v = room.version;
  if (!v) return room.kind === 'library' ? 'This film has no version with video yet.' : 'This job has no versions yet.';
  if (room.kind === 'library') {
    const version = room.lib.versions.find((x) => x.label === v);
    if (!version) return '';
    const primary = version.primary;
    const others = (version.variants || []).length;
    return `${v} is the ${ROLE_TEXT[primary.role] || primary.role || 'file'}${primary.duration_s ? `, ${fmtDuration(primary.duration_s)} long` : ''}${version.mtime ? `, from ${fmtDay(version.mtime)}` : ''}${others ? `, with ${plural(others, 'other file')}` : ''}.`;
  }
  const frame = room.frames.find((f) => f.label === v) || {};
  const cand = (room.job.candidates || []).find((c) => c.version === v) || {};
  const decisions = roomDecisions(room);
  const live = decisions.find((d) => d.version === v);
  const reviews = cand.reviews || [];
  const qa = cand.qa;
  const first = {
    needs: live && live.kind === 'release' ? `${v} waits for your release decision` : `${v} has findings that wait for your answer`,
    in_progress: `${v} is being built; its reviews are not complete`,
    rework: `${v}: ${plural(reviews.filter((r) => r.verdict_class === 'rework').length, 'reviewer')} ask for rework`,
    passed: `${v} passed ${plural(reviews.length, 'review')}${qa && qa.gates ? ` and ${plural(qa.gates, 'check')}` : ''}`,
    partial: `${v} is partly reviewed`,
    not_reviewed: `${v} is not reviewed yet`,
    not_run: `${v} has no master film`,
  }[frame.mark] || v;
  let text = `${first}.`;
  if (cand.open_levels && !['not_run', 'in_progress'].includes(frame.mark)) text += ` ${openFindingsWords(cand.open_levels)}`;
  const runtime = runtimeClause(room.job.brief_runtime, cand.duration_s || frame.duration_s, cand.script_target_s);
  if (runtime) text += ` ${runtime}`;
  if (cand.owner) text += ` ${ownerClause(cand.owner)}`;
  return text;
}

function primaryDecision(room, { live = false } = {}) {
  const pick = (list) => list.find((d) => d.version && d.version === room.version) || list.find((d) => !d.version) || list[0] || null;
  return pick(roomDecisions(room)) || (live ? null : asDecision(room, pick(roomDecided(room))));
}

/* Decide always opens a named decision: the one for this version, else the film's gate, else the first. */
function decideQuery(room) {
  const decision = primaryDecision(room);
  if (!decision) return { tab: 'decide' };
  return { tab: 'decide', v: decision.version || room.version, g: decision.kind === 'findings' ? null : decision.node };
}

function availableTabs(room) {
  if (room.kind === 'library') return ROOM_TABS.filter((tab) => ['watch', 'compare', 'files'].includes(tab.id));
  return ROOM_TABS.filter((tab) => tab.id !== 'decide' || roomDecisions(room).length || roomDecided(room).length);
}

async function loadRoom(route) {
  const room = { filmId: route.filmId, kind: route.kind === 'lib' ? 'library' : route.kind, key: route.key, row: null, job: null, lib: null };
  if (!route.kind) return { error: 'This address does not name a film.' };
  const films = await loadFilms(false);
  room.row = films.rows.find((row) => row.id === route.filmId) || null;
  try {
    if (room.kind === 'job') room.job = await getJSON(`/api/projects/${enc(route.key)}`);
    else room.lib = await getJSON(`/api/library/films/${enc(route.key)}`);
    if (room.lib && room.lib.job_pid) {
      const version = room.lib.versions.find((v) => v.label === route.version) || room.lib.versions[0];
      const fileId = route.query.get('i') || (version && version.primary ? version.primary.id : null);
      return { redirect: filmHref(`job-${room.lib.job_pid}`, { v: jobVersionOf(room.lib.job_pid, version && version.label), tab: 'files', file: fileId }) };
    }
  } catch (error) {
    if (error.status === 404) return { error: room.kind === 'job' ? 'No job with this id under the project folders.' : 'No film with this id in the library index; a rescan may have regrouped it.' };
    throw error;
  }
  room.name = room.kind === 'job' ? room.job.name : room.lib.name;
  return room;
}

function roomError(title, text, href, label) {
  return h('div', { class: 'room-error', role: 'alert' },
    h('h2', { text: title }), h('p', { class: 'sub', text }),
    href ? h('p', {}, h('a', { class: 'btn', href }, label)) : null);
}

async function viewRoom(target, route, seq) {
  const room = await loadRoom(route);
  if (seq !== renderSeq) return;
  if (room.redirect) {
    history.replaceState(null, '', room.redirect);
    state.baseKey = null;
    render();
    return;
  }
  if (room.error) { showMessage('Film not found', `${room.error} Pick a film from Films.`); return; }
  state.room = room;
  room.tabs = availableTabs(room);
  room.versions = roomVersions(room);
  room.frames = roomFrames(room);
  room.panel = h('div', { role: 'tabpanel', id: 'room-panel', tabindex: '-1' });
  room.railBox = h('div', { class: 'rail-box' });
  room.railView = prefs.railView === 'table' ? 'table' : 'frames';
  room.tablist = h('div', { class: 'room-tabs', role: 'tablist', 'aria-label': 'Review room' });
  room.headStatus = h('div', { class: 'room-status' });
  room.headActions = h('div', { class: 'room-actions' });
  room.select = (updates) => {
    const params = new URLSearchParams(state.route.query);
    for (const [key, value] of Object.entries(updates)) {
      if (value === null || value === undefined || value === '') params.delete(key); else params.set(key, String(value));
    }
    if (updates.tab && updates.tab !== 'pipeline') params.delete('n');
    if (updates.tab && updates.tab !== 'decide') params.delete('g');
    if (updates.tab && !['compare'].includes(updates.tab)) { params.delete('a'); params.delete('b'); }
    if ((updates.tab && updates.tab !== 'files') || updates.v !== undefined) params.delete('file');
    if (updates.v !== undefined) { params.delete('t'); params.delete('f'); params.delete('i'); }
    go(`#/f/${enc(room.filmId)}${params.toString() ? `?${params}` : ''}`);
  };
  room.titleEl = h('h1', { class: 'page-title room-title', tabindex: '-1', text: room.name });
  fill(target, h('header', { class: 'room-head' },
    room.titleEl, room.headStatus, room.railBox,
    h('div', { class: 'room-tabbar' }, room.tablist, room.headActions)), room.panel);
  window.requestAnimationFrame(() => fitTitle(room.titleEl));
  if (state.titleObserver) state.titleObserver.disconnect();
  if (window.ResizeObserver) {
    let width = 0;
    state.titleObserver = new ResizeObserver((entries) => {
      const next = Math.round(entries[0].contentRect.width);
      if (next !== width) { width = next; fitTitle(room.titleEl); }
    });
    state.titleObserver.observe(target);
  }
  state.view = {
    applyRoute: (r, s) => applyRoom(room, r, s),
    noteAtTime: () => room.noteAtTime?.(),
    refresh: async (pollSeq) => { if (room.refresh) await room.refresh(pollSeq); },
  };
  await applyRoom(room, route, seq);
}

/* The whole name, never clipped: one line when it fits, else two balanced lines, shrinking the type only if two lines
   are not enough. */
function fitTitle(el) {
  if (!el || !el.isConnected) return;
  el.style.fontSize = '';
  let size = parseFloat(getComputedStyle(el).fontSize) || 30;
  const lines = () => {
    const style = getComputedStyle(el);
    const line = parseFloat(style.lineHeight) || size * 1.08;
    return Math.round(el.getBoundingClientRect().height / line);
  };
  while (lines() > 2 && size > 18) {
    size -= 2;
    el.style.fontSize = `${size}px`;
  }
}

function decideShort(decision) {
  if (decision.kind === 'gate') return nodeTag(decision.node_num) || decision.words;
  if (decision.kind === 'release') return `${decision.version} release`;
  return decision.version ? `findings on ${decision.version}` : 'findings';
}

function drawRoomHead(room) {
  const forVersion = primaryDecision(room, { live: true });
  fill(room.headStatus, h('p', { class: 'sentence', text: versionSentence(room) }));
  fill(room.headActions,
    forVersion && room.tab !== 'decide'
      ? h('a', { class: 'btn decide', href: filmHref(room.filmId, { v: forVersion.version || room.version, tab: 'decide', g: forVersion.kind === 'findings' ? null : forVersion.node }),
        'data-focus-key': 'room:decide', 'aria-label': `Decide: ${forVersion.words}`, title: forVersion.words,
        on: { click: () => { room.focusAfter = 'tab'; } } },
        statusMark('needs', 'Decide'), h('span', { class: 'decide-what', text: decideShort(forVersion) }))
      : null,
    room.frames.length ? h('button', {
      type: 'button', class: 'btn small quiet rail-toggle', 'aria-pressed': String(room.railView === 'table'), 'data-focus-key': 'room:railview',
      'aria-label': 'Show the versions as a table',
      on: {
        click: (event) => {
          room.railView = room.railView === 'table' ? 'frames' : 'table';
          prefs.railView = room.railView;
          savePrefs();
          event.currentTarget.setAttribute('aria-pressed', String(room.railView === 'table'));
          drawRail(room);
        },
      },
    }, 'Table') : null);
  drawRail(room);
  const buttons = room.tabs.map((tab) => h('button', {
    type: 'button', role: 'tab', id: `room-tab-${tab.id}`, 'aria-controls': 'room-panel', 'aria-selected': String(tab.id === room.tab),
    tabindex: tab.id === room.tab ? '0' : '-1', 'data-focus-key': `room-tab:${tab.id}`,
    on: { click: () => { room.focusAfter = 'tab'; room.select(tab.id === 'decide' ? decideQuery(room) : { tab: tab.id }); } },
  }, tab.label, tab.id === 'decide' && roomDecisions(room).length ? h('span', { class: 'tab-dot', 'aria-hidden': 'true' }) : null));
  fill(room.tablist, ...buttons);
  room.tablist.onkeydown = (event) => {
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % buttons.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + buttons.length) % buttons.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = buttons.length - 1;
    if (next === null) return;
    event.preventDefault();
    buttons[next].focus();
    room.focusAfter = 'tab';
    room.select(room.tabs[next].id === 'decide' ? decideQuery(room) : { tab: room.tabs[next].id });
  };
  room.panel.setAttribute('aria-labelledby', `room-tab-${room.tab}`);
}

/* Keep the selected frame in view, as scrollIntoView({ inline: 'nearest' }) would, without moving the page. */
function revealSelected(strip, pad = 64) {
  const cell = strip.querySelector('[aria-current="true"]');
  if (!cell) return;
  const left = cell.offsetLeft;
  const right = left + cell.offsetWidth;
  if (left < strip.scrollLeft + pad) strip.scrollLeft = Math.max(0, left - pad);
  else if (right > strip.scrollLeft + strip.clientWidth - pad) strip.scrollLeft = right - strip.clientWidth + pad;
}

/* Count the frames scrolled out of view on each side, and fade the edges that hide them. */
function railCue(wrap, strip, newer, older) {
  const left = strip.scrollLeft;
  const right = left + strip.clientWidth;
  let before = 0;
  let after = 0;
  for (const cell of strip.children) {
    if (cell.offsetLeft + cell.offsetWidth <= left + 8) before += 1;
    else if (cell.offsetLeft >= right - 8) after += 1;
  }
  newer.hidden = !before;
  older.hidden = !after;
  newer.textContent = `\u2190 ${before} newer`;
  older.textContent = `${after} older \u2192`;
  wrap.classList.toggle('overflow', strip.scrollWidth > strip.clientWidth + 2);
  wrap.dataset.fade = before && after ? 'both' : before ? 'left' : after ? 'right' : 'none';
}

function drawRail(room) {
  const previous = room.railBox.querySelector('.rail');
  const keep = previous ? previous.scrollLeft : null;
  if (room.railView === 'table') { fill(room.railBox, versionsTable(room)); return; }
  if (!room.frames.length) { fill(room.railBox, h('p', { class: 'empty', text: 'This film has no versions yet.' })); return; }
  const strip = frameStrip({ id: room.filmId, name: room.name }, room.frames, {
    selected: room.tab === 'compare' && room.versions.length > 1 ? comparePair(room).b : room.version, className: 'rail', label: 'Versions, newest first', autoScroll: false, preferStill: true, ownerStyle: 'overlay',
    hrefOf: (frame) => {
      if (room.tab !== 'compare') return filmHref(room.filmId, { v: frame.label, tab: room.tab === 'decide' ? 'watch' : room.tab });
      /* On Compare a frame sets B and keeps A; the pair is never swapped behind the owner's back. */
      const pair = comparePair(room);
      const b = frame.label === pair.a ? pair.b : frame.label;
      return filmHref(room.filmId, { v: b, tab: 'compare', a: pair.a, b });
    },
  });
  strip.addEventListener('click', () => { room.focusAfter = 'tab'; });
  const page = (direction) => strip.scrollBy({ left: direction * strip.clientWidth * 0.8, behavior: 'smooth' });
  const newer = h('button', { type: 'button', class: 'rail-cue newer', tabindex: '-1', 'aria-hidden': 'true', hidden: true, on: { click: () => page(-1) } });
  const older = h('button', { type: 'button', class: 'rail-cue older', tabindex: '-1', 'aria-hidden': 'true', hidden: true, on: { click: () => page(1) } });
  const wrap = h('div', { class: 'rail-wrap', 'data-fade': 'none' }, strip, h('div', { class: 'rail-pager' }, newer, older));
  fill(room.railBox, wrap);
  const update = () => railCue(wrap, strip, newer, older);
  let queued = false;
  strip.addEventListener('scroll', () => {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(() => { queued = false; update(); });
  }, { passive: true });
  window.requestAnimationFrame(() => {
    if (keep !== null) strip.scrollLeft = keep;
    revealSelected(strip);
    update();
  });
  if (window.ResizeObserver) new ResizeObserver(() => update()).observe(strip);
}

/* The versions as a table: status, open findings by level, checks, notes and source; tick A and B to compare. */
function versionsTable(room) {
  const pick = room.pick || (room.pick = { a: null, b: null });
  const job = room.kind === 'job';
  const frames = new Map(room.frames.map((frame) => [frame.label, frame]));
  const rows = job ? (room.job.candidates || []) : room.lib.versions;
  const compare = h('a', { class: 'btn small', 'data-focus-key': 'vt:compare' }, 'Compare A and B');
  const picked = h('span', { class: 'meta' });
  const boxes = [];
  const sync = () => {
    const ready = pick.a && pick.b && pick.a !== pick.b;
    if (ready) { compare.href = filmHref(room.filmId, { tab: 'compare', a: pick.a, b: pick.b }); compare.removeAttribute('aria-disabled'); }
    else { compare.removeAttribute('href'); compare.setAttribute('aria-disabled', 'true'); }
    picked.textContent = `A: ${pick.a || 'none'}, B: ${pick.b || 'none'}`;
    for (const box of boxes) box.checked = pick[box.dataset.slot] === box.dataset.version;
  };
  const box = (slot, version) => {
    const input = h('input', {
      type: 'checkbox', 'aria-label': `Compare ${version} as ${slot.toUpperCase()}`, 'data-focus-key': `vt:${slot}:${version}`,
      on: { change: (event) => { pick[slot] = event.target.checked ? version : (pick[slot] === version ? null : pick[slot]); sync(); } },
    });
    input.dataset.slot = slot;
    input.dataset.version = version;
    boxes.push(input);
    return input;
  };
  const versionLink = (label) => h('a', {
    href: filmHref(room.filmId, { v: label, tab: room.tab === 'decide' ? 'watch' : room.tab }), 'aria-current': label === room.version ? 'true' : null,
    on: { click: () => { room.focusAfter = 'tab'; } },
  }, label);
  const levelsCell = (levels) => {
    const entries = LEVELS.map((level) => [level, (levels || {})[level] || 0]).filter(([, n]) => n);
    return entries.length ? h('span', { class: 'level-counts' }, entries.map(([level, n]) => h('span', {}, levelChip(level), h('span', { class: 'count', text: ` ${n}` }))))
      : h('span', { class: 'meta', text: levels ? 'none' : 'not counted' });
  };
  const sourceWords = { candidate: 'Candidate folder', governed: 'Engine run', both: 'Both' };
  const head = job
    ? ['A', 'B', 'Version', 'Date', 'Duration', 'Status', 'Open findings', 'Automatic checks', 'Notes', 'Source']
    : ['A', 'B', 'Version', 'Date', 'Duration', 'Role', 'Files'];
  const body = rows.map((row) => {
    if (job) {
      const frame = frames.get(row.version) || {};
      const qa = row.qa;
      return h('tr', { class: row.version === room.version ? 'selected' : null },
        h('td', {}, box('a', row.version)), h('td', {}, box('b', row.version)),
        h('th', { scope: 'row', class: 'id' }, versionLink(row.version)),
        h('td', { class: 'num' }, row.mtime ? fmtDay(row.mtime) : ''),
        h('td', { class: 'num' }, row.duration_s ? fmtDuration(row.duration_s) : ''),
        h('td', {}, frame.mark ? statusMark(frame.mark, frame.mark_word) : '', row.owner ? h('div', { class: 'owner-line' }, ownerMark(row.owner)) : null),
        h('td', {}, levelsCell(row.open_levels)),
        h('td', {}, !qa ? h('span', { class: 'meta', text: 'none recorded' })
          : qa.findings ? h('span', { text: plural(qa.findings, 'finding') }) : statusMark('passed', 'Pass')),
        h('td', { class: 'num' }, String(row.notes_count ?? row.reactions_count ?? 0)),
        h('td', {}, sourceWords[row.source] || row.source || ''));
    }
    const primary = row.primary;
    return h('tr', { class: row.label === room.version ? 'selected' : null },
      h('td', {}, box('a', row.label)), h('td', {}, box('b', row.label)),
      h('th', { scope: 'row', class: 'id' }, versionLink(row.label)),
      h('td', { class: 'num' }, row.mtime ? fmtDay(row.mtime) : ''),
      h('td', { class: 'num' }, primary.duration_s ? fmtDuration(primary.duration_s) : ''),
      h('td', {}, roleChip(primary.role)),
      h('td', { class: 'num' }, String(1 + (row.variants || []).length)));
  });
  sync();
  return h('div', { class: 'vtable-box' },
    h('div', { class: 'vtable scroll-x', role: 'region', 'aria-label': 'Versions table', tabindex: '0' },
      h('table', {}, h('caption', { class: 'visually-hidden', text: 'Versions, newest first' }), tableHead(head), h('tbody', {}, body))),
    h('div', { class: 'btn-row' }, compare, picked));
}

/* After a tab switch, a rail change or a shortcut, focus the selected tab; otherwise put focus back where it was. */
function restoreRoomFocus(room, focusKey) {
  const wantTab = room.focusAfter === 'tab';
  room.focusAfter = null;
  if (state.drawer || document.querySelector('dialog[open]')) return;
  const active = document.activeElement;
  const lost = !active || active === document.body || !active.isConnected;
  if (!wantTab && !lost) return;
  const again = !wantTab && focusKey ? document.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`) : null;
  const target = again || room.tablist.querySelector('[aria-selected="true"]');
  if (target) target.focus({ preventScroll: true });
}

async function applyRoom(room, route, seq) {
  const initial = !room.drawn;
  const focused = document.activeElement;
  const focusKey = focused && focused.dataset ? focused.dataset.focusKey || null : null;
  const wanted = route.query.get('v');
  const remembered = prefs.versions[room.filmId];
  let version = wanted;
  if (!version) {
    if (remembered && room.versions.includes(remembered)) version = remembered;
    else if (room.kind === 'job') version = room.job.focus_version || room.job.latest_version;
    else version = room.versions[0] || null;
  }
  const tab = route.query.get('tab') || 'watch';
  /* A Decide address always gets the Decide tab, so the tab label and the panel agree (decided, or nothing waits). */
  if (tab === 'decide' && room.kind === 'job' && !room.tabs.some((t) => t.id === 'decide')) {
    room.tabs = ROOM_TABS.filter((t) => t.id === 'decide' || room.tabs.some((x) => x.id === t.id));
  }
  const tabKnown = room.tabs.some((t) => t.id === tab);
  const versionKnown = !version || room.versions.includes(version);
  const changed = version !== room.version || tab !== room.tab || route.query.get('a') !== room.a || route.query.get('b') !== room.b
    || route.query.get('g') !== room.g || route.query.get('i') !== room.i || route.query.get('file') !== room.file || !room.drawn;
  room.version = version;
  room.tab = tabKnown ? tab : 'watch';
  room.a = route.query.get('a');
  room.b = route.query.get('b');
  room.g = route.query.get('g');
  room.i = route.query.get('i');
  room.file = route.query.get('file');
  if (versionKnown && version) { prefs.versions[room.filmId] = version; savePrefs(); }
  if (changed) {
    drawRoomHead(room);
    room.drawn = true;
    room.refresh = null;
    room.noteAtTime = null;
    state.video = null;
    if (!versionKnown) {
      fill(room.panel, roomError(`Version ${version} is not part of this film`,
        `${room.name} has ${plural(room.versions.length, 'version')}; ${version} is not one of them. Nothing is shown in its place.`,
        filmHref(room.filmId), `Open the latest version, ${room.versions[0] || 'none'}`));
    } else if (!tabKnown) {
      fill(room.panel, roomError(tab === 'decide' ? 'Nothing to decide here' : `No ${tab} view for this film`,
        tab === 'decide' ? 'No gate or release waits for you on this film right now. The Inbox lists every decision that waits.' : 'This film has Watch, Compare and Files.',
        filmHref(room.filmId, { v: version }), 'Watch this film'));
    } else {
      fill(room.panel, h('p', { class: 'loading', text: 'Loading\u2026' }));
      const renderers = { watch: renderWatch, decide: renderDecide, compare: renderCompare, pipeline: renderPipeline, files: renderFiles };
      await renderers[room.tab](room, seq, route);
      if (seq !== renderSeq) return;
    }
  }
  const node = route.query.get('n');
  if (room.kind === 'job' && node && room.tab === 'pipeline') await openDrawer(room.key, node, room.version, seq);
  else if (state.drawer) closeDrawer();
  if (room.applyQuery) room.applyQuery(route);
  if (!initial && seq === renderSeq) restoreRoomFocus(room, focusKey);
}

/* ------------------------------------------------------------------ single video page (outside a film) */

async function viewVideo(target, route, seq) {
  let item;
  try {
    item = await getJSON(`/api/library/items/${enc(route.itemId)}`);
  } catch (error) {
    if (error.status !== 404) throw error;
    showMessage('Video not found', 'No video with this id in the library index; a rescan may have changed it.');
    return;
  }
  if (seq !== renderSeq) return;
  if (item.job_pid || (item.film_summary && item.film_summary.job_pid)) {
    await loadFilms(false);
    if (seq !== renderSeq) return;
    history.replaceState(null, '', libHref(item));
    state.baseKey = null;
    render();
    return;
  }
  if (item.film_summary) {
    history.replaceState(null, '', filmHref(`lib-${item.film_summary.id}`, { v: item.version, i: item.id, t: route.query.get('t') }));
    state.baseKey = null;
    render();
    return;
  }
  const records = await getJSON('/api/reactions?pid=library').catch(() => []);
  if (seq !== renderSeq) return;
  const video = libraryPlayer(item, `Video ${item.name}`, 'none');
  state.video = video instanceof HTMLVideoElement ? video : null;
  const notes = libraryNotes(item, null, records, () => (state.video ? state.video.currentTime : 0));
  fill(target,
    h('header', { class: 'room-head' },
      h('h1', { class: 'page-title break', tabindex: '-1', text: item.name }),
      h('p', { class: 'sentence sub', text: item.collection ? `In Other videos: ${item.collection.label}. ${item.collection.why}.` : 'A video outside any film.' })),
    h('div', { class: 'watch' },
      h('section', { 'aria-label': 'Player' }, h('div', { class: 'player' }, video), itemFacts(item)),
      h('section', { class: 'side', 'aria-labelledby': 'same-h' }, h('h2', { id: 'same-h', text: 'Same file elsewhere' }),
        item.same_file && item.same_file.length
          ? h('ul', { class: 'lib-rows' }, item.same_file.map((other) => h('li', { class: 'lib-row' },
            h('div', { class: 'lib-row-main' }, h('a', { href: libHref(other), text: other.name }),
              h('p', { class: 'path', text: other.folder })))))
          : h('p', { class: 'empty', text: 'No other copy of this file was found.' }))),
    notes.el);
  const t = route.query.get('t');
  if (state.video && t !== null && t !== '' && Number.isFinite(Number(t))) seekTo(Number(t), { quiet: true });
  state.view = { noteAtTime: () => { notes.form.selectOption('note_time'); notes.form.focusText(); } };
}

function libraryNotes(item, versionLabel, records, getTime) {
  const mine = records.filter((r) => (r.target && r.target.id === item.id) || r.a === item.id || r.b === item.id);
  const list = liveReactions(mine, 'No notes on this video yet. Press N while it plays to add one.');
  const context = { id: item.id, film_id: item.film_id, film: item.film, version: versionLabel || item.version, name: item.name };
  const form = composer({
    pid: 'library', set: 'library', label: `${versionLabel ? `${versionLabel}: ` : ''}${item.name}`,
    target: { type: 'library', id: item.id }, timedTarget: (t) => ({ type: 'library', id: item.id, t }), getTime,
    onPending: (pending) => list.pending({ ...pending, library: context }),
    onSaved: (record, pending) => list.saved(record, pending),
    onFailed: (pending) => list.failed(pending),
    onUndone: (record) => list.remove(record.id),
  });
  const titleId = nextId('notes-h');
  return { form, el: h('section', { class: 'card', 'aria-labelledby': titleId }, h('h2', { id: titleId, text: 'Notes on this video' }), form, list.el) };
}

/* ------------------------------------------------------------------ Watch */

const WATCH_LANES = [
  { id: 'defects', label: 'Findings' },
  { id: 'notes', label: 'My notes' },
  { id: 'keyframes', label: 'Key frames', more: true },
  { id: 'captions', label: 'Captions', more: true },
];

/* The owner's notes, flags and decisions on a version: what My notes and the Table's Notes count. */
function ownerNotes(data) {
  return (data.reactions || []).filter((r) => (!r.actor || r.actor === 'owner') && !r.retracted && ['note', 'flag', 'approve', 'request_changes'].includes(r.kind));
}

function laneItems(view) {
  const data = view.data;
  return {
    defects: data.findings.filter((f) => f.t0 !== null && f.t0 !== undefined).map((f) => ({
      t: f.t0, cls: `lvl-${lvlClass(f.level)}`, uid: f.uid, label: `${f.level} ${oneLine(findingTitle(f), 100)}`,
    })),
    keyframes: data.keyframes.filter((k) => k.t !== null && k.t !== undefined).map((k) => ({ t: k.t, cls: 'kf', label: `Key frame: ${k.label}` })),
    captions: data.captions.map((c) => ({ t: c.start, end: c.end, cls: 'cue', label: `Caption: ${oneLine(c.text, 100)}` })),
    notes: ownerNotes(data).filter((r) => r.t !== null && r.t !== undefined).map((r) => ({
      t: r.t, cls: 'note', label: `${reactionKindText(r)}: ${oneLine(r.text || targetText(r), 100)}`,
    })),
  };
}

function drawTimeline(view) {
  const duration = timelineDuration(view);
  view.duration = duration;
  const items = laneItems(view);
  const lanes = WATCH_LANES.filter((lane) => !lane.more || view.lanesOpen);
  const all = lanes.flatMap((lane) => items[lane.id]);
  const body = h('div', { class: 'tl-body', style: { position: 'relative' } });
  for (const lane of lanes) {
    const track = h('div', { class: 'tl-track' }, items[lane.id].map((item) => {
      const mark = h('span', { class: `tl-mark ${item.cls || ''}` });
      mark.style.left = `${Math.min(100, Math.max(0, (item.t / duration) * 100))}%`;
      if (item.end !== undefined && item.end !== null) mark.style.width = `${Math.max(0.3, ((item.end - item.t) / duration) * 100)}%`;
      return mark;
    }), h('span', { class: 'tl-playhead' }));
    const n = items[lane.id].length;
    const total = lane.id === 'defects' ? view.data.findings.length : lane.id === 'notes' ? ownerNotes(view.data).length : n;
    body.append(h('div', { class: 'tl-lane' }, h('span', { class: 'label', text: total !== n ? `${lane.label}: ${n} timed of ${total}` : `${lane.label}: ${n}` }), track));
  }
  const slider = h('div', {
    class: 'tl-seek', role: 'slider', tabindex: '0', 'aria-label': 'Timeline: seek the film; findings and notes are marked',
    'aria-valuemin': '0', 'aria-valuemax': String(Math.round(duration)), 'data-focus-key': 'timeline:seek',
  });
  const firstTrack = () => body.querySelector('.tl-track');
  const place = () => {
    const track = firstTrack();
    if (!track) return;
    slider.style.left = `${track.offsetLeft}px`;
    slider.style.width = `${track.offsetWidth}px`;
  };
  const timeAt = (clientX) => {
    const box = slider.getBoundingClientRect();
    const raw = Math.min(1, Math.max(0, (clientX - box.left) / box.width)) * duration;
    const near = all.reduce((best, item) => (Math.abs(item.t - raw) < Math.abs((best ? best.t : 1e9) - raw) ? item : best), null);
    const snap = near && Math.abs(near.t - raw) / duration * box.width < 6 ? near : null;
    return { t: snap ? snap.t : raw, item: snap };
  };
  slider.addEventListener('click', (event) => {
    const { t, item } = timeAt(event.clientX);
    seekTo(t, { quiet: true });
    if (item && item.uid) highlightFinding(view, item.uid, false);
  });
  slider.addEventListener('mousemove', (event) => {
    const { t, item } = timeAt(event.clientX);
    slider.title = item ? `${fmtT(item.t)} ${item.label}` : fmtT(t);
  });
  slider.addEventListener('keydown', (event) => {
    const video = state.video;
    const now = video ? video.currentTime : 0;
    const step = event.shiftKey ? 5 : 1;
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next = now + step;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next = now - step;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = duration;
    else if (event.key === 'PageDown' || event.key === 'PageUp') {
      const marks = items.defects.map((item) => item.t).sort((a, b) => a - b);
      next = event.key === 'PageDown' ? marks.find((t) => t > now + 0.05) : [...marks].reverse().find((t) => t < now - 0.05);
    }
    if (next === null || next === undefined) return;
    event.preventDefault();
    seekTo(Math.max(0, Math.min(duration, next)), { quiet: true });
  });
  body.append(slider);
  const scale = h('div', { class: 'tl-scale', 'aria-hidden': 'true' }, h('span'),
    h('div', {}, h('span', { text: fmtT(0) }), h('span', { text: fmtT(duration / 2) }), h('span', { text: fmtT(duration) })));
  fill(view.timelineBox, body, scale);
  view.slider = slider;
  window.requestAnimationFrame(place);
  if (!view.placeObserver && window.ResizeObserver) {
    view.placeObserver = new ResizeObserver(() => place());
    view.placeObserver.observe(view.timelineBox);
  }
  view.place = place;
  updatePlayhead(view);
}

function updatePlayhead(view) {
  const duration = view.duration || timelineDuration(view);
  const current = view.video ? view.video.currentTime : 0;
  const left = `${Math.min(100, Math.max(0, (current / duration) * 100))}%`;
  for (const playhead of view.timelineBox.querySelectorAll('.tl-playhead')) playhead.style.left = left;
  if (view.slider) {
    view.slider.setAttribute('aria-valuenow', String(Math.round(current * 10) / 10));
    view.slider.setAttribute('aria-valuetext', `${fmtT(current)} of ${fmtT(duration)}`);
  }
  if (view.noteButton) view.noteButton.lastChild.textContent = `Note at ${fmtT(current)}`;
}

function filmstrip(view) {
  const frames = view.data.keyframes;
  if (!frames.length) return h('p', { class: 'empty', text: 'No key frames for this version.' });
  const buttons = frames.map((frame, index) => h('button', {
    type: 'button', tabindex: index === 0 ? '0' : '-1', disabled: frame.t === null || frame.t === undefined,
    'aria-label': `Play from ${fmtT(frame.t)}: ${frame.label}`, on: { click: () => seekTo(frame.t) },
  }, h('img', { src: frame.url, alt: '', loading: 'lazy', decoding: 'async' }), h('span', { class: 'meta', text: fmtT(frame.t) }),
  h('span', { class: 'kf-label', text: frame.label })));
  const list = h('ol', { class: 'filmstrip', 'aria-label': `Key frames, ${frames.length}; arrow keys move between them` }, buttons.map((button) => h('li', {}, button)));
  list.addEventListener('keydown', (event) => rovingKeys(event, buttons));
  return list;
}

const VERDICT_WORDS = { pass: 'Passed', rework: 'Rework', none: 'Not reviewed', unknown: 'Verdict unclear' };

/* "Passed. 3 findings at P4." from the reviewer's verdict and its open findings by level. */
function reviewerSentence(item, review) {
  const word = VERDICT_WORDS[item.verdict_class] || VERDICT_WORDS.unknown;
  let findings;
  if (Array.isArray(review.defects)) {
    const open = review.defects.filter(isOpenFinding);
    const counts = LEVELS.map((level) => [level, open.filter((d) => d.level === level).length]).filter(([, n]) => n);
    if (!counts.length) findings = 'No open findings';
    else if (counts.length === 1) findings = `${plural(counts[0][1], 'finding')} at ${counts[0][0] === 'P?' ? 'no level' : counts[0][0]}`;
    else findings = `${plural(open.length, 'finding')}: ${counts.map(([level, n]) => `${n} at ${level === 'P?' ? 'no level' : level}`).join(', ')}`;
  } else {
    findings = item.defect_count ? `${plural(item.defect_count, 'finding')}${item.worst_level ? `, worst ${item.worst_level}` : ''}` : 'No findings';
  }
  return { word, rest: `${findings}.` };
}

/* "6 of 6 reviewers pass · 31 automatic checks pass · 13 open findings, all P4" */
function verdictLine(data, brief = null) {
  const reviewers = (data.verdict_strip || []).filter((item) => item.type === 'reviewer');
  const passing = reviewers.filter((item) => item.verdict_class === 'pass').length;
  const checks = (data.verdict_strip || []).find((item) => item.type === 'gates');
  const levels = {};
  for (const finding of (data.findings || []).filter(isOpenFinding)) levels[finding.level] = (levels[finding.level] || 0) + 1;
  const parts = [
    reviewers.length ? `${passing} of ${plural(reviewers.length, 'reviewer')} ${passing === 1 ? 'passes' : 'pass'}` : 'No reviews yet',
    checks && checks.total ? (checks.failing ? `${checks.failing} of ${plural(checks.total, 'check')} fail` : `${plural(checks.total, 'check')} ${checks.total === 1 ? 'passes' : 'pass'}`) : 'no checks',
    openFindingsPhrase(levels),
    runtimeClause(brief, data.duration_s, scriptTargetOf(data.runtime_window), { sentence: false }),
  ].filter(Boolean);
  const rework = reviewers.some((item) => item.verdict_class === 'rework') || Boolean(checks && checks.failing);
  return { text: parts.join(' \u00b7 '), mark: rework ? 'rework' : reviewers.length && passing === reviewers.length ? 'passed' : 'not_reviewed' };
}

function verdictLines(data, ctx) {
  const level = ctx.headingLevel === 3 ? 'h3' : 'h2';
  const reviews = Object.fromEntries((data.reviews || []).map((review) => [review.key, review]));
  const line = (name, cls, word, rest, details) => {
    const meta = VERDICT[cls] || VERDICT.unknown;
    return h('li', {},
      h('span', { class: 'v-name', text: name }),
      h('p', { class: `v-sentence v-${meta.mark}` }, h('span', { class: 'v-glyph', 'aria-hidden': 'true' }, markGlyph(meta.mark)),
        h('span', { class: 'v-word', text: `${word}.` }), ` ${rest}`),
      details ? h('details', { class: 'v-more' }, h('summary', { text: 'Details' }), h('p', { class: 'sub', text: details })) : null);
  };
  const lines = data.verdict_strip.map((item) => {
    if (item.type === 'reviewer') {
      const review = reviews[item.key] || {};
      const sentence = reviewerSentence(item, review);
      return line(item.label, item.verdict_class, sentence.word, sentence.rest, review.summary ? oneLine(review.summary, 900) : null);
    }
    if (item.type === 'gates') {
      const cls = item.verdict_class in VERDICT ? item.verdict_class : 'unknown';
      const rest = item.total ? (item.failing ? `${item.failing} of ${item.total} automatic checks fail.` : `All ${item.total} automatic checks pass.`) : 'No automatic checks recorded.';
      return line('Automatic checks', cls, item.total ? (item.failing ? 'Rework' : 'Passed') : 'None', rest, null);
    }
    return null;
  }).filter(Boolean);
  const list = lines.length ? h('ul', { class: 'verdicts' }, lines) : h('p', { class: 'empty', text: 'No reviews or checks for this version yet.' });
  const full = ctx.full && (data.reviews || []).length
    ? h('details', {}, h('summary', { text: 'Scores and full reviews' }), versionReviewsTab(data)) : null;
  if (ctx.collapsed && lines.length) {
    const line = verdictLine(data, ctx.brief);
    const body = h('div', { class: 'verdict-body' });
    /* The reviewer lines are built when the line opens, so nothing sits laid out inside the closed disclosure. */
    const open = () => { if (!body.childElementCount) appendKids(body, list, full); };
    const box = h('details', { class: 'verdict-summary', open: Boolean(ctx.open) },
      h('summary', { 'data-focus-key': 'verdicts:summary' }, h('span', { class: `v-glyph v-${line.mark}`, 'aria-hidden': 'true' }, markGlyph(line.mark)), h('span', { class: 'v-line', text: line.text })),
      body);
    if (ctx.open) open();
    box.firstChild.addEventListener('click', open);
    box.addEventListener('toggle', () => { if (box.open) open(); if (ctx.onToggle) ctx.onToggle(box.open); });
    return h('section', { class: 'verdict-block', 'aria-labelledby': 'verdicts-h' }, h(level, { id: 'verdicts-h', class: 'visually-hidden', text: 'Verdicts' }), box);
  }
  return h('section', { 'aria-labelledby': 'verdicts-h' },
    h('div', { class: 'section-head' }, h(level, { id: 'verdicts-h', text: 'Verdicts' })), list, full);
}

function findingsBlock(view) {
  const data = view.data;
  const reacted = new Set(data.reactions.filter((r) => r.target && r.target.type === 'finding').map((r) => r.target.id));
  const box = h('section', { 'aria-labelledby': 'findings-h' });
  const filterOf = () => view.findingFilter || 'all';
  const matches = (f) => {
    const value = filterOf();
    if (value === 'all') return true;
    const [kind, arg] = value.split(':');
    if (kind === 'level') return f.level === arg;
    if (kind === 'node') return f.node === arg;
    if (kind === 'source') return findingSourceKey(f) === arg;
    return true;
  };
  const answers = findingAnswers(data.reactions);
  const p4 = data.findings.filter((f) => f.level === 'P4' && isOpenFinding(f) && !reacted.has(f.uid));
  const status = h('div');
  const draw = () => {
    if (!view.findingsExpanded) view.findingFilter = 'all';
    const shown = data.findings.filter(matches);
    const limit = view.findingsExpanded ? shown.length : FINDINGS_FIRST;
    const levels = LEVELS.filter((level) => data.findings.some((f) => f.level === level));
    const nodes = [...new Set(data.findings.map((f) => f.node).filter(Boolean))];
    const sources = ['reviews', 'checks', 'engine'].filter((s) => data.findings.some((f) => findingSourceKey(f) === s));
    const count = (pred) => data.findings.filter(pred).length;
    const select = h('select', {
      id: 'findings-filter', 'data-focus-key': 'findings:filter', 'aria-label': 'Show findings',
      on: { change: (event) => { view.findingFilter = event.target.value; view.findingsExpanded = false; preserveFocus(draw); } },
    }, h('option', { value: 'all', text: `All findings (${data.findings.length})`, selected: filterOf() === 'all' }),
    levels.length ? h('optgroup', { label: 'Level' }, levels.map((level) => h('option', { value: `level:${level}`, selected: filterOf() === `level:${level}`, text: `${level === 'P?' ? 'Unrated' : level} (${count((f) => f.level === level)})` }))) : null,
    nodes.length ? h('optgroup', { label: 'Node' }, nodes.map((node) => h('option', { value: `node:${node}`, selected: filterOf() === `node:${node}`, text: `${nodeWords(node)} (${count((f) => f.node === node)})` }))) : null,
    sources.length > 1 ? h('optgroup', { label: 'Source' }, sources.map((source) => h('option', { value: `source:${source}`, selected: filterOf() === `source:${source}`, text: `${{ reviews: 'Reviews', checks: 'Automatic checks', engine: 'Engine findings' }[source]} (${count((f) => findingSourceKey(f) === source)})` }))) : null);
    const bulk = p4.length > 1 ? h('button', {
      type: 'button', class: 'btn small', 'data-focus-key': 'findings:bulk-p4',
      on: { click: () => agreeAllP4(view, p4, status) },
    }, icon('check'), `Agree with all P4 (${p4.length})`) : null;
    const toggle = (expanded) => { view.findingsExpanded = expanded; preserveFocus(draw); };
    fill(box,
      h('div', { class: 'section-head' }, h('h2', { id: 'findings-h', text: `Findings (${data.findings.length})` }),
        h('span', { class: 'meta', text: shown.length > limit ? `the first ${limit}, by severity then time` : 'by severity then time' })),
      view.findingsExpanded && data.findings.length > FINDINGS_FIRST ? h('div', { class: 'findings-tools' }, select, bulk, status) : status,
      shown.length
        ? findingsList(shown.slice(0, limit), { pid: data.pid, version: data.version, reacted, answers, onReacted: () => refreshSoon() })
        : h('p', { class: 'empty', text: data.findings.length ? 'No finding matches this filter.' : 'No findings for this version.' }),
      shown.length > limit ? h('div', { class: 'findings-more' }, h('button', {
        type: 'button', class: 'btn small quiet', 'data-focus-key': 'findings:more', on: { click: () => toggle(true) },
      }, `Show all ${shown.length}`)) : null,
      view.findingsExpanded && data.findings.length > FINDINGS_FIRST ? h('div', { class: 'findings-more' }, h('button', {
        type: 'button', class: 'btn small quiet', 'data-focus-key': 'findings:fewer', on: { click: () => toggle(false) },
      }, `Show the first ${FINDINGS_FIRST}`)) : null);
  };
  view.redrawFindings = draw;
  draw();
  return box;
}

async function agreeAllP4(view, findings, status) {
  const data = view.data;
  const ok = await confirmDialog(`Agree with all ${findings.length} P4 findings?`,
    `Records an agree on each P4 finding of ${data.version} for the producer. You can undo it for ten seconds afterwards.`,
    `Agree with ${findings.length}`);
  if (!ok) return;
  const reactions = findings.slice(0, 100).map((f) => ({
    pid: data.pid, kind: 'agree', tags: [], text: '',
    target: cleanTarget({ type: 'finding', id: f.uid, version: data.version, t: f.t0 ?? null }),
  }));
  try {
    const reply = await postJSON('/api/reactions/batch', { reactions });
    fill(status, undoLine(reply.records, { word: `Agreed with ${reply.records.length} P4 findings` }));
    announce(`Agreed with ${reply.records.length} P4 findings. Undo is available for ten seconds.`);
    refreshSoon();
  } catch (error) {
    fill(status, h('span', { class: 'composer-status error', text: `Not saved: ${error.message}` }));
  }
}

function highlightFinding(view, uid, focus) {
  if (!view.redrawFindings) return;
  let row = view.side.querySelector(`.finding[data-uid="${CSS.escape(uid)}"]`);
  if (!row) {
    view.findingFilter = 'all';
    view.findingsExpanded = true;
    view.redrawFindings();
    row = view.side.querySelector(`.finding[data-uid="${CSS.escape(uid)}"]`);
  }
  if (!row) return;
  const select = row.querySelector('.f-select');
  if (select && select.getAttribute('aria-expanded') !== 'true') select.click();
  row.classList.add('flash');
  window.setTimeout(() => row.classList.remove('flash'), 2400);
  row.scrollIntoView({ block: 'nearest' });
  if (focus) (select || row).focus({ preventScroll: true });
}

/* Scroll so the note composer is in view while the player stays visible under the masthead when both fit. */
function keepNoteInView(view) {
  const form = view.noteForm;
  const player = view.playerBox;
  if (!form || !player) return;
  const top = ($('.masthead') || document.body).getBoundingClientRect().bottom + 8;
  const bottom = window.innerHeight - 8;
  const f = form.getBoundingClientRect();
  const text = form.querySelector('textarea').getBoundingClientRect();
  const p = player.getBoundingClientRect();
  let delta = 0;
  if (f.bottom > bottom) {
    delta = f.bottom - bottom;
    const keepPlayer = p.top - top;
    if (delta > keepPlayer) delta = Math.max(keepPlayer, text.bottom - bottom);
  } else if (p.top < top) {
    delta = -Math.min(top - p.top, bottom - f.bottom);
  } else if (f.top < top) {
    delta = f.top - top;
  }
  if (delta) window.scrollBy({ top: delta });
}

function closeNoteComposer(view) {
  if (view.noteForm) view.noteForm.remove();
  if (view.noteButton && view.noteButton.isConnected) view.noteButton.focus();
  announce('Note closed; the draft is kept.');
}

function openNoteComposer(view) {
  if (view.video && !view.video.paused) view.video.pause();
  const data = view.data;
  if (!view.noteForm) {
    view.noteForm = composer({
      pid: data.pid, version: data.version, set: 'version_note',
      label: `Note on ${data.version}`, getTime: () => (view.video ? view.video.currentTime : 0),
      onPending: view.notes.pending, onSaved: (record, pending) => { view.notes.saved(record, pending); },
      onFailed: view.notes.failed, onUndone: (record) => view.notes.remove(record.id),
      onCancel: () => closeNoteComposer(view),
    });
  }
  if (!view.noteForm.isConnected) fill(view.noteSlot, view.noteForm);
  view.noteForm.refreshHint();
  view.noteForm.focusText({ scroll: true });
  keepNoteInView(view);
  announce(`Note at ${fmtT(view.video ? view.video.currentTime : 0)}`);
}

async function renderWatch(room, seq, route) {
  if (room.kind === 'library') return renderLibraryWatch(room, seq, route);
  const url = `/api/projects/${enc(room.key)}/candidates/${enc(room.version)}`;
  const data = await getJSON(url);
  if (seq !== renderSeq) return;
  const video = buildVideo(data, `Master film for ${room.version}`);
  const view = {
    data, video, room, timelineBox: h('div', { class: 'timeline' }), noteSlot: h('div', { class: 'note-slot' }),
    side: h('div', { class: 'side' }), lanesOpen: Boolean(prefs.moreLanes), findingFilter: 'all', findingsExpanded: false,
  };
  state.video = data.media.master ? video : null;
  const myNotes = data.reactions.filter((r) => r.kind !== 'prefer');
  view.notes = liveReactions(myNotes, 'No notes on this version yet. Press N while it plays to add one.');
  view.noteButton = h('button', { type: 'button', class: 'btn', 'data-focus-key': 'watch:note', on: { click: () => openNoteComposer(view) } },
    icon('note'), h('span', { text: `Note at ${fmtT(0)}` }));
  const lanesId = nextId('lanes');
  const extra = h('div', { id: lanesId, hidden: !view.lanesOpen });
  const lanesToggle = h('button', {
    type: 'button', class: 'btn quiet small', 'aria-expanded': String(view.lanesOpen), 'aria-controls': lanesId, 'data-focus-key': 'watch:lanes',
    on: {
      click: () => {
        view.lanesOpen = !view.lanesOpen;
        prefs.moreLanes = view.lanesOpen;
        savePrefs();
        lanesToggle.setAttribute('aria-expanded', String(view.lanesOpen));
        lanesToggle.lastChild.textContent = view.lanesOpen ? 'Fewer lanes' : 'More lanes';
        extra.hidden = !view.lanesOpen;
        if (view.lanesOpen && !extra.childElementCount) extra.append(filmstrip(view));
        drawTimeline(view);
      },
    },
  }, h('span', { text: view.lanesOpen ? 'Fewer lanes' : 'More lanes' }));
  if (view.lanesOpen) extra.append(filmstrip(view));
  const facts = [
    data.duration_s ? fmtDuration(data.duration_s) : null,
    data.metrics && data.metrics.integrated_lufs !== null && data.metrics.integrated_lufs !== undefined ? `${fmtNum(data.metrics.integrated_lufs)} LUFS` : null,
    data.source === 'governed' || data.source === 'both' ? 'engine run' : 'built outside the engine',
    data.live ? 'in progress' : null,
  ].filter(Boolean).join(' \u00b7 ');
  const drawSide = () => {
    fill(view.side, verdictLines(data, { full: true, collapsed: true, open: view.verdictsOpen, brief: room.job.brief_runtime, onToggle: (open) => { view.verdictsOpen = open; } }), findingsBlock(view),
      h('section', { 'aria-labelledby': 'my-notes-h' }, h('div', { class: 'section-head' }, h('h2', { id: 'my-notes-h', text: `My notes on ${data.version}` })), view.notes.el));
  };
  drawSide();
  fill(room.panel, h('div', { class: 'watch' },
    h('section', { 'aria-label': 'Player and timeline' },
      view.playerBox = h('div', { class: 'player' }, data.media.master ? video : noMedia(data)),
      view.timelineBox,
      view.noteSlot,
      h('div', { class: 'tl-tools' }, view.noteButton, lanesToggle, h('span', { class: 'meta key-hint', text: 'N adds a note, Space plays' })),
      h('p', { class: 'meta facts', text: facts }),
      extra,
      data.notes && data.notes.length ? h('details', { class: 'work-notes' }, h('summary', { text: `Work notes (${data.notes.length})` }), notesTab(data)) : null),
    view.side));
  drawTimeline(view);
  video.addEventListener('timeupdate', () => updatePlayhead(view));
  video.addEventListener('loadedmetadata', () => { if (!data.duration_s) drawTimeline(view); });
  for (const event of ['pause', 'seeked']) video.addEventListener(event, () => { if (view.noteForm) view.noteForm.refreshHint(); });
  room.noteAtTime = () => openNoteComposer(view);
  room.applyQuery = (r) => {
    const t = r.query.get('t');
    const f = r.query.get('f');
    const key = `${room.version}|${t}|${f}`;
    if (key === view.lastQuery) return;
    view.lastQuery = key;
    if (t !== null && t !== '' && Number.isFinite(Number(t))) seekTo(Number(t), { quiet: true });
    if (f) highlightFinding(view, f, true);
  };
  room.refresh = async (pollSeq) => {
    const next = await getJSON(url);
    if (pollSeq !== renderSeq || state.room !== room || room.tab !== 'watch') return;
    if (JSON.stringify(next.findings) !== JSON.stringify(view.data.findings) || JSON.stringify(next.reactions) !== JSON.stringify(view.data.reactions)) {
      if (view.side.querySelector('.composer') || view.noteSlot.querySelector('textarea:focus')) return;
      view.data = next;
      Object.assign(data, next);
      preserveFocus(() => { drawSide(); drawTimeline(view); });
    }
  };
}

async function renderLibraryWatch(room, seq) {
  const version = room.lib.versions.find((v) => v.label === room.version);
  const files = [version.primary, ...version.variants];
  const item = files.find((f) => f.id === room.i) || version.primary;
  const records = await getJSON('/api/reactions?pid=library').catch(() => []);
  if (seq !== renderSeq) return;
  const video = libraryPlayer(item, `${room.name}, ${version.label}: ${item.name}`);
  state.video = video instanceof HTMLVideoElement ? video : null;
  const notes = libraryNotes(item, version.label, records, () => (state.video ? state.video.currentTime : 0));
  const playable = files.filter((f) => f.playable);
  const buttons = files.slice(0, 12).map((file) => h('button', {
    type: 'button', class: 'btn small', 'aria-pressed': String(file.id === item.id), tabindex: file.id === item.id ? '0' : '-1',
    'data-focus-key': `variant:${file.id}`, disabled: !file.playable,
    on: { click: () => room.select({ i: file.id }) },
  }, roleChip(file.role), h('span', { text: file.name })));
  const switcher = files.length > 1 ? h('div', { class: 'btn-row', role: 'group', 'aria-label': `Files of ${version.label}, ${playable.length} playable` }, buttons,
    files.length > 12 ? h('span', { class: 'meta', text: `and ${files.length - 12} more under Files` }) : null) : null;
  if (switcher) switcher.addEventListener('keydown', (event) => rovingKeys(event, buttons));
  fill(room.panel, h('div', { class: 'watch' },
    h('section', { 'aria-label': 'Player' }, h('div', { class: 'player' }, video), switcher, itemFacts(item)),
    h('div', { class: 'side' }, notes.el)));
  room.noteAtTime = () => { notes.form.selectOption('note_time'); notes.form.focusText(); };
  room.applyQuery = (r) => {
    const t = r.query.get('t');
    if (t !== null && t !== '' && Number.isFinite(Number(t)) && t !== room.lastT) { room.lastT = t; seekTo(Number(t), { quiet: true }); }
  };
}

/* ------------------------------------------------------------------ Decide: the decision sheet */

/* The decision a Decide address names. Never another one: a gate that is not live is explained, not replaced. */
function decisionFor(room) {
  const pick = (list) => (room.g
    ? list.find((d) => d.node === room.g && (!d.version || d.version === room.version))
    : list.find((d) => d.version && d.version === room.version) || list.find((d) => !d.version)) || null;
  return pick(roomDecisions(room)) || asDecision(room, pick(roomDecided(room)));
}

/* Never claim more than happens: only a gate the engine run really waits at keeps waiting there; a release of a
   version built outside the engine is recorded for the producer and nothing more. */
function engineWaitClause(gate, data = null) {
  if (!gate || !gate.engine_status) return '';
  const engineWaits = /WAITING/i.test(String(gate.engine_status || '')) || gate.state === 'waiting';
  const engineVersion = !gate.release || (data && ['governed', 'both'].includes(data.source));
  return engineWaits && engineVersion ? `The engine keeps waiting at ${nodeTag(gate.num)} until it is approved there.` : '';
}

/* Said before acting, so in the future sense; the past tense appears only once a decision is saved. */
function effectLine(gate, data = null, kind = 'approve') {
  const lead = kind === 'request_changes' ? 'Requesting changes records your reasons for the producer.' : 'Approving records your decision for the producer.';
  return [lead, engineWaitClause(gate, data)].filter(Boolean).join(' ');
}

function savedEffect(gate, data, saved) {
  const clause = engineWaitClause(gate, data);
  return [`${saved.kind === 'approve' ? 'Your approval was' : 'Your request for changes was'} recorded for the producer `, timeEl(saved.ts),
    '; waiting for the producer.', clause ? ` ${clause}` : ''];
}

/* The ask in one sentence: "Approve the objective, audience, runtime cap and quality bar." */
function askSentence(decision, gate, version) {
  if (decision.kind === 'release') return `Release ${version}, or request changes with your reasons.`;
  const words = String((gate && gate.decision) || askWords(decision) || '').replace(/[.\s]+$/, '');
  return words ? `${words}.` : '';
}

function decisionTitle(decision) {
  if (decision.kind === 'release') return `Release ${decision.version}`;
  if (decision.kind === 'gate') return nodeTitle(decision);
  return decision.words;
}

function isOpenFinding(finding) {
  if (finding.state) return finding.state !== 'resolved';
  const first = (String(finding.status || '').toLowerCase().match(/[a-z]+/) || [''])[0];
  return !['resolved', 'closed', 'fixed', 'done', 'verified'].includes(first);
}

function levelCounts(findings) {
  const open = findings.filter(isOpenFinding);
  return { open, counts: LEVELS.map((level) => [level, open.filter((f) => f.level === level).length]).filter(([, n]) => n) };
}

const BRIEF_WORDS = {
  objective: 'Objective', audience: 'Audience', business_context: 'Business context', success_criteria: 'Quality bar (success criteria)',
  quality_bar: 'Quality bar', runtime_target_s: 'Runtime target', runtime_max_s: 'Runtime cap', runtime_cap_s: 'Runtime cap', constraints: 'Constraints',
};

function briefLabel(key) {
  return BRIEF_WORDS[key] || String(key).replace(/_s$/, '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

function briefValue(key, value) {
  if (Array.isArray(value)) return value.length ? h('ul', {}, value.map((item) => h('li', { text: String(item) }))) : h('span', { class: 'meta', text: 'none' });
  if (typeof value === 'number' && /_s$/.test(key)) return `${fmtNum(value, Number.isInteger(value) ? 0 : 1)} s`;
  if (value === null || value === undefined || value === '') return h('span', { class: 'meta', text: 'not set' });
  return String(value);
}

/* A versionless gate's own evidence: the artifacts it asks you to approve, its purpose and the run's task packet. */
function gateEvidenceBlock(evidence) {
  const kids = [h('h3', { id: 'evidence-h', text: 'What you approve' })];
  if (!evidence) {
    kids.push(h('p', { class: 'empty', text: 'The evidence for this gate needs a newer server; restart Glass Box to see it. The node details under Pipeline show the task packet and files.' }));
    return h('section', { class: 'decide-block', 'aria-labelledby': 'evidence-h' }, kids);
  }
  for (const artifact of evidence.artifacts || []) {
    if (artifact.fields) {
      kids.push(h('dl', { class: 'brief' }, artifact.fields.flatMap((field) => [h('dt', { text: briefLabel(field.key) }), h('dd', {}, briefValue(field.key, field.value))])));
    } else if (artifact.text) {
      kids.push(h('details', {}, h('summary', { text: artifact.path }), h('pre', { text: artifact.text })));
    } else if (!artifact.exists) {
      kids.push(h('p', { class: 'empty', text: `${artifact.path} is listed by the engine run but missing on disk.` }));
    }
    kids.push(h('p', { class: 'meta' }, 'From ', h('span', { class: 'path', text: artifact.path }), artifact.status && artifact.status !== 'current' ? ` (${artifact.status})` : ''));
  }
  if (!(evidence.artifacts || []).length) kids.push(h('p', { class: 'empty', text: 'The engine run has recorded no artifact for this gate yet.' }));
  const contract = evidence.contract || {};
  if (contract.purpose) kids.push(h('p', { class: 'sub' }, h('span', { class: 'label', text: 'Purpose' }), ' ', textOf(contract.purpose)));
  if (Array.isArray(contract.completion) && contract.completion.length) {
    kids.push(h('details', {}, h('summary', { text: 'Done when' }), bulletList(contract.completion)));
  }
  if (evidence.task_packet && evidence.task_packet.text) {
    kids.push(h('details', {}, h('summary', { text: `Task packet${evidence.task_packet.truncated ? ' (shortened)' : ''}` }),
      h('p', { class: 'path', text: evidence.task_packet.path }), h('pre', { text: evidence.task_packet.text })));
  }
  return h('section', { class: 'decide-block', 'aria-labelledby': 'evidence-h' }, kids);
}

/* A release decision's own evidence: the version, its checks and reviews, and the caveat. */
function releaseEvidenceBlock(data, gate) {
  const reviews = data.reviews || [];
  const passing = reviews.filter((r) => r.verdict_class === 'pass').length;
  const strip = (data.verdict_strip || []).find((item) => item.type === 'gates');
  const checks = strip && strip.total ? (strip.failing ? `${strip.failing} of ${strip.total} automatic checks fail` : `all ${strip.total} automatic checks pass`) : 'no automatic checks recorded';
  return h('section', { class: 'decide-block', 'aria-labelledby': 'evidence-h' },
    h('h3', { id: 'evidence-h', text: `What you release: ${data.version}` }),
    h('p', { class: 'sub', text: `${data.version}${data.duration_s ? `, ${fmtDuration(data.duration_s)}` : ''}, ${data.source === 'governed' || data.source === 'both' ? 'from the engine run' : 'built outside the engine'}: ${passing} of ${plural(reviews.length, 'review')} pass and ${checks}.` }),
    gate && gate.gates_reason && !gate.gates_passed ? h('p', { class: 'sub', text: `Not passed yet: ${gate.gates_reason}.` }) : null,
    h('p', { class: 'caveat', text: CAVEAT_TEXT }));
}

/* A Decide address whose gate is not the live decision: say why and link to the one that is. */
async function renderDecideMissing(room, seq) {
  const decisions = roomDecisions(room);
  if (!room.g) {
    fill(room.panel, roomError('Choose a decision', decisions.length ? `No decision waits on ${room.version} itself. This film has ${plural(decisions.length, 'decision')}:` : 'No gate or release waits for you on this film right now.',
      decisions.length ? null : filmHref(room.filmId, { v: room.version }), 'Watch this film'),
    decisions.length ? h('ul', { class: 'decide-choices' }, decisions.map((d) => h('li', {}, h('a', { href: filmHref(room.filmId, { v: d.version || room.version, tab: 'decide', g: d.kind === 'findings' ? null : d.node }) }, d.words)))) : null);
    return;
  }
  let gate = null;
  if (room.kind === 'job') {
    try {
      const board = await getJSON(`/api/projects/${enc(room.key)}/gates?version=${enc(room.version)}`);
      gate = annotateGate((board.human || []).find((g) => g.node === room.g) || null, room.version, decisions);
    } catch { gate = null; }
    if (seq !== renderSeq) return;
  }
  const words = gate ? (gate.release ? `Release ${room.version}` : nodeTitle(gate)) : `This gate on ${room.version}`;
  if (gate && gate.superseded_by) {
    const live = decisions.find((d) => d.kind === 'release' && d.version === gate.superseded_by);
    fill(room.panel, h('div', { class: 'room-error', role: 'status' },
      h('h2', { tabindex: '-1', text: `${words}: superseded by ${gate.superseded_by}` }),
      h('p', { class: 'sub', text: `${gate.superseded_by} is the latest fully reviewed version, so its release decision is the one that waits. ${room.version} keeps its own verdict in the rail.` }),
      h('p', { class: 'btn-row' },
        h('a', { class: 'btn', href: live ? filmHref(room.filmId, { v: live.version, tab: 'decide', g: live.node }) : filmHref(room.filmId, { v: gate.superseded_by, tab: 'watch' }) },
          live ? `Open the release decision for ${gate.superseded_by}` : `Watch ${gate.superseded_by}`),
        h('a', { class: 'btn quiet', href: filmHref(room.filmId, { v: room.version, tab: 'watch' }) }, `Watch ${room.version}`))));
    return;
  }
  fill(room.panel, roomError(`${words} does not wait for you`,
    gate ? `Its state is ${gateStateLabel(gate).toLowerCase()}. The Inbox lists every decision that waits.` : 'Nothing waits at this gate now. The Inbox lists every decision that waits.',
    hrefs.inbox(), 'Open the Inbox'));
}

async function renderDecide(room, seq) {
  const decisions = roomDecisions(room);
  const decision = decisionFor(room);
  if (!decision) { await renderDecideMissing(room, seq); return; }
  const pid = room.key;
  const versionless = !decision.version;
  const version = decision.version || room.job.latest_version || room.version;
  const gateNode = decision.node;
  const [data, board, evidence] = await Promise.all([
    getJSON(`/api/projects/${enc(pid)}/candidates/${enc(version)}`),
    getJSON(`/api/projects/${enc(pid)}/gates?version=${enc(version)}`),
    versionless && gateNode ? getJSON(`/api/projects/${enc(pid)}/gates/${enc(gateNode)}`).catch(() => null) : Promise.resolve(null),
  ]);
  if (seq !== renderSeq) return;
  const gateFromBoard = gateNode ? (board.human || []).find((g) => g.node === gateNode) : null;
  const gate = annotateGate((evidence && evidence.gate) || gateFromBoard || (data.release_gate && data.release_gate.node === gateNode ? data.release_gate : null), version, decisions);
  const compareWith = data.compare_with && data.compare_with !== version ? data.compare_with : null;
  let compare = null;
  if (compareWith) {
    try { compare = await getJSON(`/api/projects/${enc(pid)}/compare?a=${enc(compareWith)}&b=${enc(version)}`); } catch { compare = null; }
    if (seq !== renderSeq) return;
  }
  const video = buildVideo(data, `Master film for ${version}`);
  state.video = data.media.master ? video : null;
  const { open, counts } = levelCounts(data.findings);
  const reacted = new Set(data.reactions.filter((r) => r.target && r.target.type === 'finding').map((r) => r.target.id));
  const blocking = open.filter((f) => ['P1', 'P2', 'P3'].includes(f.level));
  const title = decisionTitle(decision);
  const brief = (room.job && room.job.brief_runtime) || (room.row && room.row.brief_runtime) || {};
  const conflicts = runtimeConflicts({
    brief, owner: brief.node && brief.node === gateNode ? 'this gate' : 'the brief', version,
    duration: data.duration_s, versionWindow: data.runtime_window, frames: room.frames,
  });
  const effect = h('p', { class: 'band-effect' });

  const choices = [...decisions, ...roomDecided(room).map((d) => asDecision(room, d))];
  const same = (d) => d.node === decision.node && (d.version || null) === (decision.version || null) && Boolean(d.decided) === Boolean(decision.decided);
  const picker = choices.length > 1 ? h('nav', { class: 'decide-pick', 'aria-label': 'Decisions on this film' },
    h('span', { class: 'label', text: decisions.length ? `${plural(decisions.length, 'decision')} waiting${choices.length > decisions.length ? `, ${choices.length - decisions.length} decided` : ''}` : `${plural(choices.length, 'decision')} you recorded` }),
    choices.map((d) => h('a', {
      class: 'btn small', href: filmHref(room.filmId, { v: d.version || room.version, tab: 'decide', g: d.kind === 'findings' ? null : d.node }),
      'aria-current': same(d) ? 'true' : null,
    }, d.decided ? h('span', { class: `mark m-${decidedMark(d.decided)}`, 'aria-hidden': 'true' }, markGlyph(decidedMark(d.decided))) : null, decisionTitle(d), d.decided ? h('span', { class: 'visually-hidden', text: ', decided by you' }) : null))) : null;

  const actions = h('div', { class: 'band-actions' });
  const drawActions = (saved) => {
    const decide = canDecide(gate) || (decision.kind === 'findings' && !gate);
    const reasonId = nextId('reason');
    const reason = h('textarea', { id: reasonId, rows: 3, maxlength: 4000, 'data-focus-key': 'decide:reason' });
    if (saved) fill(effect, ...savedEffect(gate, data, saved)); else effect.textContent = effectLine(gate, data, 'approve');
    const reasonBox = h('div', { class: 'band-reason', hidden: true },
      h('label', { for: reasonId, text: 'What should change? (needed)' }), reason);
    const statusLine = h('p', { class: 'band-status', role: 'status' });
    const approve = h('button', { type: 'button', class: 'btn primary approve', 'data-focus-key': 'decide:approve', disabled: !decide }, icon('check'), 'Approve');
    const changes = h('button', { type: 'button', class: 'btn changes', 'data-focus-key': 'decide:changes', disabled: !decide }, icon('cross'), h('span', { text: 'Request changes' }));
    const target = cleanTarget({ type: 'gate', id: gateNode || (data.release_gate && data.release_gate.node), version: gate && gate.release ? version : (decision.version || null) });
    const summary = `${room.name}, ${versionless ? 'the film' : version}, ${decision.words}; ${versionless ? 'no version is approved by this gate' : plural(open.length, 'open finding')}${!versionless && blocking.length ? ` (${blocking.length} at P1 to P3)` : ''}.`;
    const record = async (kind, text) => {
      approve.disabled = true;
      changes.disabled = true;
      statusLine.textContent = 'Saving\u2026';
      try {
        const body = { pid, kind, target, tags: [], text };
        if (gate && gate.release) body.version = version;
        const record = await postReaction(body);
        announce(kind === 'approve' ? 'Approval recorded' : 'Request for changes recorded');
        await loadFilms(true);
        renderMasthead();
        drawActions(record);
      } catch (error) {
        statusLine.textContent = `Not saved: ${error.message}`;
        approve.disabled = !decide;
        changes.disabled = !decide;
      }
    };
    approve.addEventListener('click', async () => {
      const ok = await confirmDialog(`${decision.kind === 'release' ? title : `Approve ${title}`}?`, `You approve: ${summary} ${effectLine(gate, data, 'approve')}`, 'Approve');
      if (ok) record('approve', '');
    });
    changes.addEventListener('click', async () => {
      if (reasonBox.hidden) {
        reasonBox.hidden = false;
        changes.lastChild.textContent = 'Send the request for changes';
        effect.textContent = effectLine(gate, data, 'request_changes');
        reason.focus();
        return;
      }
      const text = reason.value.trim();
      if (!text) { statusLine.textContent = 'Write what should change first.'; reason.focus(); return; }
      const ok = await confirmDialog('Request changes?', `You ask for changes on: ${summary} ${effectLine(gate, data, 'request_changes')}`, 'Request changes');
      if (ok) record('request_changes', text);
    });
    const recorded = gate ? recordedDecision(gate) : [];
    fill(actions,
      !saved && gate && !decide ? h('p', { class: 'band-status', text: `This gate takes no decision now (${gateStateLabel(gate)}).` }) : null,
      !saved ? h('div', { class: 'btn-row' }, approve, changes) : null,
      !saved ? reasonBox : null,
      statusLine,
      recorded.length ? h('div', { class: 'band-recorded' }, recorded) : null);
  };
  /* A gate the owner decided: his decision and the producer's thread first; a new decision only where the server
     takes one (the engine still waits there, or the release is still the latest), and it stays a note for the producer. */
  const drawDecided = () => {
    const record = { ...((gate && gate.decision_record) || decision.decided || {}), actor: 'owner', pid };
    effect.textContent = (gate && gate.engine_waits && engineWaitClause(gate, data)) || 'Your decision is recorded for the producer; the engine is not changed.';
    const again = canDecide(gate) ? h('button', {
      type: 'button', class: 'btn changes', 'data-focus-key': 'decide:again',
      on: { click: () => { drawActions(null); const first = actions.querySelector('[data-focus-key="decide:approve"]'); if (first) first.focus(); } },
    }, 'Record a new decision') : null;
    fill(actions,
      h('div', { class: 'decided-block' },
        h('p', { class: 'decided-line' }, statusMark(decidedMark(record), gate && gate.engine_waits ? DECIDED_WORDS : MARK_WORDS[decidedMark(record)]),
          ' ', h('span', { class: 'when', text: fmtWhen(record.ts) })),
        record.text ? h('p', { class: 'reaction-text', text: record.text }) : null,
        threadBlock(record)),
      again ? h('div', { class: 'btn-row' }, again) : h('p', { class: 'band-status', text: 'The server takes no new decision here now.' }));
  };
  if (decision.decided && gate && gate.state === 'recorded') drawDecided(); else drawActions(null);

  const ask = askSentence(decision, gate, version);
  const band = h('section', { class: 'decide-band', 'aria-labelledby': 'decide-title' },
    h('h2', { id: 'decide-title', tabindex: '-1', text: title }),
    ask ? h('p', { class: 'band-ask', text: ask }) : null,
    h('p', { class: 'band-sentence', text: decisionSentence(decision, room.row, { runtime: !conflicts.length }) }),
    conflicts.length ? h('div', { class: 'band-conflicts' },
      h('p', { class: 'band-label' }, icon('warn'), conflicts.length === 1 ? 'Conflict' : 'Conflicts'),
      h('ul', {}, conflicts.map((line) => h('li', { text: line })))) : null,
    effect,
    actions);

  const changed = compare ? h('section', { class: 'decide-block', 'aria-labelledby': 'changed-h' },
    h('h3', { id: 'changed-h', text: `What changed since ${compareWith}` }),
    whatChanged(compare, { names: { a: compareWith, b: version }, brief }),
    h('p', {}, h('a', { href: filmHref(room.filmId, { tab: 'compare', a: compareWith, b: version }), text: `Compare ${compareWith} and ${version} side by side` })))
    : h('section', { class: 'decide-block' }, h('h3', { text: 'What changed' }), h('p', { class: 'empty', text: 'No earlier version to compare with.' }));
  const findings = h('section', { class: 'decide-block', 'aria-labelledby': 'open-h' },
    h('h3', { id: 'open-h', text: `Open findings on ${version}` }),
    counts.length ? h('div', { class: 'level-counts' }, counts.map(([level, n]) => h('span', {}, levelChip(level), h('span', { class: 'count', text: ` ${n}` }))),
      h('a', { href: filmHref(room.filmId, { v: version, tab: 'watch' }), text: 'Review them in Watch' })) : h('p', { class: 'empty', text: 'No open findings.' }),
    blocking.length ? findingsList(blocking.slice(0, 3), { pid, version, reacted, answers: findingAnswers(data.reactions), onReacted: () => refreshSoon() }) : null);
  const length = data.duration_s ? `, ${fmtDuration(data.duration_s)}` : '';
  const player = h('figure', { class: 'compact-player' }, h('div', { class: 'player' }, data.media.master ? video : noMedia(data)),
    h('figcaption', { class: 'meta', text: versionless ? `Latest version, for context: ${version}${length}` : `${version}${length}` }));
  const verdicts = verdictLines(data, { full: false, headingLevel: 3 });
  verdicts.classList.add('decide-verdicts');
  const blocks = versionless
    ? [gateEvidenceBlock(evidence),
      h('div', { class: 'context-head' }, h('h3', { class: 'h2-like', text: `Latest version, for context: ${version}` }),
        h('p', { class: 'sub', text: 'This gate approves the film\u2019s direction, not a version; the latest version is shown so you can judge it in context.' })),
      changed, findings]
    : [releaseEvidenceBlock(data, gate), changed, findings];
  blocks.forEach((block, index) => block.classList.add(`dl-row-${index + 2}`));
  /* The band and the film side by side at the top; the evidence follows on the left, the verdicts on the right. */
  fill(room.panel, picker || '', h('div', { class: 'decide-layout' }, band, player, ...blocks, verdicts));
}

/* ------------------------------------------------------------------ Compare */

/* "What changed": each cell names its rule; a cell whose measurement basis differs reads Uncertain, as the summary counts it. */
/* One sentence from the cells shown: neutral and unchanged both read "no change" (the API keeps both values). */
function changeSummaryText(changes, name) {
  const bucket = (change) => (change.uncertain && change.polarity !== 'n/a' ? 'uncertain'
    : ['neutral', 'unchanged'].includes(change.polarity) ? 'same' : change.polarity);
  const count = (key) => changes.filter((change) => bucket(change) === key).length;
  const parts = [`worse on ${count('worse')}`, `better on ${count('better')}`, `no change on ${count('same')}`];
  if (count('uncertain')) parts.push(`uncertain on ${count('uncertain')}`);
  if (count('n/a')) parts.push(`not comparable on ${count('n/a')}`);
  return `${name} is ${parts.join(', ')}`;
}

/* "The brief caps the film at 65 s: v21 within, v28 over by 28.1 s." beside the duration's own script targets. */
function briefLine(change, brief, side) {
  const window = briefWindow(brief);
  if (!window || change.key !== 'duration_s') return null;
  const judge = (value) => {
    const d = Number(value);
    if (!(d > 0)) return 'not measured';
    if (window.high !== null && d > window.high) return `over by ${fmtNum(d - window.high, 1)} s`;
    if (window.low !== null && d < window.low) return `under by ${fmtNum(window.low - d, 1)} s`;
    return 'within';
  };
  return h('span', { class: 'wc-reason wc-brief', text: `The brief ${windowWords(window)}: ${side.a} ${judge(change.a)}, ${side.b} ${judge(change.b)}.` });
}

function whatChanged(data, { names = null, brief = null } = {}) {
  /* One findings metric: open findings by level; the raw total of findings would say the same thing twice. */
  const changes = (data.changes || []).filter((change) => change.key !== 'findings');
  const summary = data.change_summary;
  const side = { a: names ? names.a : 'A', b: names ? names.b : 'B' };
  const summaryText = summary ? changeSummaryText(changes, side.b) : '';
  const cell = (change) => {
    const format = CHANGE_FORMAT[change.key] || ((v) => String(v ?? 'unknown'));
    const delta = changeDelta(change);
    const meta = POLARITY[change.polarity] || POLARITY['n/a'];
    const reading = change.uncertain
      ? h('span', { class: 'pol pol-na' }, icon('question'), 'Uncertain')
      : polarityChip(change);
    return h('div', { class: `wc-cell${change.uncertain ? ' uncertain' : ''}` },
      h('span', { class: 'label', text: plainWords(change.label) }),
      h('span', { class: 'wc-rule', title: change.rule_detail || null, text: plainWords(change.rule) }),
      h('span', { class: 'wc-values' }, h('span', { class: 'mono', text: format(change.a, change) }), h('span', { 'aria-hidden': 'true', text: ' to ' }),
        h('span', { class: 'mono', text: format(change.b, change) })),
      h('span', { class: 'wc-reading' }, reading, delta ? h('span', { class: 'meta', text: delta }) : null),
      change.uncertain ? h('span', { class: 'wc-reason', text: `On the raw values it reads ${meta.label}, but the measurement basis differs, so it counts as uncertain.` }) : null,
      change.reason ? h('span', { class: 'wc-reason', text: plainWords(change.reason) }) : null,
      briefLine(change, brief, side),
      basisChip(change.basis));
  };
  const others = (data.basis_changes || []).filter((item) => !changes.some((change) => change.basis
    && [change.basis.a && change.basis.a.path, change.basis.b && change.basis.b.path].includes(item.path)));
  return h('div', { class: 'what-changed-box' },
    summary ? h('p', { class: 'wc-summary', text: `${summaryText}.` }) : null,
    h('div', { class: 'what-changed' }, changes.map(cell)),
    (data.basis_changes || []).length
      ? h('details', { class: 'basis-changes' },
        h('summary', {}, `The mix reports state a different measurement basis for ${plural(data.basis_changes.length, 'measure')}`,
          others.length ? ` (${others.length} not shown above)` : '', '. A basis change can hide or fake a regression.'),
        h('ul', {}, data.basis_changes.map((item) => h('li', {},
          h('span', { class: 'mono', text: item.path }), h('div', { class: 'sub', text: `${side.a}: ${item.a || 'not stated'}` }),
          h('div', { class: 'sub', text: `${side.b}: ${item.b || 'not stated'}` })))))
      : null);
}

/* Two players with one timeline: Play both, a shared seek bar, and the sound from A or B. */
function syncedPlayers(sides) {
  const videos = sides.map((side) => side.video).filter((v) => v instanceof HTMLVideoElement);
  for (const video of videos) video.removeAttribute('controls');
  if (videos[1]) videos[1].muted = true;
  const maxDuration = Math.max(...sides.map((side) => side.duration || 0)) || 60;
  const range = h('input', { type: 'range', min: '0', max: String(maxDuration), step: '0.1', value: '0', 'aria-label': 'Seek both players' });
  const clock = h('output', { class: 'mono', 'aria-label': 'Shared position', text: `${fmtT(0)} / ${fmtT(maxDuration)}` });
  const play = h('button', { type: 'button', class: 'btn', 'data-focus-key': 'cmp-play' });
  const playing = () => videos.some((v) => !v.paused);
  const label = () => fill(play, icon(playing() ? 'pause' : 'play', playing() ? '' : 'solid'), playing() ? 'Pause both' : 'Play both');
  const clampTo = (video, t) => (Number.isFinite(video.duration) ? Math.min(t, video.duration) : t);
  play.addEventListener('click', () => {
    if (playing()) { videos.forEach((v) => v.pause()); label(); return; }
    const t = Number(range.value);
    for (const video of videos) if (Math.abs(video.currentTime - t) > 0.3) video.currentTime = clampTo(video, t);
    Promise.allSettled(videos.map((v) => v.play())).then(label);
  });
  const lead = () => videos.reduce((best, v) => ((Number.isFinite(v.duration) ? v.duration : 0) > (best && Number.isFinite(best.duration) ? best.duration : 0) ? v : best), videos[0]);
  const tick = () => {
    const leader = lead();
    if (!leader) return;
    const t = leader.currentTime;
    range.value = String(t);
    clock.textContent = `${fmtT(t)} / ${fmtT(maxDuration)}`;
    for (const other of videos) {
      if (other !== leader && !other.paused && !other.ended && Math.abs(other.currentTime - t) > 0.25 && (!Number.isFinite(other.duration) || t < other.duration)) other.currentTime = t;
    }
  };
  for (const video of videos) {
    video.addEventListener('timeupdate', tick);
    for (const event of ['play', 'pause', 'ended']) video.addEventListener(event, label);
  }
  range.addEventListener('input', () => {
    const t = Number(range.value);
    for (const video of videos) video.currentTime = clampTo(video, t);
    clock.textContent = `${fmtT(t)} / ${fmtT(maxDuration)}`;
  });
  label();
  const listen = h('fieldset', {}, h('legend', { class: 'label', text: 'Sound from' }), ['A', 'B'].map((which, index) => {
    const id = nextId(`listen-${which}`);
    return h('span', { class: 'check' }, h('input', {
      type: 'radio', id, name: 'cmp-listen', value: which, checked: index === 0,
      on: { change: () => videos.forEach((video, i) => { video.muted = i !== index; }) },
    }), h('label', { for: id, text: which }));
  }));
  const figures = h('div', { class: 'cmp-players' }, sides.map((side, index) => h('figure', {},
    h('figcaption', {}, h('span', { class: 'label', text: index ? 'B' : 'A' }), h('strong', { text: side.label }), side.mark || null,
      h('span', { class: 'meta', text: fmtDuration(side.duration) })),
    h('div', { class: 'player' }, side.video),
    side.path ? h('p', { class: 'path', text: side.path }) : null)));
  return frag(figures, h('div', { class: 'cmp-controls', role: 'group', 'aria-label': 'Shared playback for A and B' }, play, range, clock, listen));
}

/* Preference in two steps: choose A, B or tie, then record it (with Undo). */
function preferenceBox({ pid, a, b, labelA, labelB, records, context }) {
  const pair = records.filter((r) => r.kind === 'prefer' && ((r.a === a && r.b === b) || (r.a === b && r.b === a)));
  const list = liveReactions(pair, 'No preference recorded for this pair yet.');
  const noteId = nextId('prefer-note');
  const note = h('textarea', { id: noteId, rows: 2, maxlength: 4000 });
  const status = h('p', { class: 'composer-status', role: 'status' });
  const name = nextId('prefer');
  const choices = [['a', `A, ${labelA}`], ['b', `B, ${labelB}`], ['tie', 'Tie']].map(([value, text]) => {
    const id = nextId('prefer-choice');
    return h('label', { class: 'prefer-choice', for: id }, h('input', { type: 'radio', id, name, value }), h('span', { text }));
  });
  const record = h('button', { type: 'button', class: 'btn primary', 'data-focus-key': 'prefer:record' }, 'Record preference');
  record.addEventListener('click', async () => {
    const picked = choices.map((label) => label.querySelector('input')).find((input) => input.checked);
    if (!picked) { status.textContent = 'Choose A, B or Tie first.'; return; }
    const body = { pid, kind: 'prefer', a, b, choice: picked.value, text: note.value.trim(), tags: [] };
    const pending = { ...body, library: context || null, id: nextId('pending'), ts: new Date().toISOString(), actor: 'owner', acks: [], _pending: true };
    list.pending(pending);
    record.disabled = true;
    try {
      const saved = await postReaction(body);
      list.saved(saved, pending);
      note.value = '';
      picked.checked = false;
      fill(status, undoLine(saved, { word: 'Preference recorded', onUndone: () => list.remove(saved.id) }));
      announce('Preference recorded. Undo is available for ten seconds.');
    } catch (error) {
      list.failed(pending);
      status.textContent = `Not saved: ${error.message}`;
    } finally {
      record.disabled = false;
    }
  });
  return h('section', { class: 'card', 'aria-labelledby': 'prefer-h' },
    h('h2', { id: 'prefer-h', text: 'Your preference' }),
    h('div', { class: 'prefer-box' },
      h('fieldset', {}, h('legend', { class: 'label', text: 'Which version is better?' }), choices),
      h('label', { for: noteId, class: 'label', text: 'Why (optional)' }), note,
      h('div', { class: 'btn-row' }, record), status, list.el));
}

function comparePickers(room, a, b) {
  const versions = room.versions;
  const picker = (id, label, value, which) => h('span', { class: 'field' }, h('label', { for: id, class: 'label', text: label }), h('select', {
    id, 'data-focus-key': `cmp:${which}`,
    on: { change: (event) => room.select(which === 'a' ? { a: event.target.value, b } : { a, b: event.target.value }) },
  }, versions.map((v) => h('option', { value: v, text: v, selected: v === value }))));
  return h('div', { class: 'cmp-pickers', role: 'group', 'aria-label': 'Choose the two versions' },
    picker('cmp-a', 'A', a, 'a'), picker('cmp-b', 'B', b, 'b'),
    h('button', { type: 'button', class: 'btn small quiet', on: { click: () => room.select({ a: b, b: a }) } }, 'Swap A and B'));
}

function scoreDeltas(data) {
  const changed = data.scores.filter((row) => row.change && row.change !== 'same');
  const cellFor = (row) => {
    if (row.change === 'worse') return h('td', { class: 'delta-worse' }, `${fmtNum(row.delta, 2)} worse`);
    if (row.change === 'better') return h('td', { class: 'delta-better' }, `+${fmtNum(row.delta, 2)} better`);
    if (row.change === 'same') return h('td', { class: 'delta-same', text: 'same' });
    return h('td', { class: 'delta-same', text: 'not comparable' });
  };
  const show = (row, side) => {
    const value = side === 'a' ? row.a : row.b;
    const result = side === 'a' ? row.a_result : row.b_result;
    if (value !== null && value !== undefined) return String(value);
    return result ? oneLine(result, 40) : 'none';
  };
  const table = (rows) => (rows.length
    ? h('div', { class: 'scroll-x' }, h('table', { class: 'scores' }, tableHead(['Reviewer', 'Dimension', 'Node', 'A', 'B', 'Change']),
      h('tbody', {}, rows.map((row) => h('tr', {},
        h('td', { text: row.reviewer_label }), h('td', { class: 'mono', text: row.dimension }), h('td', { class: 'mono', text: nodeLabel(row.node) }),
        h('td', { class: 'mono', text: show(row, 'a') }), h('td', { class: 'mono', text: show(row, 'b') }), cellFor(row))))))
    : h('p', { class: 'empty', text: 'No scores changed.' }));
  return h('details', { class: 'card' }, h('summary', {}, h('span', { class: 'h3-like', text: 'Score changes by dimension' }),
    h('span', { class: 'meta', text: `${changed.length} of ${data.scores.length} changed` })),
  table(changed), changed.length < data.scores.length ? h('details', {}, h('summary', { text: `All ${data.scores.length} scores` }), table(data.scores)) : null);
}

function verdictTable(data, a, b) {
  const rows = Object.entries(data.verdicts);
  return h('details', { class: 'card' }, h('summary', {}, h('span', { class: 'h3-like', text: 'Verdicts' }), h('span', { class: 'meta', text: plural(rows.length, 'reviewer') })),
    rows.length ? h('div', { class: 'scroll-x' }, h('table', {}, tableHead(['Reviewer', 'Node', `A (${a})`, `B (${b})`]),
      h('tbody', {}, rows.map(([key, row]) => h('tr', {},
        h('th', { scope: 'row', text: row.label, title: key }), h('td', { class: 'mono', text: nodeLabel(row.node) }),
        h('td', {}, verdictChip(row.a_class, row.a || undefined, row.a_worst, row.a_defects)),
        h('td', {}, verdictChip(row.b_class, row.b || undefined, row.b_worst, row.b_defects)))))))
      : h('p', { class: 'empty', text: 'Neither version has reviews.' }));
}

function metricTable(data) {
  const changeByKey = Object.fromEntries((data.changes || []).map((change) => [change.key, change]));
  const sideA = data.a;
  const sideB = data.b;
  return h('details', { class: 'card' }, h('summary', {}, h('span', { class: 'h3-like', text: 'Metric changes' })),
    h('div', { class: 'scroll-x' }, h('table', {}, tableHead(['Metric', `A (${sideA.version})`, `B (${sideB.version})`, 'Change (B minus A)', 'Reading']),
      h('tbody', {}, METRIC_ROWS.map(([key, name, format]) => {
        const va = key === 'duration_s' ? sideA.duration_s : sideA.metrics[key];
        const vb = key === 'duration_s' ? sideB.duration_s : sideB.metrics[key];
        const change = changeByKey[key];
        return h('tr', {}, h('th', { scope: 'row', text: name }), h('td', { class: 'mono', text: format(va) }),
          h('td', { class: 'mono', text: format(vb) }), h('td', { class: 'mono', text: deltaText(key, data.metrics_delta[key]) }),
          h('td', {}, change ? [change.uncertain ? h('span', { class: 'pol pol-na' }, icon('question'), 'Uncertain') : polarityChip(change),
            h('div', { class: 'sub', title: change.rule_detail || null, text: change.rule })] : h('span', { class: 'sub', text: 'no better or worse rule' }),
          data.metric_bases && data.metric_bases[key] && data.metric_bases[key].differs ? basisChip(data.metric_bases[key]) : null));
      })))));
}

/* The pair Compare shows: B is the address's b, else the selected version; A is a, else the version before B. */
function comparePair(room) {
  const versions = room.versions;
  const b = room.b || room.version || versions[0];
  const index = versions.indexOf(b);
  return { a: room.a || versions[index + 1] || versions[index - 1], b };
}

async function renderCompare(room, seq) {
  const versions = room.versions;
  if (versions.length < 2) {
    fill(room.panel, roomError('Only one version', 'Compare needs two versions of this film.', filmHref(room.filmId), 'Watch it'));
    return;
  }
  const { a, b } = comparePair(room);
  const unknown = [a, b].filter((v) => !versions.includes(v));
  if (unknown.length) {
    fill(room.panel, comparePickers(room, versions.includes(a) ? a : versions[1], versions.includes(b) ? b : versions[0]),
      roomError(`${joinList(unknown)} ${unknown.length === 1 ? 'is' : 'are'} not part of this film`, 'Pick two of its versions above; nothing is shown in place of a missing version.'));
    return;
  }
  if (a === b) {
    fill(room.panel, comparePickers(room, a, b), roomError('A and B are the same version', 'Pick two different versions to compare.'));
    return;
  }
  if (room.kind === 'library') {
    const find = (label) => room.lib.versions.find((v) => v.label === label);
    const records = await getJSON('/api/reactions?pid=library&kind=prefer').catch(() => []);
    if (seq !== renderSeq) return;
    const side = (label, which) => {
      const version = find(label);
      const item = version.primary;
      return { label, duration: item.duration_s, path: item.path, mark: roleChip(item.role), video: libraryPlayer(item, `${which}: ${label}, ${item.name}`), item };
    };
    const sideA = side(a, 'A');
    const sideB = side(b, 'B');
    const context = (s) => ({ id: s.item.id, film_id: room.lib.id, film: room.name, version: s.label, name: s.item.name });
    fill(room.panel, comparePickers(room, a, b), syncedPlayers([sideA, sideB]),
      preferenceBox({ pid: 'library', a: sideA.item.id, b: sideB.item.id, labelA: a, labelB: b, records, context: { a: context(sideA), b: context(sideB) } }));
    return;
  }
  const data = await getJSON(`/api/projects/${enc(room.key)}/compare?a=${enc(a)}&b=${enc(b)}`);
  if (seq !== renderSeq) return;
  const side = (s, which) => ({
    label: s.version, duration: s.duration_s, video: s.media.master ? buildVideo(s, `${which}: ${s.version}`) : noMedia(s),
    mark: s.source === 'governed' || s.source === 'both' ? h('span', { class: 'meta', text: 'engine run' }) : null,
  });
  fill(room.panel, comparePickers(room, a, b), syncedPlayers([side(data.a, 'A'), side(data.b, 'B')]),
    h('section', { 'aria-labelledby': 'wc-h' }, h('div', { class: 'section-head' }, h('h2', { id: 'wc-h', text: `What changed from ${a} to ${b}` })), whatChanged(data, { names: { a: 'A', b: 'B' }, brief: room.job && room.job.brief_runtime })),
    scoreDeltas(data), verdictTable(data, a, b), metricTable(data),
    preferenceBox({ pid: data.pid, a, b, labelA: a, labelB: b, records: data.preferences || [] }));
}

/* ------------------------------------------------------------------ Pipeline: the drawn workflow */

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) if (value !== null && value !== undefined) el.setAttribute(key, String(value));
  return el;
}

function pipelinePlan(nodes, run) {
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const waitingId = run && run.waiting_at && run.waiting_at.length ? run.waiting_at[0] : null;
  let runIndex = -1;
  if (waitingId && index.has(waitingId)) runIndex = index.get(waitingId);
  else nodes.forEach((node, i) => { if (node.provenance === 'governed') runIndex = i; });
  const kinds = nodes.map((node) => nodeRunKind(node));
  const segments = nodes.slice(1).map((node, i) => {
    const kind = kinds[i + 1];
    if (kind === 'engine') return i + 1 <= runIndex ? 'run' : 'run-late';
    if (kind === 'direct') return 'direct';
    if (kind === 'unrecorded') return 'unrecorded';
    return 'idle';
  });
  const running = nodes.findIndex((node) => node.status === 'running');
  return { runIndex, waitingIndex: waitingId && index.has(waitingId) ? index.get(waitingId) : -1, segments, running, kinds };
}

/* How a node ran, which is what its outline says: the engine run, outside the engine, passed but not recorded, or not run. */
function nodeRunKind(node) {
  const truth = nodeTruth(node);
  if (truth.status === 'unrecorded') return 'unrecorded';
  if (node.provenance === 'governed') return 'engine';
  if (node.provenance === 'direct' || node.status === 'direct') return 'direct';
  return 'not_run';
}

/* Lane-to-lane and row-to-row returns run through the gutter just above the next node, never along a lane rule.
   On phones the nodes stack and one vertical line runs down their left side. */
function connectorPath(a, b, { vertical = false, track = 9, first = false } = {}) {
  if (vertical) {
    const ay = a.y + Math.min(a.h / 2, 22);
    const by = b.y + Math.min(b.h / 2, 22);
    return `${first ? `M${track} ${ay}H${a.x}` : ''}M${track} ${ay}V${by}H${b.x}`;
  }
  const x1 = a.x + a.w;
  const y1 = a.y + a.h / 2;
  const x2 = b.x;
  const y2 = b.y + b.h / 2;
  if (Math.abs(y1 - y2) < 2 && x2 > x1) return `M${x1} ${y1}H${x2}`;
  const gutter = b.y - 11;
  return `M${x1} ${y1}H${x1 + 14}V${gutter}H${x2 - 14}V${y2}H${x2}`;
}

const SEGMENT_CLASS = { run: 'seg-run', 'run-late': 'seg-run', direct: 'seg-direct', unrecorded: 'seg-unrecorded', idle: 'seg-idle' };

/* Draw once, then only move: the paths keep their elements, so the trace animates on the first draw and never replays. */
function drawConnectors(wrap, svg, buttons, plan, { live }) {
  const box = wrap.getBoundingClientRect();
  if (!box.width) return;
  const vertical = window.matchMedia('(max-width: 760px)').matches;
  svg.setAttribute('viewBox', `0 0 ${Math.round(box.width)} ${Math.round(box.height)}`);
  const rects = buttons.map((button) => {
    const r = button.getBoundingClientRect();
    return { x: r.left - box.left, y: r.top - box.top, w: r.width, h: r.height };
  });
  const parts = plan.segments.map((kind, i) => connectorPath(rects[i], rects[i + 1], { vertical, first: i === 0 }));
  const runD = parts.filter((_, i) => plan.segments[i] === 'run').join(' ');
  let drawn = wrap._drawn;
  if (!drawn) {
    drawn = wrap._drawn = { segs: [], run: null, ring: null, dot: null };
    plan.segments.forEach((kind, i) => {
      if (kind === 'run') return;
      const el = svgEl('path', { class: SEGMENT_CLASS[kind] });
      drawn.segs.push({ el, i });
      svg.append(el);
    });
    if (runD) {
      drawn.run = svgEl('path', { class: 'seg-run trace' });
      drawn.run.addEventListener('animationend', () => {
        drawn.run.classList.remove('trace');
        drawn.run.style.removeProperty('--len');
      }, { once: true });
      svg.append(drawn.run);
    }
    if (plan.waitingIndex >= 0) { drawn.ring = svgEl('rect', { class: 'ring', rx: 11 }); svg.append(drawn.ring); }
    if (live && plan.running > 0) {
      drawn.dot = svgEl('circle', { class: 'signal', r: 3.5 });
      drawn.motion = svgEl('animateMotion', { dur: '2.4s', repeatCount: 'indefinite', keyPoints: '0;1', keyTimes: '0;1', calcMode: 'linear' });
      drawn.dot.append(drawn.motion);
      svg.append(drawn.dot);
    }
  }
  for (const { el, i } of drawn.segs) el.setAttribute('d', parts[i]);
  if (drawn.run) {
    drawn.run.setAttribute('d', runD);
    if (drawn.run.classList.contains('trace') && drawn.run.getTotalLength) drawn.run.style.setProperty('--len', String(Math.ceil(drawn.run.getTotalLength())));
  }
  if (drawn.ring) {
    const r = rects[plan.waitingIndex];
    const ring = { x: r.x - 6, y: r.y - 6, width: r.w + 12, height: r.h + 12 };
    for (const [key, value] of Object.entries(ring)) drawn.ring.setAttribute(key, String(value));
    const label = wrap.querySelector('.ring-label');
    if (label) { label.style.left = `${ring.x + 12}px`; label.style.top = `${ring.y - 7}px`; label.hidden = false; }
  }
  if (drawn.dot) drawn.motion.setAttribute('path', connectorPath(rects[plan.running - 1], rects[plan.running], { vertical }));
}

function pipelineNode(node, room, kind) {
  const truth = nodeTruth(node);
  return h('button', {
    type: 'button', class: `pnode run-${kind} st-${truth.status}`, 'data-node': node.id, 'data-focus-key': `node:${node.id}`, tabindex: '-1',
    title: nodeReason(node) || null,
    'aria-label': `${nodeTitle(node)}, ${truth.label}${node.human_gate ? ', human gate' : ''}. ${RUN_KIND_WORDS[kind]}. Open the details.`,
    on: { click: () => room.select({ n: node.id }) },
  },
  h('span', { class: 'pn-name' }, nodeDisplay(node), h('span', { class: 'meta', text: nodeTag(node.num) })),
  statusMark(truth.mark, truth.label, nodeReason(node)),
  node.human_gate ? h('span', { class: 'pn-gate', text: 'Human gate' }) : null);
}

const RUN_KIND_WORDS = { engine: 'Engine run', direct: 'Built outside the engine', unrecorded: 'Passed, run not recorded', not_run: 'Not run' };

function drawnPipeline(room, nodes, live) {
  const run = room.job.run;
  const plan = pipelinePlan(nodes, run);
  const phases = [...room.job.phases];
  for (const node of nodes) if (node.phase && !phases.includes(node.phase)) phases.push(node.phase);
  const wrap = h('div', { class: 'pipe' });
  const svg = svgEl('svg', { class: 'pipe-svg', 'aria-hidden': 'true', focusable: 'false' });
  const buttons = [];
  const byId = new Map();
  nodes.forEach((node, i) => byId.set(node.id, pipelineNode(node, room, plan.kinds[i])));
  wrap.append(svg);
  for (const phase of phases) {
    const lane = nodes.filter((node) => (node.phase || 'Other') === phase);
    if (!lane.length) continue;
    wrap.append(h('section', { class: 'pipe-lane', 'aria-label': `${PHASE_WORDS[String(phase).toLowerCase()] || phase} phase` },
      h('span', { class: 'label', text: PHASE_WORDS[String(phase).toLowerCase()] || phase }),
      h('div', { class: 'pipe-nodes' }, lane.map((node) => byId.get(node.id)))));
  }
  const loose = nodes.filter((node) => !node.phase);
  if (loose.length) wrap.append(h('section', { class: 'pipe-lane', 'aria-label': 'Other nodes' }, h('span', { class: 'label', text: 'Other' }), h('div', { class: 'pipe-nodes' }, loose.map((node) => byId.get(node.id)))));
  if (plan.waitingIndex >= 0) wrap.append(h('span', { class: 'ring-label', 'aria-hidden': 'true', hidden: true, text: nodes[plan.waitingIndex].status === 'decided' ? 'Engine waits here' : 'Waits here' }));
  for (const node of nodes) buttons.push(byId.get(node.id));
  const focusIndex = Math.max(0, plan.waitingIndex >= 0 ? plan.waitingIndex : plan.running >= 0 ? plan.running : 0);
  if (buttons[focusIndex]) buttons[focusIndex].tabIndex = 0;
  wrap.addEventListener('keydown', (event) => {
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    const map = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let next = null;
    if (event.key in map) next = Math.max(0, Math.min(buttons.length - 1, index + map[event.key]));
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = buttons.length - 1;
    if (next === null) return;
    event.preventDefault();
    buttons[index].tabIndex = -1;
    buttons[next].tabIndex = 0;
    buttons[next].focus();
  });
  const redraw = () => drawConnectors(wrap, svg, buttons, plan, { live });
  window.requestAnimationFrame(redraw);
  if (window.ResizeObserver) new ResizeObserver(() => window.requestAnimationFrame(redraw)).observe(wrap);
  const reached = plan.runIndex >= 0 ? nodes[plan.runIndex] : null;
  const decidedNode = nodes.find((node) => node.status === 'decided') || null;
  const decidedWait = plan.waitingIndex >= 0 && nodes[plan.waitingIndex].status === 'decided';
  const caption = reached
    ? `The engine run reached ${nodeWords(reached.id, room.job.graph)}${plan.waitingIndex >= 0 ? (decidedWait ? ' and waits there for its approval in the engine; you have decided' : ' and waits there for you') : ''}.`
    : 'The engine has not run this job; every node shown comes from builds outside it.';
  const present = new Set(plan.kinds);
  const key = h('div', { class: 'pipe-key', 'aria-hidden': 'true' },
    h('span', {}, keySwatch('engine'), RUN_KIND_WORDS.engine),
    h('span', {}, keySwatch('direct'), RUN_KIND_WORDS.direct),
    present.has('unrecorded') ? h('span', {}, keySwatch('unrecorded'), RUN_KIND_WORDS.unrecorded) : null,
    h('span', {}, keySwatch('not_run'), RUN_KIND_WORDS.not_run),
    plan.waitingIndex >= 0 ? h('span', {}, keyRing(), decidedWait ? 'Engine waits here' : 'Waits for you') : null,
    decidedNode ? h('span', {}, h('span', { class: `mark m-${decidedMark(decidedNode.gate_state && decidedNode.gate_state.decision_record)}` }, markGlyph(decidedMark(decidedNode.gate_state && decidedNode.gate_state.decision_record))), DECIDED_WORDS) : null);
  return { el: h('figure', { class: 'pipe-figure' }, key, h('p', { class: 'run-line', text: runSummaryLine(room) }), wrap), caption };
}

/* "The engine run waits at Objective and audience (N03) since Oct 21; 3 nodes recorded in it; 31 versions built
   outside it." The full run card stays in Files. */
function runSummaryLine(room) {
  const run = room.job && room.job.run;
  if (!run) return 'No engine run is recorded for this job; its versions were built outside the engine.';
  const note = room.row && room.row.run_note;
  const graph = room.job.graph || [];
  const waiting = (run.waiting_at || []).map((id) => { const node = graph.find((n) => n.id === id); return node ? nodeTitle(node) : nodeLabel(id); });
  const parts = [waiting.length
    ? `The engine run waits at ${joinList(waiting)}${note && note.since ? ` since ${fmtDay(note.since)}` : ''}`
    : `The engine run is ${String(run.label || run.status || 'recorded').replace(/^governed run:\s*/i, '')}`];
  const ran = Object.values(run.nodes || {}).filter((node) => node && (Number(node.attempts) > 0 || /PASS|FAIL|DONE/i.test(String(node.status || '')))).length;
  parts.push(`${plural(ran, 'node')} ran in it`);
  if (note && note.outside) parts.push(`${plural(note.outside, 'version')} built outside it`);
  return `${parts.join('; ')}. The full run card is in Files.`;
}

/* A key entry: the line into a node and the node's outline, drawn exactly as in the map. */
function keySwatch(kind) {
  const svg = svgEl('svg', { viewBox: '0 0 46 20', class: `key-${kind}` });
  svg.append(svgEl('path', { d: 'M1 10H15', class: SEGMENT_CLASS[{ engine: 'run', direct: 'direct', unrecorded: 'unrecorded', not_run: 'idle' }[kind]] }),
    svgEl('rect', { x: 15.5, y: 3.5, width: 29, height: 13, rx: 3, class: `key-node ${kind}` }));
  return svg;
}

function keyRing() {
  const svg = svgEl('svg', { viewBox: '0 0 46 20', class: 'key-ring' });
  svg.append(svgEl('rect', { x: 9.5, y: 4.5, width: 27, height: 11, rx: 3, class: 'key-node engine' }),
    svgEl('rect', { x: 6, y: 1, width: 34, height: 18, rx: 6, class: 'ring' }));
  return svg;
}

/* A note or reaction composed in place under its gate or check; the button toggles it and Undo stays visible. */
function inlineReaction(room, { set, target, label, buttonText, ariaLabel, row = null }) {
  const slot = h('div', { class: 'inline-slot', id: nextId('inline-slot') });
  const button = h('button', { type: 'button', class: 'btn small quiet', 'aria-label': ariaLabel, 'aria-expanded': 'false', 'aria-controls': slot.id, on: {
    click: () => {
      const open = !slot.firstChild;
      if (!open) fill(slot);
      else {
        const box = composer({ pid: room.key, version: target.version, set, target: cleanTarget(target), label, onSaved: () => refreshSoon() });
        if (row) row.hidden = false;
        fill(slot, box);
        box.focusText();
        if (!box.contains(document.activeElement)) box.querySelector('button, textarea, input, select')?.focus({ preventScroll: true });
      }
      if (row) row.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
    },
  } }, buttonText);
  return { button, slot };
}

function gatesInWords(room, board, nodes = []) {
  const version = board.version;
  const byNode = new Map(nodes.map((node) => [node.id, node]));
  for (const gate of board.human) annotateGate(gate, version, roomDecisions(room));
  const human = h('section', { class: 'card', 'aria-labelledby': 'hg-h' },
    h('h2', { id: 'hg-h', text: 'Human gates' }),
    h('ul', { class: 'gate-list' }, board.human.map((gate) => {
      const forVersion = gate.release ? version : null;
      const words = gate.release && forVersion ? `Release ${forVersion}` : (gate.decision || nodeDisplay(gate));
      const note = inlineReaction(room, {
        set: 'comment_only', target: { type: 'gate', id: gate.node, version: forVersion }, label: `${words} (${nodeTag(gate.num)})`,
        buttonText: 'Note', ariaLabel: `Note on ${words}`,
      });
      return h('li', {},
        h('div', { class: 'gate-line' }, h('span', { class: 'g-name', text: words }), h('span', { class: 'meta', text: nodeTitle(gate) })),
        gateStatusLine(gate, byNode.get(gate.node)),
        recordedDecision(gate),
        h('div', { class: 'btn-row' },
          needsOwner(gate) ? h('a', { class: 'btn decide small', href: filmHref(room.filmId, { v: forVersion || room.version, tab: 'decide', g: gate.node }) }, statusMark('needs', 'Decide')) : null,
          gate.state === 'recorded' && !gate.superseded_by ? h('a', { class: 'btn small', href: filmHref(room.filmId, { v: forVersion || room.version, tab: 'decide', g: gate.node }) }, 'Your decision') : null,
          note.button,
          h('button', { type: 'button', class: 'btn small quiet', 'data-focus-key': `hg:${gate.node}`, on: { click: () => room.select({ n: gate.node }) } }, 'Details')),
        note.slot);
    })));
  const failing = board.automatic.filter((gate) => !gate.pass).length;
  const summary = board.qa_summary;
  const automatic = h('details', { class: 'card' },
    h('summary', {}, h('span', { class: 'h3-like', text: board.automatic.length
      ? (failing ? `${failing} of ${board.automatic.length} automatic checks fail` : `All ${board.automatic.length} automatic checks pass`)
      : 'No automatic checks recorded' }),
    summary ? h('span', { class: 'meta', text: `qa-direct: ${plural(summary.findings ?? 0, 'finding')}, ${summary.blocking ?? 0} blocking` }) : null),
    board.qa_url ? h('p', {}, h('a', { href: board.qa_url, target: '_blank', rel: 'noopener', text: 'Open qa-direct.json' })) : null,
    board.automatic.length ? h('div', { class: 'scroll-x' }, h('table', {}, tableHead(['Check', 'Node', 'Result', 'Findings', '']),
      h('tbody', {}, board.automatic.flatMap((gate) => {
        const reactRow = h('tr', { class: 'inline-row', hidden: true });
        const react = inlineReaction(room, {
          set: 'gate', target: { type: 'gate', id: gate.name, version }, label: `Automatic check ${gate.name} (${nodeTag(gate.node_num)}) on ${version}`,
          buttonText: 'React', ariaLabel: `React to check ${gate.name}`, row: reactRow,
        });
        reactRow.append(h('td', { colspan: '5' }, react.slot));
        return [h('tr', {},
        h('th', { scope: 'row', class: 'mono' }, gate.name, gate.governed ? h('span', { class: 'meta', text: ' engine' }) : null),
        h('td', { text: nodeWords(gate.node, room.job.graph) }),
        h('td', {}, statusMark(gate.pass ? 'passed' : 'rework', gate.pass ? 'Pass' : 'Fail', gate.outcome || null)),
        h('td', {}, gate.findings_count || (gate.messages && gate.messages.length)
          ? h('details', {}, h('summary', { text: gate.findings_count ? plural(gate.findings_count, 'finding') : plural(gate.messages.length, 'reason') }),
            h('ol', {}, gate.findings.map((finding) => h('li', {}, readableFinding(finding)))),
            gate.messages && gate.messages.length ? h('ul', { class: 'sub' }, gate.messages.map((message) => h('li', { text: message }))) : null)
          : '0'),
        h('td', {}, h('span', { class: 'action-group' },
          react.button,
          h('button', { type: 'button', class: 'btn small quiet', 'aria-label': `Open ${nodeTag(gate.node_num)} for check ${gate.name}`, on: { click: () => room.select({ n: gate.node }) } }, 'Node')))), reactRow];
      }))))
      : null);
  const reviewers = h('details', { class: 'card' },
    h('summary', {}, h('span', { class: 'h3-like', text: `Reviewer gates (${board.reviewers.length})` })),
    board.reviewers.length ? h('ul', { class: 'gate-list' }, board.reviewers.map((review) => h('li', {},
      h('div', { class: 'gate-line' }, h('span', { class: 'g-name', text: review.label }), h('span', { class: 'meta', text: nodeWords(review.node, room.job.graph) })),
      h('div', { class: 'status-line' }, verdictChip(review.verdict_class, review.verdict || undefined, review.worst_level, review.defect_count),
        h('button', { type: 'button', class: 'btn small quiet', on: { click: () => room.select({ n: review.node }) } }, 'Details')))))
      : h('p', { class: 'empty', text: 'No reviews for this version.' }));
  return frag(human, automatic, reviewers);
}

/* A human gate's state in words, agreeing with its node: superseded release gates and nodes that passed outside the
   engine run read the same in the map, this list and the node details. */
function gateStatusLine(gate, node) {
  const truth = node ? nodeTruth(node) : null;
  if (truth && truth.status === 'unrecorded' && gate.state === 'not_reached') {
    return frag(h('div', { class: 'status-line' }, statusMark('passed', UNRECORDED_WORDS)),
      h('p', { class: 'sub', text: 'No decision waits here: the engine run has not reached this gate.' }));
  }
  const label = gateLabelText(gate);
  const pill = gate.state === 'recorded' && !gate.superseded_by
    ? statusMark(decidedMark(gate.decision_record), gateStateLabel(gate)) : statusPill(gateStatus(gate), gateStateLabel(gate));
  return frag(h('div', { class: 'status-line' }, pill, gate.since && gate.state === 'waiting' && !gate.superseded_by ? timeEl(gate.since, 'since ') : null),
    label ? h('p', { class: 'sub', text: label }) : null);
}

async function renderPipeline(room, seq) {
  const pid = room.key;
  const [nodes, board] = await Promise.all([
    getJSON(`/api/projects/${enc(pid)}/nodes?version=${enc(room.version || '')}`),
    getJSON(`/api/projects/${enc(pid)}/gates?version=${enc(room.version || '')}`),
  ]);
  if (seq !== renderSeq) return;
  for (const node of nodes) if (node.gate_state) annotateGate(node.gate_state, room.version, roomDecisions(room));
  const frame = room.frames.find((f) => f.label === room.version);
  const live = Boolean(frame && frame.mark === 'in_progress');
  const workflow = room.job.workflow || {};
  const drawn = drawnPipeline(room, nodes, live);
  fill(room.panel,
    h('section', { 'aria-labelledby': 'pipe-h' },
      h('div', { class: 'section-head' }, h('h2', { id: 'pipe-h', text: `Pipeline for ${room.version || 'this job'}` }),
        h('span', { class: 'meta', text: [workflow.name ? `workflow ${workflow.name}` : 'default workflow', plural(nodes.length, 'node'), workflow.human_gates ? plural(workflow.human_gates, 'human gate') : null].filter(Boolean).join(' \u00b7 ') })),
      workflow.note ? h('p', { class: 'notice', text: workflow.note }) : null,
      h('p', { class: 'sub', text: drawn.caption }),
      drawn.el),
    h('div', { class: 'section' }, gatesInWords(room, board, nodes)));
}

/* ------------------------------------------------------------------ Files */

function libraryFilesTable(room, version, files) {
  return h('div', { class: 'scroll-x' }, h('table', { class: 'files-table' }, tableHead(['Role', 'File', 'Size', 'Duration', 'Why here', '']),
    h('tbody', {}, files.map((file) => h('tr', {},
      h('td', {}, roleChip(file.role)),
      h('td', { class: 'break', title: file.path }, h('span', { text: file.name }),
        file.duplicate_of ? h('div', { class: 'meta', text: `same file as ${file.duplicate_of.place}` }) : null,
        file.copies ? h('div', { class: 'meta', text: `${plural(file.copies, 'other copy', 'other copies')} on this Mac` }) : null),
      h('td', { class: 'mono', text: fmtBytes(file.size) }),
      h('td', { class: 'mono', text: fmtDuration(file.duration_s) }),
      h('td', { class: 'why', text: `${file.role_why}; ${file.version_why || ''}` }),
      h('td', {}, file.playable && room
        ? h('a', { class: 'btn small quiet', href: room && room.kind === 'library' ? filmHref(room.filmId, { v: version, i: file.id, tab: 'watch' }) : filmHref(room ? room.filmId : `job-${file.job_pid}`, { v: version, tab: 'files', file: file.id }), 'aria-label': `Play ${file.name}` }, 'Play')
        : h('span', { class: 'meta', text: file.playable ? '' : 'not playable here' })))))));
}

/* A file the library found inside this job, opened here rather than in a separate library room. */
function selectedFileBlock(room, item) {
  if (item.missing) {
    return h('section', { class: 'selected-file', 'aria-labelledby': 'picked-h' },
      h('h2', { id: 'picked-h', text: 'Selected file not found' }),
      h('p', { class: 'empty', text: 'The library index no longer lists this file; a rescan may have changed it.' }));
  }
  const video = libraryPlayer(item, `Video ${item.name}`, 'metadata');
  const box = h('section', { class: 'selected-file', id: 'selected-file', 'aria-labelledby': 'picked-h', tabindex: '-1' },
    h('div', { class: 'section-head' }, h('h2', { id: 'picked-h', class: 'break', text: `Selected file: ${item.name}` }),
      h('a', { href: filmHref(room.filmId, { v: room.version, tab: 'files' }), text: 'Clear' })),
    h('p', { class: 'sub', text: `This file sits inside ${room.name}, so it opens here with the job instead of in a separate library film.` }),
    h('div', { class: 'selected-grid' }, h('div', { class: 'player compact' }, video), itemFacts(item)));
  window.requestAnimationFrame(() => { if (box.isConnected) box.scrollIntoView({ block: 'nearest' }); });
  return box;
}

async function renderFiles(room, seq) {
  if (room.kind === 'library') {
    const version = room.lib.versions.find((v) => v.label === room.version);
    const files = [version.primary, ...version.variants];
    fill(room.panel,
      h('section', { 'aria-labelledby': 'files-h' },
        h('div', { class: 'section-head' }, h('h2', { id: 'files-h', text: `Files of ${version.label}` }), h('span', { class: 'meta', text: plural(files.length, 'file') })),
        h('p', { class: 'why', text: `${version.label}: ${version.why}.` }),
        libraryFilesTable(room, version.label, files)),
      h('section', { class: 'section', 'aria-labelledby': 'grouped-h' },
        h('h2', { id: 'grouped-h', text: 'Why these files are one film' }),
        h('p', { class: 'sub', text: `Glass Box groups these ${plural(room.lib.versions_count, 'version')} as one film by where they sit (${room.lib.why}).` }),
        room.lib.root ? h('p', { class: 'path', text: room.lib.root }) : null));
    return;
  }
  const pid = room.key;
  const data = await getJSON(`/api/projects/${enc(pid)}/candidates/${enc(room.version)}`);
  if (seq !== renderSeq) return;
  let copies = null;
  if (room.row && room.row.film_id) {
    try {
      const film = await getJSON(`/api/library/films/${enc(room.row.film_id)}`);
      const match = film.versions.find((v) => v.label === room.version);
      copies = { film, match };
    } catch { copies = null; }
    if (seq !== renderSeq) return;
  }
  let selected = null;
  if (room.file) {
    try { selected = await getJSON(`/api/library/items/${enc(room.file)}`); } catch { selected = { missing: true }; }
    if (seq !== renderSeq) return;
  }
  const media = data.media || {};
  const mediaRows = [['Master', media.master], ['Poster', media.poster], ['Captions', media.vtt], ...(media.stems || []).map((stem) => [stem.key.replace(/_/g, ' '), stem.url])].filter(([, url]) => url);
  const nodes = (data.nodes || []).filter((node) => node.present);
  const job = room.job;
  const picked = selected ? selectedFileBlock(room, selected) : null;
  fill(room.panel, picked,
    h('section', { 'aria-labelledby': 'files-h' },
      h('div', { class: 'section-head' }, h('h2', { id: 'files-h', text: `Files of ${room.version}` }), h('span', { class: 'meta', text: data.source === 'governed' ? 'engine run' : data.source === 'both' ? 'engine run and candidate folder' : 'candidate folder' })),
      h('p', { class: 'sub job-folder' }, 'Job folder: ', h('span', { class: 'path', text: job.rel_path })),
      mediaRows.length ? h('ul', { class: 'lib-rows' }, mediaRows.map(([label, url]) => h('li', { class: 'lib-row' },
        h('div', { class: 'lib-row-main' }, h('span', { class: 'label', text: label }), ' ', h('a', { href: url, target: '_blank', rel: 'noopener', text: decodeURIComponent(url.split('/').pop()) })))))
        : h('p', { class: 'empty', text: 'No master or media files for this version.' }),
      nodes.length ? nodes.map((node) => h('details', { class: 'card' },
        h('summary', {}, h('h3', { class: 'h3-like', text: nodeWords(node.node, job.graph) }), h('span', { class: 'meta', text: plural((node.files || []).length, 'entry', 'entries') })),
        fileTable(node.files), node.context ? contextBlock(node.context) : null)) : null),
    h('section', { class: 'section', 'aria-labelledby': 'copies-h' },
      h('h2', { id: 'copies-h', text: 'Copies on this Mac' }),
      copies && copies.match
        ? frag(h('p', { class: 'sub', text: `The library found ${plural(1 + copies.match.variants.length, 'file')} for ${room.version} in ${copies.film.name}.` }),
          libraryFilesTable(room, room.version, [copies.match.primary, ...copies.match.variants]))
        : h('p', { class: 'empty', text: copies ? `The library index has no folder named ${room.version} for this job.` : 'The library index does not list this job.' })),
    h('section', { class: 'section', 'aria-labelledby': 'run-h' },
      h('h2', { id: 'run-h', text: 'Run, deliverables and README' }),
      runCard(job.run, job.telemetry),
      job.deliverables && job.deliverables.length
        ? h('details', { class: 'card' }, h('summary', {}, h('h3', { class: 'h3-like', text: `Deliverables (${job.deliverables.length})` })),
          h('ul', { class: 'lib-rows' }, job.deliverables.map((item) => h('li', { class: 'lib-row' },
            h('div', { class: 'lib-row-main' }, h('span', { class: 'label', text: item.key }), ' ', item.url ? h('a', { href: item.url, target: '_blank', rel: 'noopener', text: item.rel }) : h('span', { class: 'path', text: item.rel || 'missing' }))))))
        : null,
      readmeCard(job.readme_head)));
}

/* ------------------------------------------------------------------ Activity */

/* Filters lead only when there is something to filter; otherwise they wait behind a closed "Filter". */
function wrapFilters(hasRecords, filters) {
  return hasRecords ? filters : h('details', { class: 'filter-fold' }, h('summary', { text: 'Filter' }), filters);
}

async function viewActivity(target, seq) {
  let records = await getJSON('/api/reactions?include_retracted=1');
  if (seq !== renderSeq) return;
  const films = await loadFilms(false);
  const filter = { pid: '', version: '', target: '', kind: '', ack: '', retracted: '', ...(prefs.activity || {}) };
  filter.ack = { acked: 'picked_up', unacked: 'open' }[filter.ack] ?? filter.ack;
  if (!['', 'open', 'picked_up', 'resolved'].includes(filter.ack)) filter.ack = '';
  prefs.activity = filter;
  const summaryLine = h('p', { class: 'activity-summary', role: 'status' });
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Where the producer is' });
  const listBox = h('div');
  const exportBox = h('div');
  const jobs = films.rows.filter((row) => row.kind === 'job');
  const copy = h('button', { type: 'button', class: 'btn', 'data-focus-key': 'act:copy' }, icon('copy'), 'Copy for chat');
  const download = h('a', { class: 'btn', download: '', 'data-focus-key': 'act:download' }, icon('download'), 'Download .md');
  const reason = h('span', { class: 'meta' });
  const shownRecords = () => records.filter((r) => (!filter.pid || r.pid === filter.pid)
    && (!filter.version || r.version === filter.version || (r.kind === 'prefer' && (r.a === filter.version || r.b === filter.version)))
    && (!filter.target || (filter.target === 'prefer' ? r.kind === 'prefer' : (r.target && r.target.type === filter.target)))
    && (!filter.kind || r.kind === filter.kind)
    && (!filter.ack || threadState(r) === filter.ack)
    && (filter.retracted === 'show' || !r.retracted));
  const syncExport = () => {
    const scoped = records.filter((r) => !filter.pid || r.pid === filter.pid);
    const empty = !scoped.length;
    copy.disabled = empty;
    copy.title = empty ? 'Nothing to copy yet' : '';
    if (empty) { download.removeAttribute('href'); download.setAttribute('aria-disabled', 'true'); }
    else { download.setAttribute('href', `/api/reactions/export?format=md&download=1${filter.pid ? `&pid=${enc(filter.pid)}` : ''}`); download.removeAttribute('aria-disabled'); }
    reason.textContent = empty ? 'Nothing to export yet: export needs at least one note or decision.' : 'Markdown for the producer, newest first, with the pickup state.';
  };
  copy.addEventListener('click', () => copyForChat(filter.pid, exportBox));
  const drawSeg = () => {
    const scoped = records.filter((r) => !r.retracted && (!filter.pid || r.pid === filter.pid));
    fill(seg, [['', 'All'], ['open', 'Waiting for the producer'], ['picked_up', 'Picked up'], ['resolved', 'Addressed']].map(([value, label]) => h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(filter.ack === value), 'data-focus-key': `af:ack:${value || 'all'}`,
      on: { click: () => { filter.ack = value; savePrefs(); preserveFocus(draw); } },
    }, label, h('span', { class: 'count', text: ` ${value ? scoped.filter((r) => threadState(r) === value).length : scoped.length}` }))));
  };
  const draw = () => {
    const shown = shownRecords();
    summaryLine.textContent = pickupSummary(records.filter((r) => !filter.pid || r.pid === filter.pid));
    drawSeg();
    fill(listBox,
      h('p', { class: 'meta', text: `${plural(shown.length, 'note')} shown of ${records.filter((r) => !r.retracted).length}, newest first` }),
      records.length
        ? reactionList(shown, 'Nothing matches these filters.', { links: true })
        : h('div', { class: 'none-waiting' }, h('p', { class: 'deck', text: 'No notes yet. Add one from a film\u2019s Watch tab with N, or answer a finding from the Inbox.' })));
    syncExport();
  };
  const select = (key, label, options, onChange) => {
    const id = nextId(`af-${key}`);
    const control = h('select', {
      id, 'data-focus-key': `af:${key}`,
      on: { change: (event) => { filter[key] = event.target.value; if (onChange) onChange(); savePrefs(); draw(); } },
    }, options.map(([value, text]) => h('option', { value, selected: filter[key] === value, text })));
    return { box: h('span', { class: 'field' }, h('label', { for: id, class: 'label', text: label }), control), control };
  };
  const versionOptions = () => {
    const row = jobs.find((r) => r.pid === filter.pid);
    return [['', row ? 'All versions' : 'Pick a film first'], ...(row ? row.versions.map((v) => [v.label, v.label]) : [])];
  };
  const versionField = select('version', 'Version', versionOptions());
  versionField.control.disabled = !jobs.some((r) => r.pid === filter.pid);
  const resetVersions = () => {
    filter.version = '';
    fill(versionField.control, ...versionOptions().map(([value, text]) => h('option', { value, text })));
    versionField.control.disabled = !jobs.some((r) => r.pid === filter.pid);
  };
  fill(target,
    h('header', { class: 'page-head' },
      h('h1', { class: 'page-title', tabindex: '-1', text: 'Activity' }),
      summaryLine,
      h('p', { class: 'deck', text: 'Every note and decision you recorded, and where the producer is with it: waiting, picked up with a plan, or addressed in a version.' })),
    records.length ? seg : null,
    wrapFilters(records.length, h('div', { class: 'filters', role: 'group', 'aria-label': 'Filter notes and decisions' },
      select('pid', 'Film', [['', 'All films'], ...jobs.map((r) => [r.pid, r.name]), ['library', 'Library videos']], resetVersions).box,
      versionField.box,
      select('target', 'About', [['', 'Anything'], ['version', 'A version'], ['node', 'A node'], ['gate', 'A gate'], ['finding', 'A finding'], ['time', 'A moment'], ['library', 'A library video'], ['prefer', 'A preference']]).box,
      select('kind', 'Kind', [['', 'Any'], ...Object.entries(KIND_LABEL).filter(([kind]) => !['ack', 'retract', 'resolve', 'reopen'].includes(kind))]).box,
      select('retracted', 'Retracted', [['', 'Hide'], ['show', 'Show']]).box)),
    h('div', { class: 'btn-row' }, copy, download, reason),
    exportBox, listBox);
  draw();
  /* #/activity?r=<id>: the thread a Producers line or a link points at, shown even when the filters would hide it. */
  const wanted = state.route && state.route.query ? state.route.query.get('r') : null;
  if (wanted && records.some((r) => r.id === wanted)) {
    const find = () => listBox.querySelector(`.reaction[data-rid="${CSS.escape(wanted)}"]`);
    if (!find()) {
      Object.assign(filter, { pid: '', version: '', target: '', kind: '', ack: '', retracted: records.find((r) => r.id === wanted).retracted ? 'show' : '' });
      for (const control of target.querySelectorAll('.filters select')) control.value = /retracted/.test(control.id) ? filter.retracted : '';
      savePrefs();
      draw();
    }
    const item = find();
    if (item) {
      item.classList.add('is-target');
      window.requestAnimationFrame(() => { item.scrollIntoView({ block: 'center' }); item.focus({ preventScroll: true }); });
    }
  }
  state.view = {
    refresh: async (pollSeq) => {
      const next = await getJSON('/api/reactions?include_retracted=1');
      if (pollSeq !== renderSeq) return;
      if (JSON.stringify(next) !== JSON.stringify(records)) { records = next; preserveFocus(draw); }
    },
  };
}

/* ------------------------------------------------------------------ help and boot */

function openHelp() {
  const meta = state.meta || {};
  const shortcuts = [
    ['/', 'Search films and versions'],
    ['Space', 'Play or pause the film (not while typing)'],
    ['Left, Right', 'Seek 1 s; with Shift, 5 s'],
    ['N', 'Note at the current moment'],
    ['D', 'Decide: the decision that waits on this film, or the one you made'],
    ['G', 'Pipeline of this job'],
    ['C', 'Compare two versions'],
    ['Esc', 'Close the node details or a dialog'],
    ['?', 'This help'],
  ];
  const legend = h('div', { class: 'btn-row' },
    ['needs', 'decided_changes', 'decided_ok', 'in_progress', 'passed', 'rework', 'not_reviewed', 'superseded'].map((mark) => statusMark(mark)),
    LEVELS.map((level) => levelChip(level)));
  const body = frag(
    h('h3', { text: 'Decide' }),
    h('p', { text: 'The Inbox lists every decision that waits for you. Decide opens the film with its verdicts, open findings and what changed; Approve and Request changes live only there, and both ask you to confirm.' }),
    h('h3', { text: 'Watch and note' }),
    h('p', { text: 'Open any frame in Films to watch it. Press N to note the current moment; a note, agree, disagree, flag or preference can be undone for ten seconds.' }),
    h('h3', { text: 'How notes reach the producer' }),
    h('p', { text: 'Your notes and decisions wait in Glass Box until the film\u2019s producer reads them; nothing is sent anywhere. A producer, such as the Copilot chat that builds the film, runs inbox.py in the Glass Box folder: it lists your waiting notes word for word with links, then records when it picks one up, with its plan, and which version addresses it. Both appear under your note, and Reopen sends a note back when the fix did not land. Once a note has waited 15 minutes, Copy for the producer on the Inbox gives the same list as Markdown for its chat.' }),
    h('pre', { class: 'cli', text: [
      `python3 inbox.py --project <job folder>`,
      `python3 inbox.py --project <job folder> --ack <id> --reply "<plan>" --producer "<name>"`,
      `python3 inbox.py --project <job folder> --resolve <id> --version vN --reply "<what changed>"`,
      `python3 inbox.py --all`,
    ].join('\n') }),
    h('h3', { text: 'Understand why' }),
    h('p', { text: 'Pipeline draws the workflow. A solid ink outline and line mean the engine ran the node; dashed means it was built outside the engine; dotted means it passed but the engine run never recorded it; a light outline means it has not run. A ring marks the gate that waits for you. Select a node for its evidence, checks, reviews and contract.' }),
    h('h3', { text: 'Words' }),
    h('dl', { class: 'glossary' },
      h('dt', { text: 'Engine run (governed)' }), h('dd', { text: 'The VPG engine ran the node and recorded it in its run state and ledger. Its human gates wait until they are approved in the engine.' }),
      h('dt', { text: 'Built outside the engine (direct run)' }), h('dd', { text: 'The producer made the version by running steps directly, outside the governed run. Glass Box reads its files, reviews and checks from the candidate folders.' }),
      h('dt', { text: 'Check' }), h('dd', { text: 'An automatic test of a version, such as loudness, captions or stills. It passes or lists findings.' }),
      h('dt', { text: 'Review' }), h('dd', { text: 'A reviewer\u2019s verdict on a version, pass or rework, with scores and findings.' }),
      h('dt', { text: 'Gate' }), h('dd', { text: 'A point where the workflow stops. A human gate waits for your decision; automatic gates are checks.' }),
      h('dt', { text: 'Finding' }), h('dd', { text: 'A problem a review or check reports, from P1 (blocks release) to P4 (small). A finding stays open until its status says it is resolved.' }),
      h('dt', { text: 'Superseded' }), h('dd', { text: 'Replaced by a newer version. A release decision always belongs to the latest fully reviewed version.' })),
    h('h3', { text: 'Keyboard' }),
    h('table', { class: 'shortcuts' }, h('tbody', {}, shortcuts.map(([key, what]) => h('tr', {}, h('td', {}, h('kbd', { text: key })), h('td', { text: what }))))),
    h('h3', { text: 'Marks' }),
    legend,
    h('h3', { text: 'What this app changes' }),
    h('p', {}, 'Nothing in the projects. Your notes and decisions are saved on this Mac in ', h('code', { text: meta.reactions_file || 'reactions.jsonl' }),
      ', outside every project. Producers read them through this local server and answer there: picked up, then addressed in a version. The engine itself is not changed; a governed gate keeps waiting until it is approved in the engine.'));
  modal('Help', body, { wide: true });
}

function boot() {
  $('#help-btn').addEventListener('click', openHelp);
  $('#skip-link').addEventListener('click', () => main().focus());
  buildSearch();
  document.addEventListener('keydown', onKeydown);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(state.timer);
    else if (state.route) poll();
  });
  window.addEventListener('hashchange', render);
  render();
}

boot();
