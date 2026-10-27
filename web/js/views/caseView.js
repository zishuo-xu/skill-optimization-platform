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
import { eventLabel } from '../eventTypes.js';

export function sideBadge(side) {
  if (!side) return h('span.muted.xsmall', { text: '—' });
  if (side.status === undefined && side.passed === undefined) return chip('未评分', 'neutral');
  if (side.status === null && side.passed === null) return chip('未评分', 'neutral');
  return statusBadge(side.status || (side.passed ? 'pass' : 'fail'), 'case');
}

function callBlock(call, { expected = false } = {}) {
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
    h(`pre.code-block__pre.${expected ? 'expected-call__args' : 'actual-call__args'}`, {
      text: safeJsonStringify(args, 2),
      style: { maxHeight: '200px', whiteSpace: 'pre-wrap', margin: '6px 0 0' },
    })
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
  const tokens = side.usage?.total_tokens ?? (side.usage?.input_tokens ? side.usage.input_tokens + (side.usage.output_tokens || 0) : undefined);
  return compareColumn({
    label,
    badge: sideBadge(side),
    routes: side.selected_skills || [],
    action: side.action,
    calls: (side.tool_calls || []).map((call) => callBlock(call)),
    missing: side.action === 'clarify' ? side.missing_fields : null,
    footer: h('div.xsmall.muted', { text: `耗时 ${formatDuration(side.duration_ms)} · Token ${formatNumber(tokens)}` }),
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
        detail = h('div', null, detail, h('span.case-verdict__basis', { text: message }));
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
    ...events.slice(-60).map((event) =>
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
            event.payload?.finish_reason,
            event.payload?.passed !== undefined ? (event.payload.passed ? '通过' : '未通过') : null,
            event.payload?.issue?.message || event.payload?.message,
          ]
            .filter(Boolean)
            .join(' · ')
        )
      )
    )
  );
}

export function timelineDetails(events) {
  return h('details.case-more', null, h('summary', { text: `执行过程（${events.length} 条事件）` }), timelineBlock(events));
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
    timelineDetails(data.events || []),
    data.expected ? jsonViewer(data.expected, { label: '预期结果 JSON', maxHeight: '240px', redact: false }) : null
  );
}

/**
 * Open a drawer that runs one trial and shows the result.
 * `runTrial` returns the API promise; `title`/`context` describe what runs.
 */
export function openTrialDrawer({ title, context = '', runTrial }) {
  let busy = false;
  let drawer = null;
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
    if (busy) return;
    busy = true;
    drawer.setFooter(footer());
    drawer.setBody(loadingState('正在试跑：执行这个案例并评分…'));
    try {
      const data = await runTrial();
      const version = data.skill_version || {};
      const env = data.environment || {};
      const head = h(
        'p.xsmall.muted.trial-meta',
        { text: `Skill ${version.status === 'candidate' ? '候选版本' : '版本'} ${version.id || '—'}${version.message ? `（${truncate(version.message, 40)}）` : ''} · 执行环境 v${env.version_seq ?? '—'}${env.status === 'draft' ? '（草稿）' : ''}` }
      );
      drawer.setBody(h('div', null, head, trialResultView(data)));
    } catch (error) {
      drawer.setBody(errorState(error, { title: '试跑失败', retry: start }));
    } finally {
      busy = false;
      drawer.setFooter(footer());
    }
  }

  drawer = openDrawer({
    title,
    subtitle: context,
    size: 'xl',
    body: loadingState('正在试跑…'),
    footer: h('div'),
  });
  start();
  return drawer;
}
