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

/* ---------------- plain-language case results ---------------- */

const CASE_ERROR_LABELS = {
  format_error: '模型输出格式错误',
  refused: '模型拒绝回答',
  steps_exhausted: '步数用尽仍未完成',
  context_overflow: '上下文超长',
  truncated: '模型输出被截断',
  protocol_error: '模型输出不符合协议',
  fixture_miss: '缺少模拟工具返回',
  parse_failed: '模型输出无法解析',
  auth_failed: '模型鉴权失败',
  network_error: '网络错误',
  timeout: '模型调用超时',
  rate_limited: '模型调用被限流',
  platform_error: '模型平台错误',
  stream_interrupted: '流式输出中断',
  judge_failed: '评分模型调用失败',
  judge_parse_failed: '评分模型输出无法解析',
  cancelled: '已取消',
};

/** Failure group shown in "主要失败原因"; order is the display order. */
export const FAILURE_GROUPS = {
  action: '动作不对（该调用工具 / 该澄清 / 不该动作）',
  route: '技能选错',
  tool: '工具调用不对',
  arg: '参数不对',
  error: '执行出错',
};

const FAILURE_GROUPS_ORDER = { action: 1, route: 2, tool: 3, arg: 4, error: 5 };

const DIAG_GROUP = {
  wrong_route: 'route', missing_route: 'route', extra_route: 'route',
  action_mismatch: 'action', clarify_mismatch: 'action', unexpected_call: 'action',
  missing_tool: 'tool', extra_tool: 'tool', wrong_tool: 'tool',
  missing_arg: 'arg', extra_arg: 'arg', value_error: 'arg', type_error: 'arg',
  fixture_miss: 'error', protocol_error: 'error',
};

function plainField(path) {
  const text = String(path || '').replace(/^\$\.?/, '');
  return text || '参数';
}

function plainValue(value) {
  if (value === null || value === undefined || value === '') return '空';
  if (typeof value === 'string') return truncate(value, 40);
  if (Array.isArray(value)) {
    if (!value.length) return '无';
    return truncate(value.map((item) => (Array.isArray(item) ? item.join(' → ') : plainValue(item))).join('、'), 60);
  }
  if (typeof value === 'object') return truncate(JSON.stringify(value), 60);
  return String(value);
}

/** One scorer diagnostic → `{ group, text }` in plain Chinese. */
export function describeDiagnostic(diag) {
  const d = diag || {};
  const exp = plainValue(d.expected_value);
  const act = plainValue(d.actual_value);
  const field = plainField(d.field_path);
  const routeField = d.field_path === 'selected_skills';
  let text;
  switch (d.category) {
    case 'wrong_route': text = `选错技能：选了 ${act}，应为 ${exp}`; break;
    case 'missing_route': text = `没有选中技能 ${plainValue(d.expected_value ?? d.skill_id)}`; break;
    case 'extra_route': text = `多选了技能 ${plainValue(d.actual_value ?? d.skill_id)}`; break;
    case 'wrong_order': text = routeField ? `技能顺序不对：${act}，应为 ${exp}` : `工具调用顺序不对：${act}，应为 ${exp}`; break;
    case 'action_mismatch': text = `应该${ACTION_LABELS[d.expected_value] || exp}，实际${ACTION_LABELS[d.actual_value] || act}`; break;
    case 'clarify_mismatch': text = `澄清的字段不对：问了 ${act}，应问 ${exp}`; break;
    case 'unexpected_call': text = `不该调用工具 ${act}`; break;
    case 'missing_tool': text = `没有调用工具 ${exp}`; break;
    case 'extra_tool': text = `多调用了工具 ${act}`; break;
    case 'wrong_tool': text = `工具用错：调了 ${act}，应为 ${exp}`; break;
    case 'missing_arg': text = d.expected_value === 'required' ? `缺少必填参数 ${field}` : `缺少参数 ${field}（应为 ${exp}）`; break;
    case 'extra_arg': text = `多了参数 ${field}`; break;
    case 'value_error': text = `参数 ${field} 不对：给了 ${act}，应为 ${exp}`; break;
    case 'type_error': text = `参数 ${field} 类型不对：给了 ${act}，应为 ${exp}`; break;
    case 'fixture_miss': text = `工具 ${d.tool || ''} 缺少模拟返回`.replace('  ', ' '); break;
    case 'protocol_error': text = `模型输出不符合协议${d.actual_value ? `（${act}）` : ''}`; break;
    default: text = `${d.category || '不匹配'}${d.field_path ? `：${field}` : ''}`;
  }
  if (d.note && d.category === 'value_error') text += `（${truncate(d.note, 60)}）`;
  return { group: DIAG_GROUP[d.category] || 'error', text };
}

/**
 * Why one side (baseline / candidate run of a case) failed, as plain sentences.
 * Returns `{ passed, reasons: [{group, text}] }`; `reasons` is empty on a pass.
 */
export function explainCaseSide(side) {
  if (!side) return null;
  if (side.passed) return { passed: true, reasons: [] };
  const reasons = [];
  const seen = new Set();
  const diags = side.diagnostics || [];
  const missing = diags.filter((diag) => diag.category === 'missing_arg');
  for (const diag of diags) {
    let item;
    if (diag.category === 'missing_arg' && missing.length > 1) {
      if (diag !== missing[0]) continue;
      const names = missing.map((one) => (one.expected_value === 'required' || one.expected_value === undefined || one.expected_value === null
        ? plainField(one.field_path)
        : `${plainField(one.field_path)}（应为 ${plainValue(one.expected_value)}）`));
      item = { group: 'arg', text: `缺少参数 ${names.join('、')}` };
    } else {
      item = describeDiagnostic(diag);
    }
    if (seen.has(item.text)) continue;
    seen.add(item.text);
    reasons.push(item);
  }
  // Most decisive first: wrong action explains everything after it.
  const order = Object.keys(FAILURE_GROUPS_ORDER);
  reasons.sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group));
  if (!reasons.length) {
    const code = side.error_code;
    const label = CASE_ERROR_LABELS[code] || side.reason || code;
    if (label) reasons.push({ group: 'error', text: side.reason && CASE_ERROR_LABELS[code] ? `${CASE_ERROR_LABELS[code]}：${truncate(side.reason, 60)}` : label });
    else if (side.routing && side.routing.passed === false) reasons.push({ group: 'route', text: side.routing.reason || '技能选择不对' });
    else if (side.tool_args && side.tool_args.passed === false) reasons.push({ group: 'arg', text: side.tool_args.reason || '工具调用或参数不对' });
    else reasons.push({ group: 'error', text: '与预期不一致' });
  }
  return { passed: false, reasons };
}
