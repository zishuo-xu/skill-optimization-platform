/**
 * Shared case display pieces: verdict + reasons, expected / actual columns,
 * judge verdicts, timeline, and the single-case trial ("试跑") drawer.
 * Used by the run details drawer and the dataset page.
 */

import { h } from '../dom.js';
import {
  button,
  chip,
  statusBadge,
  openDrawer,
  loadingState,
  errorState,
  jsonViewer,
  codeBlock,
} from '../components.js';
import {
  actionLabel,
  compareRuleSummary,
  explainCaseSide,
  formatDateTime,
  formatDuration,
  formatNumber,
  safeJsonStringify,
  splitLabel,
  truncate,
} from '../format.js';
import { eventLabel, STAGE_LABELS } from '../eventTypes.js';
import { projectExecutionTrace, groupExecutionTrace } from '../executionTrace.js';

export function sideBadge(side) {
  if (!side) return h('span.muted.xsmall', { text: '—' });
  if (side.status === undefined && side.passed === undefined) return chip('未评分', 'neutral');
  if (side.status === null && side.passed === null) return chip('未评分', 'neutral');
  return statusBadge(side.status || (side.passed ? 'pass' : 'fail'), 'case');
}

// A result is associated only when its tool and full arguments match. Consume each
// record once so repeated calls with identical arguments retain their order.
function sameJson(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const ak = Object.keys(a).sort();
  const bk = Object.keys(b).sort();
  return ak.length === bk.length && ak.every((key, i) => key === bk[i] && sameJson(a[key], b[key]));
}

export function matchToolResults(calls = [], results = []) {
  const remaining = results.map((result, index) => ({ result, index }));
  const matched = calls.map((call) => {
    // Missing records make identical calls indistinguishable; do not guess.
    const sameCall = (other) => other && call && other.name === call.name && Object.hasOwn(other, 'arguments') &&
      Object.hasOwn(call, 'arguments') && sameJson(other.arguments, call.arguments);
    const callCount = calls.filter(sameCall).length;
    const resultCount = results.filter(sameCall).length;
    if (callCount > 1 && resultCount < callCount) return null;
    const index = remaining.findIndex(({ result }) => result && result.name === call?.name &&
      Object.hasOwn(result, 'arguments') && Object.hasOwn(call, 'arguments') && sameJson(result.arguments, call.arguments));
    return index < 0 ? null : remaining.splice(index, 1)[0].result;
  });
  return { matched, unmatched: remaining.map(({ result }) => result) };
}

function resultBlock(result) {
  if (result?.error === 'fixture_miss') return h('div.small', { text: '模拟返回未匹配：已尝试调用，未取得工具返回。' });
  if (!result || !Object.hasOwn(result, 'response')) return h('div.xsmall.muted', { text: '未记录工具返回' });
  return h('details.case-more', null,
    h('summary', { text: '工具返回（执行记录，不计入参数评分）' }),
    h('pre.code-block__pre', { text: safeJsonStringify(result.response, 2), style: { maxHeight: '200px', whiteSpace: 'pre-wrap' } }));
}

function callBlock(call, { expected = false, result = null } = {}) {
  if (!call) return h('p.muted.small', { text: '无业务工具调用' });
  const args = call && typeof call.arguments === 'object' && call.arguments ? call.arguments : {};
  const method = args && typeof args === 'object' ? args.method : null;
  const path = expected ? call.arguments_path : null;
  return h(
    expected ? 'div.expected-call' : 'div.actual-call',
    null,
    h(
      'div.row',
      { style: { gap: '6px', alignItems: 'center' } },
      h('span.mono.xsmall', { text: `${call.name || '—'}${method ? ` · ${method}` : ''}` }),
      path ? chip(`只比较 ${path}`, 'neutral') : null
    ),
    h('span.xsmall.muted', { text: '调用参数（按评分规则比较）' }),
    h(`pre.code-block__pre.${expected ? 'expected-call__args' : 'actual-call__args'}`, {
      text: safeJsonStringify(args, 2),
      style: { maxHeight: '200px', whiteSpace: 'pre-wrap', margin: '6px 0 0' },
    }),
    expected ? null : resultBlock(result)
  );
}

/** One column of the expected / actual comparison. */
export function compareColumn({ label, badge = null, routes, action, calls, missing = null, footer = null, expected = false }) {
  return h(
    `div.compare-column${expected ? ' compare-column--expected' : ''}`,
    null,
    h('div.compare-column__head', null, h('span', { text: label }), badge),
    h(
      'div.compare-column__body',
      null,
      h('div', null, h('span.xsmall.muted', { text: '技能' }), h('div', null, routes.length ? routes.map((route) => h('span.route-pill', { text: route })) : h('span.small.muted', { text: '无' }))),
      h('div', null, h('span.xsmall.muted', { text: '动作' }), h('div.small', { text: actionLabel(action) })),
      missing && missing.length ? h('div', null, h('span.xsmall.muted', { text: '应澄清的字段' }), h('div.small', { text: missing.join('、') })) : null,
      h(
        'div',
        null,
        h('span.xsmall.muted', { text: '工具调用' }),
        calls.length ? h('div.stack.stack--sm', null, ...calls) : h('div.small.muted', { text: '无' })
      ),
      footer
    )
  );
}

export function expectedColumn(expected) {
  const exp = expected || {};
  const routes = (exp.routes || []).map((route) => (Array.isArray(route) ? route.join(' → ') : String(route)));
  return compareColumn({
    label: '预期（数据集）',
    routes: routes.length > 1 ? routes.map((route, index) => (index ? `或 ${route}` : route)) : routes,
    action: exp.action,
    calls: (exp.tool_calls || []).map((call) => callBlock(call, { expected: true })),
    missing: exp.action === 'clarify' ? exp.missing_fields : null,
    expected: true,
  });
}

export function actualColumn(side, label) {
  if (!side) {
    return h(
      'div.compare-column',
      null,
      h('div.compare-column__head', null, h('span', { text: label })),
      h('div.compare-column__body', null, h('p.muted.small', { text: '本次没有运行' }))
    );
  }
  const calls = Array.isArray(side.tool_calls) ? side.tool_calls : [];
  const { matched, unmatched } = matchToolResults(calls, Array.isArray(side.tool_results) ? side.tool_results : []);
  const tokens = side.usage?.total_tokens ?? (side.usage?.input_tokens ? side.usage.input_tokens + (side.usage.output_tokens || 0) : undefined);
  return compareColumn({
    label,
    badge: sideBadge(side),
    routes: side.selected_skills || [],
    action: side.action,
    calls: calls.map((call, index) => callBlock(call, { result: matched[index] })),
    missing: side.action === 'clarify' ? side.missing_fields : null,
    footer: h('div.stack.stack--sm', null,
      unmatched.length ? h('div', null,
        h('span.xsmall.muted', { text: '未关联的工具返回记录' }),
        ...unmatched.map((result) => h('div.actual-call', null,
          h('span.mono.xsmall', { text: result?.name || '—' }),
          h('details.case-more', null, h('summary', { text: '记录参数' }),
            h('pre.code-block__pre', { text: safeJsonStringify(result?.arguments, 2) })),
          resultBlock(result)))) : null,
      h('div', null, h('span.xsmall.muted', { text: '最终回复（展示，不计入参数评分）' }),
        h('div.small', { text: typeof side.final_response === 'string' && side.final_response ? side.final_response : '未记录最终回复', style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } })),
      side.display_note ? h('div.xsmall.muted', { text: side.display_note }) : null,
      h('div.xsmall.muted', { text: `耗时 ${formatDuration(side.duration_ms)} · Token ${formatNumber(tokens)}` })),
  });
}

/** Reasons with the rule each one was judged by ("依据"). */
export function reasonList(reasons) {
  return h(
    'ul.case-verdict__reasons',
    null,
    ...reasons.map((reason) =>
      h('li', null, h('span', { text: reason.text }), reason.basis ? h('span.case-verdict__basis', { text: `依据：${reason.basis}` }) : null)
    )
  );
}

/** One line naming the comparison rules a scored side was judged with. */
function ruleLine(sides) {
  const scored = sides.map(([, side]) => side).find((side) => side && side.tool_args?.compare);
  const summary = scored ? compareRuleSummary(scored.tool_args.compare) : '';
  if (!summary) return null;
  return h('p.case-verdict__rules', null, h('span', { text: `评分规则：${summary}` }), h('span.muted', { text: '（在「执行环境 → 评分规则」中修改）' }));
}

/** Plain-language verdict per side: `[[label, side], ...]`. */
export function verdictRows(sides) {
  return h(
    'div.case-verdict',
    null,
    ...sides.map(([label, side]) => {
      const scored = side && !(side.passed === null && side.status === null);
      const explained = scored ? explainCaseSide(side) : null;
      let detail;
      if (!side) detail = h('span.small.muted', { text: '' });
      else if (!scored) detail = h('span.small.muted', { text: '自由输入没有预期，只展示模型的实际行为。' });
      else if (explained && !explained.passed) detail = reasonList(explained.reasons);
      else detail = h('span.small.muted', { text: '与预期一致' });
      const protocol = side?.finish_protocol;
      if (protocol === 'missing' || protocol === 'missing_undetermined') {
        const message = protocol === 'missing_undetermined'
          ? '结束协议：未调用 finish，缺少明确动作证据，无法判定是否完成'
          : '结束协议：未调用 finish；业务结果单独评分';
        detail = h('div', null, detail, h('details.case-more', null,
          h('summary', { text: '结束协议详情' }),
          h('span.case-verdict__basis', { text: message })));
      }
      return h(
        'div.case-verdict__row',
        null,
        h('span.case-verdict__label', { text: label }),
        side ? sideBadge(side) : h('span.muted.small', { text: '未运行' }),
        detail
      );
    }),
    sides.some(([, side]) => side && side.passed === false) ? ruleLine(sides) : null
  );
}

/** Judge-model verdicts on argument values: `[[label, side], ...]`. */
export function judgmentsBlock(sides) {
  const withJudgments = sides.filter(([, side]) => side && (side.judgments || []).length);
  if (!withJudgments.length) return null;
  const text = (value) => (typeof value === 'string' ? value : safeJsonStringify(value, 0));
  return h(
    'div.case-section',
    null,
    h('div.case-section__title', null, h('span', { text: '评分模型判定' })),
    ...withJudgments.map(([label, side]) =>
      h(
        'div.stack.stack--sm',
        null,
        withJudgments.length > 1 ? h('div.xsmall.muted', { text: label }) : null,
        ...side.judgments.map((item) =>
          h(
            'div.param-diff',
            null,
            h('div.param-diff__path', null, h('div', { text: item.path || '$' }), statusBadge(item.passed ? 'pass' : 'fail', 'case')),
            h('div.param-diff__cell', null, h('span.param-diff__label', { text: '预期 → 实际' }), h('span.param-diff__value', { text: `${text(item.expected)} → ${text(item.actual)}` })),
            h('div.param-diff__cell', null, h('span.param-diff__label', { text: '理由' }), h('span.param-diff__value', { text: item.reason || '—' }))
          )
        )
      )
    )
  );
}

export function timelineBlock(events) {
  if (!events.length) return h('p.muted.small', { text: '没有该案例的详细事件。' });
  return h(
    'div.timeline',
    null,
    ...events.map((event) =>
      h(
        `div.timeline__item timeline__item--${event.level || 'info'}`,
        null,
        h('span.timeline__time', { text: event.ts ? formatDateTime(event.ts).slice(11) : '' }),
        h('span.timeline__title', { text: `${event.payload?.purpose === 'judge' ? '评分模型 · ' : ''}${event.payload?.title || eventLabel(event.type)}` }),
        h(
          'div.timeline__detail',
          null,
          [
            event.payload?.skill_id,
            event.payload?.tool,
            event.payload?.path || event.payload?.skill_path,
            event.payload?.source === 'select_skill' ? '选择时自动加载' : null,
            event.payload?.cached ? '已缓存' : null,
            event.payload?.finish_reason,
            event.payload?.passed !== undefined ? (event.payload.passed ? '通过' : '未通过') : null,
            event.payload?.issue?.message || event.payload?.message,
          ]
            .filter(Boolean)
            .join(' · ')
        ),
        (() => {
          const payload = event.payload || {};
          const detail = Object.fromEntries(['arguments', 'result', 'response', 'output'].filter((key) => payload[key] !== undefined).map((key) => [key, payload[key]]));
          return !Object.keys(detail).length ? null : h('details.timeline__payload', { 'data-trace-key': `raw:${event.seq ?? `${event.call_id}:${event.type}`}` },
            h('summary', { text: '查看参数 / 返回' }),
            h('pre.code-block__pre', { text: safeJsonStringify(detail, 2), style: { maxHeight: '180px', whiteSpace: 'pre-wrap' } }));
        })()
      )
    )
  );
}

export function timelineDetails(events, { open = false } = {}) {
  return h('details.case-more', open ? { open: true } : null, h('summary', { text: `执行过程（${events.length} 条事件）` }), timelineBlock(events));
}

export function scopedExecutionTrace(events, scopes = []) {
  const groups = groupExecutionTrace(events);
  if (!groups.length) return executionTraceView([], { complete: true });
  return h('div.execution-trace-scopes', null, ...groups.map(({scope, events: records}) => {
    const [variant, stage, repeat] = scope;
    const label = scopes.find(item => item.variant === variant)?.label || ({baseline:'基线执行链路',candidate:'候选执行链路'}[variant]) || '执行链路';
    const title = [label, stage ? (STAGE_LABELS[stage] || stage) : '', repeat !== '' ? `第 ${repeat} 次` : ''].filter(Boolean).join(' · ');
    return h('section.trace-scope', null, h('h3.trace-group__title', {text:title}), executionTraceView(records, {complete:true}));
  }));
}

function traceStatus(status) {
  return { loaded: '已加载', pending: '加载中', unconfirmed: '未确认', running: '执行中', returned: '已返回', failed: '失败' }[status] || '未确认';
}

export function executionTraceView(events, { complete = false } = {}) {
  const trace = projectExecutionTrace(events, { complete });
  const skillRows = trace.skills.length ? trace.skills.map((item) => h('div.trace-step', null,
    h('span.trace-step__title', { text: `加载子技能 · ${item.skillId || '—'}` }),
    h('span.trace-step__status', { text: traceStatus(item.status) }),
    item.path ? h('span.trace-step__detail', { text: `${item.path}${item.cached ? ' · 已缓存' : ''}` }) : null
  )) : [h('div.trace-empty', { text: '尚未观察到子技能加载' })];
  const callRows = trace.calls.length ? trace.calls.map((item) => h('div.trace-step', null,
    h('span.trace-step__title', { text: `调用工具 · ${item.name}` }),
    h('span.trace-step__status', { text: traceStatus(item.status) }),
    item.error ? h('span.trace-step__detail', { text: item.error }) : null,
    (item.arguments !== undefined || item.response !== undefined) ? h('details.trace-step__payload', { 'data-trace-key': `tool:${item.key}` },
      h('summary', { text: '查看工具参数 / 返回' }),
      h('pre.code-block__pre', { text: safeJsonStringify({ arguments: item.arguments, response: item.response }, 2), style: { maxHeight: '180px', whiteSpace: 'pre-wrap' } })) : null
  )) : [h('div.trace-empty', { text: '尚未观察到业务工具调用' })];
  return h('div.execution-trace', null,
    h('section.trace-group', null, h('h3.trace-group__title', { text: '加载子技能' }), ...skillRows),
    h('section.trace-group', null, h('h3.trace-group__title', { text: '调用工具' }), ...callRows),
    h('details.case-more.trace-internal', { 'data-trace-key': 'internal' }, h('summary', { text: `内部协议与原始事件（${trace.internal.length} 条）` }), timelineBlock(trace.internal))
  );
}

/* ---------------- single-case trial ---------------- */

function trialResultView(data) {
  const side = data.result || {};
  const errorText = side.error_message && side.error_code ? `${side.error_code}：${side.error_message}` : side.error_message;
  return h(
    'div.case-detail',
    null,
    h(
      'div.case-section',
      null,
      h('div.case-detail__query', { text: data.query || '—' }),
      h(
        'div.row',
        { style: { marginTop: '8px', gap: '6px' } },
        data.split ? chip(splitLabel(data.split), 'neutral') : chip('自由输入', 'neutral'),
        data.critical ? chip('关键案例', 'warning', { iconName: 'alert' }) : null,
        chip('试跑', 'accent')
      ),
      verdictRows([['结果', side]]),
      errorText ? h('p.small.trial-error', { text: truncate(errorText, 400) }) : null
    ),
    executionTraceView(data.events || [], { complete: true }),
    h(
      'div.case-section',
      null,
      h('div.case-section__title', null, h('span', { text: data.scored ? '预期与实际' : '实际行为' })),
      h('div.compare-columns', null, data.scored ? expectedColumn(data.expected) : null, actualColumn(side, '实际'))
    ),
    judgmentsBlock([['结果', side]]),
    side.raw_output && side.action !== 'tool_call'
      ? h('details.case-more', null, h('summary', { text: '模型原始输出' }), codeBlock(side.raw_output, { wrap: true, maxHeight: '260px' }))
      : null,
    data.expected ? jsonViewer(data.expected, { label: '预期结果 JSON', maxHeight: '240px', redact: false }) : null
  );
}

function createTrialLiveView(events, startedAt) {
  const elapsed = Math.max(0, Date.now() - startedAt);
  const latest = events[events.length - 1];
  const details = h('div.trial-live__trace', null, executionTraceView(events));
  const liveTitle = (items) => {
    const trace = projectExecutionTrace(items);
    const lastCall = trace.calls[trace.calls.length - 1];
    if (lastCall?.status === 'running') return lastCall.name ? `调用工具 · ${lastCall.name}` : '调用工具';
    if (items.some(item => item.type === 'scoring_started')) return '正在核对结果';
    if (lastCall?.status === 'returned' || lastCall?.status === 'failed') return '等待后续执行结果';
    const lastSkill = trace.skills[trace.skills.length - 1];
    if (lastSkill?.status === 'pending') return `加载子技能 · ${lastSkill.skillId || '—'}`;
    if (lastSkill) return '子技能已加载，等待后续执行';
    return '准备执行';
  };
  const root = h('div.trial-live', null,
    h('div.row', { style: { justifyContent: 'space-between', alignItems: 'center' } },
      h('strong.trial-live__title', { text: liveTitle(events) }),
      h('span.xsmall.muted.trial-live__elapsed', { text: `已等待 ${formatDuration(elapsed)}` })),
    h('p.xsmall.muted.trial-live__message', { text: latest?.payload?.message || '按实际事件更新技能加载和工具调用；完整记录可在详情中展开。' }), details
  );
  root._update = (nextEvents, nextStartedAt) => {
    const nextLatest = nextEvents[nextEvents.length - 1];
    root.querySelector('.trial-live__title').textContent = liveTitle(nextEvents);
    root.querySelector('.trial-live__elapsed').textContent = `已等待 ${formatDuration(Math.max(0, Date.now() - nextStartedAt))}`;
    root.querySelector('.trial-live__message').textContent = nextLatest?.payload?.message || '按实际事件更新技能加载和工具调用；完整记录可在详情中展开。';
    if (nextEvents === events) return;
    const nextTrace = executionTraceView(nextEvents);
    const oldTrace = root.querySelector('.trial-live__trace .execution-trace');
    if (oldTrace) {
      const openDetails = new Map([...oldTrace.querySelectorAll('details')].map((item) => [item.dataset.traceKey || '', {open:item.open, scroll:item.querySelector('pre')?.scrollTop || 0}]));
      oldTrace.replaceWith(nextTrace);
      [...nextTrace.querySelectorAll('details')].forEach((item) => {
        const state = openDetails.get(item.dataset.traceKey || '');
        if (state) { item.open = state.open; const pre = item.querySelector('pre'); if (pre) pre.scrollTop = state.scroll; }
      });
    }
    events = nextEvents;
  };
  return root;
}

/**
 * Open a drawer that runs one trial and shows the result.
 * `runTrial` returns the API promise; `title`/`context` describe what runs.
 */
export function openTrialDrawer({ title, context = '', runTrial }) {
  let busy = false;
  let drawer = null;
  let controller = null;
  let startedAt = 0;
  let liveEvents = [];
  let liveTimer = null;
  let liveRoot = null;
  let closed = false;
  const footer = () =>
    h(
      'div.btn-row.btn-row--between',
      null,
      h('span.xsmall.muted', { text: '试跑会真实调用模型，结果不写入运行记录，也不影响指标。' }),
      h(
        'div.btn-row',
        null,
        button({ label: busy ? '正在试跑…' : '再跑一次', variant: 'secondary', iconName: 'play', disabled: busy, onClick: start }),
        button({ label: '关闭', variant: 'ghost', onClick: () => drawer.close() })
      )
    );

  async function start() {
    if (busy || closed) return;
    busy = true;
    controller = new AbortController();
    startedAt = Date.now();
    liveEvents = [];
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(() => {
      if (busy && drawer && !closed && liveRoot?._update) liveRoot._update(liveEvents, startedAt);
    }, 1000);
    drawer.setFooter(footer());
    liveRoot = createTrialLiveView(liveEvents, startedAt);
    drawer.setBody(liveRoot);
    try {
      const data = await runTrial({
        signal: controller.signal,
        onEvent: (event) => {
          if (!event || liveEvents.some((item) => item.seq === event.seq)) return;
          liveEvents = [...liveEvents, event].slice(-200);
          if (drawer && busy && !closed && liveRoot?._update) liveRoot._update(liveEvents, startedAt);
        },
      });
      const version = data.skill_version || {};
      const env = data.environment || {};
      const head = h(
        'p.xsmall.muted.trial-meta',
        { text: `Skill ${version.status === 'candidate' ? '候选版本' : '版本'} ${version.id || '—'}${version.message ? `（${truncate(version.message, 40)}）` : ''} · 执行环境 v${env.version_seq ?? '—'}${env.status === 'draft' ? '（草稿）' : ''}` }
      );
      if (!closed) drawer.setBody(h('div', null, head, trialResultView(data)));
    } catch (error) {
      if (error?.code !== 'aborted' && !closed) {
        const failure = errorState(error, { title: '试跑失败', retry: start });
        drawer.setBody(h('div', null, liveRoot || null, failure));
      }
    } finally {
      busy = false;
      if (liveTimer) clearInterval(liveTimer);
      liveTimer = null;
      controller = null;
      if (!closed) drawer.setFooter(footer());
    }
  }

  drawer = openDrawer({
    title,
    subtitle: context,
    size: 'xl',
    body: loadingState('正在试跑…'),
    footer: h('div'),
    onClose: () => {
      closed = true;
      if (controller) controller.abort();
      if (liveTimer) clearInterval(liveTimer);
      liveTimer = null;
    },
  });
  start();
  return drawer;
}
