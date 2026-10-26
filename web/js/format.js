/**
 * Formatting + comparison helpers (pure functions, no DOM except the JSON
 * highlighter which returns a DocumentFragment).
 */

import { STAGE_LABELS, ACTION_LABELS, SPLIT_META, MODE_META } from './eventTypes.js';
import { h } from './dom.js';

const pad2 = (n) => String(n).padStart(2, '0');

export function parseTimestamp(value) {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatTime(value) {
  const d = parseTimestamp(value);
  if (!d) return '—';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

export function formatClock(value) {
  const d = parseTimestamp(value);
  if (!d) return '—';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

export function formatDate(value) {
  const d = parseTimestamp(value);
  if (!d) return '—';
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

export function formatDateTime(value) {
  const d = parseTimestamp(value);
  if (!d) return '—';
  return `${formatDate(d)} ${formatTime(d)}`;
}

/** True when two timestamps are on different local days. */
export function isDifferentDay(a, b) {
  const da = parseTimestamp(a);
  const db = parseTimestamp(b);
  if (!da || !db) return false;
  return da.toDateString() !== db.toDateString();
}

export function relativeTime(value, now = Date.now()) {
  const d = parseTimestamp(value);
  if (!d) return '—';
  const diff = Math.max(0, now - d.getTime());
  if (diff < 5_000) return '刚刚';
  if (diff < 60_000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return formatDate(d);
}

/** mm:ss for short waits, h:mm:ss beyond an hour. */
export function formatElapsed(startValue, now = Date.now()) {
  const start = parseTimestamp(startValue);
  if (!start) return '—';
  return formatClockDuration(Math.max(0, now - start.getTime()));
}

export function formatClockDuration(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${pad2(m)}:${pad2(s)}`;
  return `${pad2(m)}:${pad2(s)}`;
}

/** Human readable Chinese duration for metrics ("2 分 13 秒"). */
export function formatDuration(ms) {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '—';
  const value = Number(ms);
  if (value < 1000) return `${Math.max(1, Math.round(value))} 毫秒`;
  const totalSeconds = value / 1000;
  if (totalSeconds < 60) {
    return `${totalSeconds >= 10 ? Math.round(totalSeconds) : totalSeconds.toFixed(1)} 秒`;
  }
  const total = Math.floor(totalSeconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} 小时 ${m} 分`;
  return `${m} 分 ${s} 秒`;
}

export function formatNumber(value, fallback = '—') {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) {
    return fallback;
  }
  return Number(value).toLocaleString('zh-CN');
}

/** Metric value is a 0..1 ratio. */
export function formatPercent(value, { digits = 1, fallback = '未评估' } = {}) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return fallback;
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

/** Delta in percentage points, e.g. "+3.2pp". */
export function formatDelta(value, { digits = 1, suffix = 'pp' } = {}) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  const sign = n > 0 ? '+' : n < 0 ? '' : '±';
  return `${sign}${(n * 100).toFixed(digits)}${suffix}`;
}

export function formatTokens(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatCost(cost) {
  if (cost === null || cost === undefined) return '未配置价格';
  if (typeof cost === 'number') return `¥${cost.toFixed(2)}`;
  if (typeof cost === 'object') {
    const amount = cost.total ?? cost.amount ?? cost.value;
    if (amount === null || amount === undefined) return '未配置价格';
    const currency = cost.currency || 'CNY';
    const value = Number(amount);
    if (Number.isNaN(value)) return '未配置价格';
    if (currency === 'CNY' || currency === 'RMB') return `¥${value.toFixed(2)}`;
    return `${currency} ${value.toFixed(2)}`;
  }
  return '未配置价格';
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return '—';
  const n = Number(bytes);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function truncate(value, max = 80) {
  const str = value === null || value === undefined ? '' : String(value);
  if (str.length <= max) return str;
  return `${str.slice(0, Math.max(1, max - 1))}…`;
}

export function shortenId(id, head = 10) {
  if (!id) return '—';
  const str = String(id);
  return str.length <= head ? str : `${str.slice(0, head)}…`;
}

export function modeLabel(mode) {
  return (MODE_META[mode] || {}).label || mode || '运行';
}

export function splitLabel(split) {
  if (!split) return '—';
  return (SPLIT_META[split] || {}).label || split;
}

export function actionLabel(action) {
  return ACTION_LABELS[action] || action || '—';
}

export function formatRoutes(routes) {
  if (!Array.isArray(routes) || routes.length === 0) return '—';
  return routes
    .map((route) => (Array.isArray(route) ? route.join(' → ') : String(route)))
    .join('  |  ');
}

export function formatToolCall(call) {
  if (!call || !call.name) return '—';
  let args = call.arguments ?? call.args ?? {};
  // Workflow-style calls wrap the business args in arguments.args.
  if (args && typeof args === 'object' && args.args && typeof args.args === 'object'
      && (args.method !== undefined || args.request !== undefined || args.script !== undefined)) {
    args = args.args;
  }
  const entries = Object.entries(args);
  if (!entries.length) return `${call.name}()`;
  const inner = entries
    .map(([key, value]) => `${key}=${formatScalar(value)}`)
    .join(', ');
  return `${call.name}(${truncate(inner, 120)})`;
}

export function formatScalar(value) {
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'object') return truncate(JSON.stringify(value), 60);
  return String(value);
}

export function summarizeQuery(query, max = 64) {
  return truncate((query || '').replace(/\s+/g, ' ').trim(), max);
}

export function pluralize(count, unit) {
  return `${formatNumber(count)} ${unit}`;
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function safeJsonStringify(value, spacing = 2) {
  const seen = new WeakSet();
  return JSON.stringify(
    value,
    (_key, val) => {
      if (typeof val === 'object' && val !== null) {
        if (seen.has(val)) return '[循环引用]';
        seen.add(val);
      }
      return val;
    },
    spacing
  );
}

export function jsonPretty(value) {
  if (typeof value === 'string') {
    const parsed = tryParseJson(value);
    if (parsed !== undefined) return JSON.stringify(parsed, null, 2);
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Returns parsed value, or ``undefined`` when the string is not valid JSON. */
export function tryParseJson(value) {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

/** Parse a string that may hold JSON; otherwise return it untouched. */
export function coerceJson(value) {
  if (typeof value !== 'string') return value;
  const trimmed = value.trim();
  if (!trimmed || !/^[[{]/.test(trimmed)) return value;
  const parsed = tryParseJson(trimmed);
  return parsed === undefined ? value : parsed;
}

const TOKEN_COUNT_KEYS = new Set(['input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'cached_tokens', 'max_tokens']);
const SENSITIVE_KEY = /(api[_-]?key|authorization|password|secret|token|cookie|credential|access[_-]?key)/i;

/** Deep-clone and redact credential-ish values before showing/copying trace data. */
export function redactValue(value, depth = 0) {
  if (depth > 12) return '[深度截断]';
  if (Array.isArray(value)) return value.map((item) => redactValue(item, depth + 1));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [key, val] of Object.entries(value)) {
      if (SENSITIVE_KEY.test(key) && val !== null && val !== undefined
          && !(TOKEN_COUNT_KEYS.has(key) && typeof val === 'number')) {
        out[key] = '***';
      } else {
        out[key] = redactValue(val, depth + 1);
      }
    }
    return out;
  }
  if (typeof value === 'string' && value.length > 4) {
    if (/^(sk-|Bearer\s)/i.test(value)) return '***';
  }
  return value;
}

/** Redact secrets from a raw string (headers, WS frames) while keeping line breaks. */
export function redactStructuredText(value) {
  const str = String(value);
  return str
    .replace(/("?(?:api[_-]?key|authorization|password|secret|token|cookie)"?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,}]+)/gi, '$1"***"')
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/g, 'Bearer ***')
    .replace(/\bsk-[A-Za-z0-9_-]{8,}/g, 'sk-***');
}

export function sanitizeFilename(name) {
  return String(name || 'download')
    .replace(/[\\/:*?"<>|]+/g, '_')
    .slice(0, 120);
}

/* ------------------------------------------------------------------ */
/* JSON path comparison                                               */
/* ------------------------------------------------------------------ */

export function jsonPathDiffs(expected, actual, base = '$') {
  const out = [];
  walkDiff(expected, actual, base, out);
  return out.sort((a, b) => a.path.localeCompare(b.path));
}

function walkDiff(expected, actual, path, out) {
  if (deepEqual(expected, actual)) return;
  const expIsObj = expected && typeof expected === 'object';
  const actIsObj = actual && typeof actual === 'object';
  if (Array.isArray(expected) && Array.isArray(actual)) {
    const len = Math.max(expected.length, actual.length);
    for (let i = 0; i < len; i += 1) {
      if (i >= expected.length) {
        out.push({ path: `${path}[${i}]`, kind: 'extra', expected: undefined, actual: actual[i] });
      } else if (i >= actual.length) {
        out.push({ path: `${path}[${i}]`, kind: 'missing', expected: expected[i], actual: undefined });
      } else {
        walkDiff(expected[i], actual[i], `${path}[${i}]`, out);
      }
    }
    return;
  }
  if (expIsObj && actIsObj && !Array.isArray(expected) && !Array.isArray(actual)) {
    const keys = new Set([...Object.keys(expected), ...Object.keys(actual)]);
    for (const key of keys) {
      const childPath = /^[A-Za-z_$][\w$]*$/.test(key) ? `${path}.${key}` : `${path}[${JSON.stringify(key)}]`;
      if (!(key in expected)) {
        out.push({ path: childPath, kind: 'extra', expected: undefined, actual: actual[key] });
      } else if (!(key in actual)) {
        out.push({ path: childPath, kind: 'missing', expected: expected[key], actual: undefined });
      } else {
        walkDiff(expected[key], actual[key], childPath, out);
      }
    }
    return;
  }
  const kind = typeOf(expected) !== typeOf(actual) ? 'type' : 'value';
  out.push({ path, kind, expected, actual });
}

export function typeOf(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value;
}

export function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return false;
  if (typeof a !== 'object') return Object.is(a, b);
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    if (a.length !== b.length) return false;
    return a.every((item, index) => deepEqual(item, b[index]));
  }
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((key) => Object.prototype.hasOwnProperty.call(b, key) && deepEqual(a[key], b[key]));
}

/* ------------------------------------------------------------------ */
/* Unified diff parsing                                               */
/* ------------------------------------------------------------------ */

/**
 * Parse unified diff text into typed lines with old/new line numbers.
 * @returns {Array<{type:'meta'|'hunk'|'add'|'del'|'ctx'|'note', text:string, oldNo:number|null, newNo:number|null}>}
 */
export function parseUnifiedDiff(diffText) {
  const lines = String(diffText || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let oldNo = null;
  let newNo = null;
  for (const raw of lines) {
    if (raw === '' && out.length === 0) continue;
    if (raw.startsWith('@@')) {
      const match = raw.match(/^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@/);
      if (match) {
        oldNo = Number(match[1]);
        newNo = Number(match[3]);
      }
      out.push({ type: 'hunk', text: raw, oldNo: null, newNo: null });
      continue;
    }
    if (raw.startsWith('+++ ') || raw.startsWith('--- ') || raw.startsWith('diff ') ||
        raw.startsWith('index ') || raw.startsWith('new file') || raw.startsWith('deleted file') ||
        raw.startsWith('similarity index')) {
      out.push({ type: 'meta', text: raw, oldNo: null, newNo: null });
      continue;
    }
    if (raw.startsWith('\\')) {
      out.push({ type: 'note', text: raw, oldNo: null, newNo: null });
      continue;
    }
    if (raw.startsWith('+')) {
      out.push({ type: 'add', text: raw.slice(1), oldNo: null, newNo });
      if (newNo !== null) newNo += 1;
      continue;
    }
    if (raw.startsWith('-')) {
      out.push({ type: 'del', text: raw.slice(1), oldNo, newNo: null });
      if (oldNo !== null) oldNo += 1;
      continue;
    }
    if (raw.startsWith(' ')) {
      out.push({ type: 'ctx', text: raw.slice(1), oldNo, newNo });
      if (oldNo !== null) oldNo += 1;
      if (newNo !== null) newNo += 1;
      continue;
    }
    out.push({ type: 'ctx', text: raw, oldNo, newNo });
    if (oldNo !== null) oldNo += 1;
    if (newNo !== null) newNo += 1;
  }
  // drop the trailing empty ctx line produced by a final newline
  while (out.length && out[out.length - 1].type === 'ctx' && out[out.length - 1].text === '') out.pop();
  return out;
}

/**
 * Pair unified diff lines into side-by-side rows.
 * @returns {Array<{kind:'hunk'|'row', text?:string, left?:object|null, right?:object|null}>}
 */
export function buildSplitDiff(diffText) {
  const parsed = parseUnifiedDiff(diffText);
  const rows = [];
  let dels = [];
  let adds = [];

  const flush = () => {
    const max = Math.max(dels.length, adds.length);
    for (let i = 0; i < max; i += 1) {
      rows.push({
        kind: 'row',
        left: dels[i] || null,
        right: adds[i] || null,
      });
    }
    dels = [];
    adds = [];
  };

  for (const line of parsed) {
    if (line.type === 'hunk') {
      flush();
      rows.push({ kind: 'hunk', text: line.text });
    } else if (line.type === 'meta') {
      flush();
      rows.push({ kind: 'hunk', text: line.text });
    } else if (line.type === 'del') {
      dels.push(line);
    } else if (line.type === 'add') {
      adds.push(line);
    } else if (line.type === 'ctx') {
      flush();
      rows.push({
        kind: 'row',
        left: { type: 'ctx', text: line.text, oldNo: line.oldNo, newNo: null },
        right: { type: 'ctx', text: line.text, oldNo: null, newNo: line.newNo },
      });
    }
  }
  flush();
  return rows;
}

/* ------------------------------------------------------------------ */
/* JSON syntax highlighting (safe: text nodes / escaped spans)         */
/* ------------------------------------------------------------------ */

const JSON_TOKEN = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

export function highlightedJson(value) {
  const textValue = typeof value === 'string' ? value : safeJsonStringify(value);
  const fragment = document.createDocumentFragment();
  let lastIndex = 0;
  let match;
  const re = new RegExp(JSON_TOKEN.source, 'g');
  while ((match = re.exec(textValue)) !== null) {
    if (match.index > lastIndex) {
      fragment.appendChild(document.createTextNode(textValue.slice(lastIndex, match.index)));
    }
    let cls = 'tok-punct';
    if (match[1]) cls = 'tok-key';
    else if (match[2]) cls = 'tok-string';
    else if (match[3]) cls = 'tok-literal';
    else if (match[4]) cls = 'tok-number';
    const span = document.createElement('span');
    span.className = cls;
    span.textContent = match[0];
    fragment.appendChild(span);
    lastIndex = re.lastIndex;
  }
  if (lastIndex < textValue.length) {
    fragment.appendChild(document.createTextNode(textValue.slice(lastIndex)));
  }
  return fragment;
}

/** Human summary used by the run list / overview. */
export function describeRunMode(mode, parentRunId) {
  const label = modeLabel(mode);
  return parentRunId ? `${label} · 父运行 ${shortenId(parentRunId, 8)}` : label;
}

export function stageText(stage) {
  return stage ? STAGE_LABELS[stage] || stage : '整轮';
}

export function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

export function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null);
}

/** Build a tiny "R 80.0% · A 75.0%" summary from a run metrics object. */
export function metricsSummary(metrics) {
  if (!metrics) return '无指标';
  const parts = [];
  for (const [key, label] of [['routing', 'R'], ['tool_args', 'A'], ['joint', 'J']]) {
    const block = metrics[key];
    if (!block) continue;
    if (block.value === null || block.value === undefined) continue;
    parts.push(`${label} ${formatPercent(block.value)}`);
  }
  return parts.length ? parts.join(' · ') : '未评估';
}

/** Append a DOM node for a labelled value (used by the JSON viewer fallbacks). */
export function labelledValue(label, value) {
  return h('div.kv', null, h('span.kv-key', { text: label }), h('span.kv-value', { text: value }));
}
