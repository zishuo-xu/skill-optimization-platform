/**
 * 运行记录（§44–§50）：问题摘要与定位、筛选、时间日志、按需加载详情、
 * 跟随/暂停、SSE 实时追加与断线重连、历史回放与去重。
 */

import { api, ApiError, subscribeRunEvents } from '../api_v3.js';
import { h, mount, icon, clear, focusElement } from '../dom.js';
import {
  button,
  chip,
  statusBadge,
  emptyState,
  errorState,
  loadingState,
  inlineError,
  severityIcon,
  copyRedactedDetail,
  jsonViewer,
  codeBlock,
  copyButton,
  callout,
} from '../components.js';
import {
  formatDateTime,
  formatTime,
  isDifferentDay,
  relativeTime,
  redactValue,
  safeJsonStringify,
  shortenId,
  splitLabel,
  truncate,
} from '../format.js';
import {
  EVENT_META,
  EVENT_CATEGORIES,
  STAGE_LABELS,
  RUN_STATUS_META,
  eventMeta,
  eventLabel,
  isOptimizerEvent,
  TERMINAL_STATUSES,
  RULE_LABELS,
} from '../eventTypes.js';

const HISTORY_PAGE_SIZE = 200;
const HISTORY_MAX_PAGES = 40;

function asItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.items)) return payload.items;
  return [];
}

function asNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeEvent(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const seq = asNumber(raw.seq);
  if (seq === null) return null;
  return {
    ...raw,
    seq,
    level: raw.level || 'info',
    payload: raw.payload && typeof raw.payload === 'object' ? raw.payload : {},
  };
}

function normalizeIssue(item) {
  if (!item || typeof item !== 'object') return null;
  const seq = asNumber(item.seq);
  if (seq === null) return null;
  return {
    seq,
    level: item.level || 'warning',
    type: item.type || '',
    stage: item.stage || null,
    case_id: item.case_id || null,
    errorCode: item.error_code || item.errorCode || '',
    title: item.title || eventLabel(item.type) || '问题',
    message: item.message || '',
    impact: item.impact || '',
    nextAction: item.next_action || item.nextAction || '',
    causeSeq: asNumber(item.cause_event_seq ?? item.causeSeq),
    technicalDetail: item.technical_detail || item.technicalDetail || null,
  };
}

function eventTitle(event) {
  const payload = event.payload || {};
  if (payload.title) return payload.title;
  if (payload.issue && payload.issue.title) return payload.issue.title;
  const meta = eventMeta(event.type);
  if (payload.purpose === 'judge' && event.type.startsWith('model_call')) return `评分模型 · ${meta.label}`;
  if (event.type === 'judge_completed') {
    const cached = Number(payload.cached || 0);
    return `${meta.label}：${payload.passed ?? 0}/${payload.items ?? 0} 项一致${cached ? `（${cached} 项复用缓存）` : ''}`;
  }
  if (event.type === 'stage_started' || event.type === 'stage_completed' || event.type === 'stage_failed') {
    return `${meta.label} · ${STAGE_LABELS[event.stage] || event.stage || ''}`.replace(/ · $/, '');
  }
  if (event.type === 'preflight_item') {
    return `预检：${payload.name || '检查项'}`;
  }
  if (event.type === 'static_check_item') {
    return `静态检查：${payload.name || '检查项'}`;
  }
  if (event.type === 'rule_evaluated' && payload.rule) {
    return `接受规则：${RULE_LABELS[payload.rule] || payload.rule}`;
  }
  return meta.label;
}

function eventSummary(event) {
  const p = event.payload || {};
  const meta = eventMeta(event.type);
  const bits = [];
  if (event.type === 'skill_selected') bits.push(p.skill_id);
  if (event.type === 'skill_read') bits.push(p.path ? `读取 ${p.path}` : '');
  if (['tool_call_started', 'tool_result', 'fixture_miss'].includes(event.type)) {
    if (p.tool) bits.push(p.tool);
    if (p.arguments) bits.push(truncate(safeJsonStringify(p.arguments, 0), 60));
  }
  if (event.type === 'model_call_started') {
    if (p.model) bits.push(p.model);
    if (p.attempt) bits.push(`第 ${p.attempt} 次尝试`);
    if (p.messages_count) bits.push(`${p.messages_count} 条消息`);
  }
  if (event.type === 'model_call_finished') {
    if (p.finish_reason) bits.push(p.finish_reason);
    if (p.duration_ms) bits.push(`${p.duration_ms} ms`);
    appendResponseSummary(bits, p.response_summary);
  }
  if (event.type === 'optimizer_input_built') {
    bits.push(`失败 ${p.failure_count ?? '?'} 条 · 提供 ${p.provided_failures ?? p.failure_count ?? '?'} 条`);
    if (p.files) bits.push(`${Array.isArray(p.files) ? p.files.length : p.files} 个文件`);
    if (p.chars) bits.push(`${p.chars} 字符`);
    bits.push(p.truncated ? '输入被截断' : '无截断');
    if (p.strategy) bits.push(`策略 ${p.strategy}`);
  }
  if (event.type === 'optimizer_call_started' && p.model) bits.push(p.model);
  if (event.type === 'optimizer_call_finished') {
    if (p.finish_reason) bits.push(p.finish_reason);
    if (p.usage?.output_tokens) bits.push(`输出 ${p.usage.output_tokens} tok`);
    appendResponseSummary(bits, p.response_summary);
  }
  if (event.type === 'optimizer_output_parsed') {
    if (p.summary) bits.push(p.summary);
  }
  if (event.type === 'patch_created') {
    if (p.summary) bits.push(p.summary);
    if (p.edits) bits.push(`${p.edits.length} 处修改`);
  }
  if (event.type === 'case_started' || event.type === 'case_completed' || event.type === 'case_failed') {
    if (p.phase) bits.push(p.phase === 'baseline' ? '基线' : p.phase === 'candidate' ? '候选' : p.phase);
    if (p.passed !== undefined) bits.push(p.passed ? '通过' : '未通过');
  }
  if (event.type === 'score_completed' && p.passed !== undefined) bits.push(p.passed ? '通过' : '未通过');
  if (event.type === 'preflight_item' || event.type === 'static_check_item') {
    bits.push(p.ok === false ? '未通过' : '通过');
    if (p.detail || p.message) bits.push(p.detail || p.message);
  }
  if (event.type === 'rule_evaluated') {
    if (p.rule) bits.push(RULE_LABELS[p.rule] || p.rule);
    bits.push(p.ok ? '通过' : '未通过');
    if (p.detail) bits.push(p.detail);
  }
  if (event.type === 'decision_made') {
    bits.push({ accept: '达标', reject: '未达标', inconclusive: '证据不足' }[p.decision] || p.decision || '');
    if (p.reason) bits.push(truncate(p.reason, 60));
  }
  if (['run_completed', 'run_failed', 'run_cancelled', 'run_interrupted'].includes(event.type) && p.reason) {
    bits.push(p.reason);
  }
  if (event.type === 'retest_started' && p.repeats) bits.push(`重复 ${p.repeats} 次`);
  if (event.type === 'retest_completed') {
    if (p.improvements !== undefined) bits.push(`改善 ${p.improvements}`);
    if (p.regressions !== undefined) bits.push(`回归 ${p.regressions}`);
  }
  if (!bits.filter(Boolean).length && p.message && !event.payload.issue) bits.push(truncate(p.message, 80));
  if (!bits.filter(Boolean).length && meta.category === 'lifecycle' && p.mode) bits.push(p.mode);
  return bits.filter(Boolean).join(' · ');
}

function appendResponseSummary(bits, summary) {
  if (typeof summary === 'string' && summary) {
    bits.push(`输出：${truncate(summary, 70)}`);
    return;
  }
  if (!summary || typeof summary !== 'object') return;
  const names = Array.isArray(summary.tool_names) ? summary.tool_names.filter(Boolean) : [];
  if (names.length) bits.push(`工具：${names.join('、')}`);
  if (summary.has_text === false && names.length) bits.push('结构化工具输出');
  const preview = summary.text_preview || summary.content_preview;
  if (preview) bits.push(`文本：${truncate(String(preview), 70)}`);
}

function isModelOutputEvent(event) {
  return event?.type === 'model_call_finished' || event?.type === 'optimizer_call_finished';
}

function contextBits(event) {
  const bits = [];
  if (event.case_id) bits.push({ key: 'case', text: event.case_id, mono: true });
  if (event.stage) bits.push({ key: 'stage', text: STAGE_LABELS[event.stage] || event.stage });
  if (event.split) bits.push({ key: 'split', text: splitLabel(event.split) });
  const phase = event.payload?.phase;
  if (phase === 'baseline' || phase === 'candidate') bits.push({ key: 'phase', text: phase === 'baseline' ? '基线' : '候选' });
  if (event.variant && event.variant !== 'main') bits.push({ key: 'variant', text: event.variant });
  if (event.repeat_index) bits.push({ key: 'repeat', text: `重复 #${event.repeat_index}` });
  if (event.attempt && event.attempt > 1) bits.push({ key: 'attempt', text: `尝试 #${event.attempt}` });
  if (event.call_id) bits.push({ key: 'call', text: shortenId(event.call_id, 18), mono: true, title: event.call_id });
  return bits;
}

/**
 * @param {{run:object, onRunActivity?:(event:object)=>void, onConnectionChange?:(state:string)=>void}} options
 * @returns {{element:HTMLElement, destroy:()=>void, updateRun:(run:object)=>void, filterByCase:(caseId:string|null)=>void, refresh:()=>Promise<void>}}
 */
export function createRunLog({ run, onRunActivity = null, onConnectionChange = null }) {
  let currentRun = run;
  const state = {
    events: new Map(),
    issues: [],
    issueCounts: { error: 0, warning: 0 },
    issuesLoaded: false,
    historyLoading: true,
    historyError: null,
    historyMissing: false,
    historyTotal: null,
    filters: { mode: 'all', scope: 'all', includeOptimizer: true },
    expanded: new Set(),
    rowNodes: new Map(),
    artifacts: new Map(),
    modelOutputs: new Map(),
    follow: true,
    newCount: 0,
    connection: 'connecting',
    notice: '',
    focusSeq: null,
    truncatedRange: null,
    pendingCalls: new Set(),
    expandedIssueGroups: new Set(),
    runWidePending: false,
  };

  const issuesHost = h('div');
  const toolbarHost = h('div');
  const noticeHost = h('div');
  const bannersHost = h('div');
  const listHost = h('div.log-list');
  const scroll = h('div.log-scroll', { tabindex: '0', 'aria-label': '运行记录时间线' });
  const footerHost = h('div');
  const followBar = h('div.log-follow-bar', { hidden: true });
  const scrollWrap = h(
    'div.log-scroll-wrap',
    null,
    bannersHost,
    scroll,
    followBar
  );
  const element = h('div.run-log', null, issuesHost, noticeHost, toolbarHost, scrollWrap, footerHost);

  let subscription = null;
  let destroyed = false;
  let noticeTimer = null;
  let renderQueued = false;
  let issueRefreshTimer = null;

  function sortedEvents() {
    return [...state.events.values()].sort((a, b) => a.seq - b.seq);
  }

  function isTerminal() {
    return TERMINAL_STATUSES.has(currentRun?.status);
  }

  function heuristicConfigured() {
    if (currentRun?.optimizer_mode) return currentRun.optimizer_mode === 'heuristic';
    const environment = currentRun?.snapshot?.environment || currentRun?.environment || {};
    return environment?.optimizer_config?.provider === 'heuristic';
  }

  // Rule-based repair chosen in the environment is expected behaviour, not a
  // problem: show it as information (older runs stored it as an error).
  const HEURISTIC_TITLE = '规则修复模式';
  const HEURISTIC_MESSAGE = '执行环境选择了规则修复模式：按固定规则生成候选，不调用优化模型。';
  const HEURISTIC_IMPACT = '候选仍需通过静态检查与配对验证后才会给出结论。';

  function normalizeExpectedHeuristicIssue(issue) {
    if (!issue || issue.errorCode !== 'optimizer_not_configured' || !heuristicConfigured()) return issue;
    return {
      ...issue,
      level: 'info',
      title: HEURISTIC_TITLE,
      message: HEURISTIC_MESSAGE,
      impact: HEURISTIC_IMPACT,
    };
  }

  function normalizeExpectedHeuristicEvent(event) {
    if (!event || !heuristicConfigured() || event.payload?.issue?.error_code !== 'optimizer_not_configured') return event;
    return {
      ...event,
      level: 'info',
      payload: {
        ...event.payload,
        issue: {
          ...event.payload.issue,
          title: HEURISTIC_TITLE,
          message: HEURISTIC_MESSAGE,
          impact: HEURISTIC_IMPACT,
        },
      },
    };
  }

  function computePendingCalls() {
    const finished = new Set();
    const started = [];
    for (const event of state.events.values()) {
      if (event.type === 'model_call_finished' || event.type === 'model_call_failed') {
        if (event.call_id) finished.add(event.call_id);
      }
      if (event.type === 'model_call_started' && event.call_id) started.push(event);
    }
    state.pendingCalls = new Set(started.filter((event) => !finished.has(event.call_id)).map((event) => event.call_id));
  }

  function filteredEvents() {
    const { mode, scope, includeOptimizer } = state.filters;
    return sortedEvents().filter((event) => {
      if (mode === 'issues' && event.level !== 'warning' && event.level !== 'error') return false;
      if (scope === 'run' && event.case_id) return false;
      if (scope !== 'all' && scope !== 'run' && event.case_id !== scope) return false;
      if (!includeOptimizer && isOptimizerEvent(event.type)) return false;
      return true;
    });
  }

  function hasActiveFilters() {
    return state.filters.mode !== 'all' || state.filters.scope !== 'all' || !state.filters.includeOptimizer;
  }

  function resetFilters() {
    state.filters = { mode: 'all', scope: 'all', includeOptimizer: true };
    renderToolbar();
    renderList();
  }

  function setNotice(text) {
    state.notice = text;
    renderNotice();
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => {
      state.notice = '';
      renderNotice();
    }, 6000);
  }

  function renderNotice() {
    if (!state.notice) {
      mount(noticeHost);
      return;
    }
    mount(
      noticeHost,
      h(
        'div.log-banner.log-banner--info',
        null,
        icon('info', { size: 14 }),
        h('span', { text: state.notice }),
        h('span.log-banner__spacer'),
        button({
          label: '收起',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            state.notice = '';
            renderNotice();
          },
        })
      )
    );
  }

  /* ---------------- issue summary ---------------- */

  function buildIssueGroups() {
    const bySeq = new Map(state.issues.map((issue) => [issue.seq, issue]));
    const roots = state.issues.filter(
      (issue) => !(issue.causeSeq && bySeq.has(issue.causeSeq) && issue.causeSeq !== issue.seq)
    );
    const groups = new Map();
    const ensureGroup = (issue) => {
      const key = issue.errorCode || `title:${issue.title}`;
      let group = groups.get(key);
      if (!group) {
        group = {
          key,
          title: issue.title,
          errorCode: issue.errorCode,
          level: issue.level,
          message: issue.message,
          impact: issue.impact,
          nextAction: issue.nextAction,
          stage: issue.stage,
          caseIds: new Set(),
          instances: [],
          related: [],
        };
        groups.set(key, group);
      }
      if (issue.level === 'error') group.level = 'error';
      if (issue.case_id) group.caseIds.add(issue.case_id);
      return group;
    };
    for (const root of roots) {
      const group = ensureGroup(root);
      group.instances.push(root);
    }
    for (const issue of state.issues) {
      if (!(issue.causeSeq && bySeq.has(issue.causeSeq) && issue.causeSeq !== issue.seq)) continue;
      const root = bySeq.get(issue.causeSeq);
      const group = ensureGroup(root);
      group.related.push(issue);
    }
    for (const group of groups.values()) {
      group.instances.sort((a, b) => a.seq - b.seq);
      group.related.sort((a, b) => a.seq - b.seq);
      group.level = group.instances.some((issue) => issue.level === 'error') ? 'error' : 'warning';
    }
    return [...groups.values()].sort((a, b) => b.instances.length - a.instances.length || a.instances[0].seq - b.instances[0].seq);
  }

  function issueField(label, value) {
    if (!value) return [];
    return [h('dt', { text: label }), h('dd', { text: value })];
  }

  function renderIssues() {
    if (!state.issues.length) {
      mount(issuesHost);
      return;
    }
    const groups = buildIssueGroups();
    const errorCount = state.issueCounts.error || state.issues.filter((issue) => issue.level === 'error').length;
    const warningCount = state.issueCounts.warning || state.issues.filter((issue) => issue.level === 'warning').length;
    const tone = errorCount > 0 ? 'error' : 'warning';
    const totalRecords = state.issues.length;
    const summary =
      groups.length === 1
        ? groups[0].message
        : groups
            .slice(0, 2)
            .map((group) => `${group.title}${group.message ? `：${truncate(group.message, 46)}` : ''}`)
            .join('；');

    mount(
      issuesHost,
      h(
        `div.log-issues${tone === 'warning' ? ' log-issues--warning' : ''}`,
        null,
        h(
          'div.log-issues__head',
          null,
          h(
            'div',
            { style: { minWidth: '0' } },
            h(
              'div.log-issues__title-row',
              null,
              h('span.log-issues__icon', null, icon('alert', { size: 17 })),
              h('span.log-issues__title', {
                text:
                  groups.length === 1
                    ? `运行遇到问题：${groups[0].title}${groups[0].instances.length > 1 ? `（${groups[0].instances.length} 条记录）` : ''}`
                    : `发现 ${groups.length} 类问题（共 ${totalRecords} 条记录）`,
              }),
              h('span.log-issues__count', { text: `${errorCount} 错误 / ${warningCount} 警告` })
            ),
            h('p.log-issues__summary', { text: summary })
          ),
          button({
            label: '定位错误',
            variant: 'secondary',
            size: 'sm',
            iconName: 'arrowRight',
            onClick: () => locateIssue(groups[0].instances[0]),
          })
        ),
        h(
          'div.issue-list',
          null,
          ...groups.map((group) => {
            const first = group.instances[0];
            const expanded = state.expandedIssueGroups.has(group.key);
            const caseText =
              group.caseIds.size > 1
                ? `${[...group.caseIds].slice(0, 2).join('、')} 等 ${group.caseIds.size} 个案例`
                : group.caseIds.size === 1
                ? [...group.caseIds][0]
                : '';
            return h(
              'div.issue-card',
              null,
              h(
                'div.issue-card__head',
                null,
                h(
                  'div',
                  { style: { minWidth: '0' } },
                  h(
                    'div',
                    { class: 'row', style: { gap: '8px' } },
                    h('div.issue-card__title', { text: group.title }),
                    group.instances.length > 1
                      ? h('span.log-issues__count', { text: `${group.instances.length} 条` })
                      : null,
                    group.errorCode ? chip(group.errorCode, 'danger') : null
                  )
                ),
                h('span.xsmall.muted', { text: `${STAGE_LABELS[first.stage] || first.stage || '整轮'} · #${first.seq}` })
              ),
              h(
                'dl.issue-card__fields',
                null,
                ...issueField(
                  '失败位置',
                  [STAGE_LABELS[first.stage] || first.stage, first.type ? eventLabel(first.type) : '', caseText].filter(Boolean).join(' · ')
                ),
                ...issueField('直接原因', group.message),
                ...issueField('影响范围', group.impact),
                ...issueField('后续处理', group.nextAction)
              ),
              group.related.length
                ? h(
                    'p.issue-card__merged',
                    null,
                    `已合并 ${group.related.length} 条因果相关记录：${group.related.map((child) => child.title).join('；')}`
                  )
                : null,
              h(
                'div.issue-card__actions',
                null,
                button({ label: '定位错误', variant: 'ghost', size: 'sm', iconName: 'arrowRight', onClick: () => locateIssue(first) }),
                group.instances.length > 1
                  ? button({
                      label: expanded ? '收起逐项定位' : `逐项定位（${group.instances.length}）`,
                      variant: 'ghost',
                      size: 'sm',
                      iconName: expanded ? 'chevronDown' : 'chevronRight',
                      onClick: () => {
                        if (expanded) state.expandedIssueGroups.delete(group.key);
                        else state.expandedIssueGroups.add(group.key);
                        renderIssues();
                      },
                    })
                  : null,
                ...(group.related.length
                  ? [button({ label: `定位关联回退（${group.related.length}）`, variant: 'ghost', size: 'sm', onClick: () => locateIssue(group.related[0]) })]
                  : [])
              ),
              expanded
                ? h(
                    'div',
                    { class: 'row', style: { gap: '6px', marginTop: '8px', flexWrap: 'wrap' } },
                    ...group.instances.map((instance) =>
                      button({
                        label: `${instance.ts ? formatTime(instance.ts) : `#${instance.seq}`}${instance.case_id ? ` · ${instance.case_id}` : ''}`,
                        variant: 'ghost',
                        size: 'sm',
                        onClick: () => locateIssue(instance),
                      })
                    )
                  )
                : null
            );
          })
        )
      )
    );
  }

  /* ---------------- toolbar ---------------- */

  function caseOptions() {
    const ids = new Set();
    for (const event of state.events.values()) {
      if (event.case_id) ids.add(event.case_id);
    }
    return [...ids].sort();
  }

  function warningErrorCount() {
    let count = 0;
    for (const event of state.events.values()) {
      if (event.level === 'warning' || event.level === 'error') count += 1;
    }
    return count;
  }

  function renderToolbar() {
    const matched = filteredEvents().length;
    const total = state.events.size;
    const modeTabs = h(
      'div.segmented',
      { role: 'group', 'aria-label': '记录筛选' },
      h(
        `button.segmented__btn${state.filters.mode === 'all' ? ' is-active' : ''}`,
        {
          type: 'button',
          onclick: () => {
            state.filters.mode = 'all';
            renderToolbar();
            renderList();
          },
        },
        '全部记录'
      ),
      h(
        `button.segmented__btn${state.filters.mode === 'issues' ? ' is-active' : ''}`,
        {
          type: 'button',
          onclick: () => {
            state.filters.mode = 'issues';
            renderToolbar();
            renderList();
          },
        },
        `错误与警告${warningErrorCount() ? ` (${warningErrorCount()})` : ''}`
      )
    );

    const caseSelect = h(
      'select.select',
      {
        'aria-label': '案例范围',
        onchange: (event) => {
          state.filters.scope = event.target.value;
          renderToolbar();
          renderList();
        },
      },
      h('option', { value: 'all', selected: state.filters.scope === 'all' }, '全部案例范围'),
      h('option', { value: 'run', selected: state.filters.scope === 'run' }, '仅整轮事件'),
      ...caseOptions().map((caseId) =>
        h('option', { value: caseId, selected: state.filters.scope === caseId }, caseId)
      )
    );

    const optimizerToggle = h(
      'label.log-toolbar__toggle',
      { title: '优化器事件（输入、调用、解析、回退）' },
      h('input', {
        type: 'checkbox',
        checked: state.filters.includeOptimizer,
        onchange: (event) => {
          state.filters.includeOptimizer = event.target.checked;
          renderToolbar();
          renderList();
        },
      }),
      h('span', { text: '包含优化器事件' })
    );

    mount(
      toolbarHost,
      h(
        'div.log-toolbar',
        null,
        modeTabs,
        caseSelect,
        optimizerToggle,
        h('span.log-toolbar__spacer'),
        h('span.log-toolbar__count', { text: `匹配 ${matched} 条${matched !== total ? ` / 共 ${total} 条` : ''}` }),
        hasActiveFilters()
          ? button({ label: '清除筛选', variant: 'ghost', size: 'sm', iconName: 'x', onClick: resetFilters })
          : null
      )
    );
  }

  /* ---------------- banners / footer ---------------- */

  function renderBanners() {
    const items = [];
    if (state.connection === 'error') {
      items.push(
        h(
          'div.log-banner.log-banner--warning',
          null,
          h('span.spinner.spinner--sm'),
          h('span', {
            text: isTerminal()
              ? '实时连接中断，正在重连…已加载的历史记录不受影响，任务状态独立展示。'
              : '实时连接中断，正在重连…已保留已收到的记录；任务状态独立展示。',
          })
        )
      );
    }
    if (state.focusSeq && state.issues.some((issue) => issue.seq === state.focusSeq)) {
      // transient focus hint is handled by the highlighted row
    }
    if (state.truncatedRange) {
      items.push(
        h(
          'div.log-truncated-note',
          null,
          icon('alert', { size: 13 }),
          h('span', {
            text: `日志较长：已加载 ${state.truncatedRange.loaded} 条，另有 ${state.truncatedRange.remaining} 条记录未加载。`,
          }),
          button({ label: '加载其余记录', variant: 'ghost', size: 'sm', onClick: () => loadAllHistory() })
        )
      );
    }
    if (items.length) mount(bannersHost, ...items);
    else mount(bannersHost);
  }

  function taskStateText() {
    const status = currentRun?.status || 'running';
    const meta = RUN_STATUS_META[status] || RUN_STATUS_META.running;
    if (status === 'completed') {
      if (currentRun.decision === 'reject') return '运行已完成：评测未达标，原因是指标规则不满足，不是系统运行失败。';
      if (currentRun.decision === 'inconclusive') return '运行已完成：证据不足（候选未生成或验证不完整）。';
      if (currentRun.decision === 'accept') return '运行已完成：评测达标，等待应用候选。';
      return '运行已完成。';
    }
    if (status === 'failed') return `运行失败：${currentRun.error_message || currentRun.decision_reason || '请查看运行记录中的错误事件。'}`;
    if (status === 'cancelled') return '运行已取消；已产生的费用不会回退。';
    if (status === 'interrupted') return '运行中断：进程可能已重启，已有过程保留。';
    if (status === 'cancel_requested') return '已请求取消，等待在途案例结束。';
    if (status === 'queued') return '已排队，等待 Worker 领取任务。';
    return `运行中：当前阶段 ${STAGE_LABELS[currentRun.stage] || currentRun.stage || '—'}。`;
  }

  function renderFooter() {
    const connectionLabel =
      state.connection === 'open'
        ? '实时连接已建立'
        : state.connection === 'error'
        ? '实时连接中断，正在重连'
        : state.connection === 'closed'
        ? '实时连接已关闭'
        : '正在连接实时事件…';
    mount(
      footerHost,
      h(
        'div.log-footer',
        null,
        h(
          'div.log-footer__state',
          null,
          statusBadge(currentRun?.status || 'running', 'run'),
          h('span', { text: taskStateText() })
        ),
        h(
          'div.row',
          { style: { gap: '12px' } },
          state.events.size
            ? h('span', { text: `已加载 ${state.events.size} 条事件 · 最新 #${Math.max(...state.events.keys())}` })
            : null,
          h('span', { text: isTerminal() ? '历史记录模式' : connectionLabel })
        )
      )
    );
  }

  /* ---------------- rows ---------------- */

  function rowMatches(event) {
    return filteredEvents().includes(event);
  }

  function renderList({ keepScroll = false } = {}) {
    clear(listHost);
    state.rowNodes.clear();
    const events = filteredEvents();
    if (state.historyLoading && !state.events.size) {
      mount(scroll, loadingState('正在加载运行记录…'));
      renderFollowBar();
      return;
    }
    if (state.historyError) {
      mount(
        scroll,
        h(
          'div.log-empty',
          null,
          errorState(state.historyError, { title: '无法加载运行记录', retry: () => loadHistory() })
        )
      );
      renderFollowBar();
      return;
    }
    if (state.historyMissing) {
      mount(
        scroll,
        isTerminal()
          ? emptyState({
              iconName: 'info',
              title: '此运行未保存详细过程',
              description: '运行记录接口没有返回事件。界面不会推测或补造旧事件；任务状态与结论仍以上方为准。',
            })
          : emptyState({
              iconName: 'clock',
              title: currentRun?.status === 'queued' ? '运行已排队' : '正在等待首批事件',
              description: '任务已创建，暂时还没有可显示的过程记录；收到事件后会自动追加到这里。',
            })
      );
      renderFollowBar();
      return;
    }
    if (!events.length) {
      const queued = currentRun?.status === 'queued';
      mount(
        scroll,
        queued
          ? emptyState({
              iconName: 'clock',
              title: '运行已排队',
              description: '等待 Worker 领取任务。领取后会在这里实时显示执行步骤。',
            })
          : emptyState({
              iconName: 'search',
              title: '没有匹配的记录',
              description: hasActiveFilters()
                ? '当前筛选条件下没有事件。可清除筛选查看全部记录，或调整案例范围。'
                : '本次运行暂时没有产生可显示的事件。',
              actions: hasActiveFilters()
                ? [{ label: '清除筛选', variant: 'secondary', onClick: resetFilters }]
                : [],
            })
      );
      renderFollowBar();
      return;
    }
    mount(scroll, listHost);
    const fragment = document.createDocumentFragment();
    for (const event of events) {
      const row = buildRow(event);
      state.rowNodes.set(event.seq, row);
      if (state.expanded.has(event.seq)) {
        row.classList.add('is-expanded');
        row.appendChild(buildDetail(event));
      }
      fragment.appendChild(row);
    }
    listHost.appendChild(fragment);
    renderFollowBar();
    if (!keepScroll) {
      requestAnimationFrame(() => {
        if (state.follow) scrollToBottom();
      });
    }
  }

  function buildRow(event) {
    const row = h('div.log-row-wrap', { id: `log-seq-${event.seq}`, dataset: { seq: String(event.seq) } });
    const level = event.level || 'info';
    const pendingCall = !isTerminal() && event.type === 'model_call_started' && event.call_id && state.pendingCalls.has(event.call_id);
    const title = frowTitle(event, pendingCall);
    const context = contextBits(event);
    const summary = eventSummary(event);
    const meta = eventMeta(event.type);

    const titleNode = h(
      'span.log-row__title-text',
      { tabindex: '-1', text: title }
    );

    const buttonEl = h(
      `button.log-row.log-row--${level}`,
      {
        type: 'button',
        'aria-expanded': state.expanded.has(event.seq) ? 'true' : 'false',
        title: `${formatDateTime(event.ts)} · ${event.type}`,
        onclick: () => toggleEvent(event.seq),
      },
      h('span.log-row__time', { text: formatTime(event.ts), title: formatDateTime(event.ts) }),
      h('span.log-row__icon', null, severityIcon(level)),
      h(
        'div.log-row__main',
        null,
        h(
          'div.log-row__title',
          null,
          titleNode,
          isModelOutputEvent(event) ? chip('查看输出', 'info') : null,
          pendingCall ? chip('等待模型响应', 'info', { iconName: 'clock' }) : null,
          event.level === 'warning' ? chip('警告', 'warning') : null,
          event.level === 'error' ? chip('错误', 'danger') : null,
          isOptimizerEvent(event.type) ? chip('优化器', 'purple') : null
        ),
        context.length || summary
          ? h(
              'div.log-row__ctx',
              null,
              ...context.flatMap((bit, index) => [
                index ? h('span.ctx-sep', { 'aria-hidden': 'true' }, '·') : null,
                h(bit.mono ? 'code' : 'span', { text: bit.text, title: bit.title || undefined }),
              ]),
              context.length && summary ? h('span.ctx-sep', { 'aria-hidden': 'true' }, '·') : null,
              summary ? h('span', { text: truncate(summary, 140) }) : null
            )
          : null
      ),
      h('span.log-row__chevron', null, icon('chevronDown', { size: 14 }))
    );
    row.appendChild(buttonEl);
    row._button = buttonEl;
    return row;
  }

  function frowTitle(event, pendingCall) {
    if (pendingCall) return eventTitle(event);
    return eventTitle(event);
  }

  function toggleEvent(seq) {
    const row = state.rowNodes.get(seq);
    if (!row) return;
    if (state.expanded.has(seq)) {
      state.expanded.delete(seq);
      row.classList.remove('is-expanded');
      const detail = row.querySelector('.log-detail');
      if (detail) detail.remove();
      row._button.setAttribute('aria-expanded', 'false');
    } else {
      state.expanded.add(seq);
      row.classList.add('is-expanded');
      const event = state.events.get(seq);
      if (event) row.appendChild(buildDetail(event));
      row._button.setAttribute('aria-expanded', 'true');
    }
  }

  function buildDetail(event) {
    const payload = event.payload || {};
    const detail = h('div.log-detail');
    const issue = payload.issue || (event.level !== 'info' ? {} : null);

    if (payload.issue) {
      const issueBox = h(
        `div.log-detail__issue${event.level === 'warning' ? ' log-detail__issue--warning' : ''}`,
        null,
        h('div.log-detail__issue-title', { text: payload.issue.title || eventTitle(event) }),
        h('div.log-detail__text', { text: payload.issue.message || '' }),
        h(
          'dl.log-detail__kv',
          null,
          payload.issue.impact ? h('dt', { text: '影响' }) : null,
          payload.issue.impact ? h('dd', { text: payload.issue.impact }) : null,
          payload.issue.next_action ? h('dt', { text: '后续处理' }) : null,
          payload.issue.next_action ? h('dd', { text: payload.issue.next_action }) : null,
          payload.issue.error_code ? h('dt', { text: '错误码' }) : null,
          payload.issue.error_code ? h('dd', null, h('code', { text: payload.issue.error_code })) : null
        ),
        payload.issue.cause_event_seq
          ? h(
              'div',
              { style: { marginTop: '8px' } },
              h(
                'button.btn.btn--ghost.btn--sm',
                { type: 'button', onclick: () => locateSeq(payload.issue.cause_event_seq) },
                icon('arrowRight', { size: 13 }),
                h('span', { text: `定位关联原因记录 #${payload.issue.cause_event_seq}` })
              )
            )
          : null
      );
      detail.appendChild(issueBox);
    }

    const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : [];
    const requestArtifacts = artifacts.filter((artifact) => /request/i.test(artifact.kind || artifact.id || ''));
    const responseArtifacts = artifacts.filter((artifact) => /response|raw|output/i.test(artifact.kind || artifact.id || ''));
    const otherArtifacts = artifacts.filter((artifact) => !requestArtifacts.includes(artifact) && !responseArtifacts.includes(artifact));

    if (isModelOutputEvent(event)) {
      appendSection(detail, buildModelOutputSection(event));
    }

    // request / messages
    const requestData = payload.request ?? payload.messages ?? payload.prompt;
    if (requestData !== undefined || requestArtifacts.length) {
      appendSection(
        detail,
        buildSection(
          '请求与 messages',
          h(
            'div.stack',
            null,
            requestData !== undefined ? h('div', null, renderLooseContent(requestData, { maxHeight: '280px' })) : null,
            ...requestArtifacts.map((artifact) => artifactViewer(artifact))
          ),
          false
        )
      );
    }

    // raw response
    const responseData = isModelOutputEvent(event)
      ? undefined
      : payload.response ??
        payload.raw_output ??
        payload.raw ??
        payload.output ??
        payload.raw_preview ??
        (payload.content && event.type !== 'model_delta' ? payload.content : undefined);
    if (!isModelOutputEvent(event) && (responseData !== undefined || responseArtifacts.length)) {
      appendSection(
        detail,
        buildSection(
          '原始返回',
          h(
            'div.stack',
            null,
            responseData !== undefined ? h('div', null, renderLooseContent(responseData, { maxHeight: '320px' })) : null,
            ...responseArtifacts.map((artifact) => artifactViewer(artifact))
          ),
          false
        )
      );
    }

    // score diff
    const scoreData = payload.score ?? payload.scores;
    const diagnostics = payload.diagnostics;
    if (scoreData !== undefined || (Array.isArray(diagnostics) && diagnostics.length)) {
      const blocks = [];
      if (Array.isArray(diagnostics) && diagnostics.length) {
        blocks.push(
          h(
            'div.param-diff-list',
            null,
            ...diagnostics.map((diff) =>
              h(
                'div.param-diff',
                null,
                h('div.param-diff__path', { text: diff.field_path || diff.category || '—' }),
                h(
                  'div.param-diff__cell',
                  null,
                  h('span.param-diff__label', { text: '预期' }),
                  h('span.param-diff__value.is-expected', { text: formatDiffValue(diff.expected_value ?? diff.expected) })
                ),
                h(
                  'div.param-diff__cell',
                  null,
                  h('span.param-diff__label', { text: '实际' }),
                  h('span.param-diff__value.is-actual', { text: formatDiffValue(diff.actual_value ?? diff.actual) })
                )
              )
            )
          )
        );
      }
      if (scoreData !== undefined) {
        blocks.push(jsonViewer(scoreData, { label: '评分结果', redact: true, maxHeight: '260px' }));
      }
      appendSection(detail, buildSection('评分差异', h('div.stack', null, ...blocks), false));
    }

    // key payload fields (non-issue, non-artifact)
    const excludedKeys = [
      'issue', 'artifacts', 'request', 'messages', 'prompt', 'response', 'raw_output',
      'raw', 'output', 'raw_preview', 'score', 'scores', 'diagnostics', 'content', 'title', 'message',
    ];
    if (isModelOutputEvent(event)) {
      excludedKeys.push('response_summary', 'tool_calls', 'finish_reason', 'usage', 'model', 'optimizer_output_preview', 'output_preview', 'preview_truncated');
    }
    const summaryEntries = Object.entries(payload).filter(([key]) => {
      if (excludedKeys.includes(key)) return false;
      const value = payload[key];
      return value !== undefined && value !== null && (typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length <= 8);
    });
    if (summaryEntries.length) {
      appendSection(
        detail,
        buildSection(
          '关键信息',
          h(
            'dl.log-detail__kv',
            null,
            ...summaryEntries.slice(0, 14).flatMap(([key, value]) => [
              h('dt', { text: key }),
              h(
                'dd',
                null,
                typeof value === 'object'
                  ? h('code.xsmall', { text: safeJsonStringify(value, 0) })
                  : h('span', { text: String(value) })
              ),
            ])
          ),
          false
        )
      );
    }

    appendSection(
      detail,
      buildSection(
        '技术详情',
        h(
          'div.stack.stack--sm',
          null,
          payload.issue?.technical_detail
            ? jsonViewer(payload.issue.technical_detail, { label: '技术细节（已脱敏）', redact: true, maxHeight: '240px' })
            : null,
          payload.error ? jsonViewer(payload.error, { label: '错误对象', redact: true, maxHeight: '240px' }) : null,
          payload.stack ? codeBlock(payload.stack, { label: '堆栈（仅展开时可见）', maxHeight: '240px' }) : null,
          jsonViewer(redactValue(technicalPayload(event)), { label: '完整事件载荷（脱敏）', redact: false, maxHeight: '320px' }),
          otherArtifacts.length ? h('div', null, ...otherArtifacts.map((artifact) => artifactViewer(artifact))) : null
        ),
        false
      )
    );

    const meta = h(
      'div.log-detail__meta',
      null,
      h('span', { text: `#${event.seq}` }),
      h('span', { text: event.type }),
      event.call_id ? h('span', { text: `call ${event.call_id}` }) : null,
      h('span', { text: formatDateTime(event.ts) }),
      event.variant && event.variant !== 'main' ? h('span', { text: `variant ${event.variant}` }) : null,
      event.repeat_index ? h('span', { text: `repeat ${event.repeat_index}` }) : null,
      event.attempt ? h('span', { text: `attempt ${event.attempt}` }) : null
    );
    const actions = h(
      'div.log-detail__actions',
      null,
      copyRedactedDetail(payload),
      h(
        'button.btn.btn--ghost.btn--sm',
        { type: 'button', onclick: () => locateSeq(event.seq) },
        icon('link', { size: 13 }),
        h('span', { text: '复制此记录定位链接' })
      )
    );
    detail.appendChild(meta);
    detail.appendChild(actions);
    // copy-link writes the hash without navigating away
    actions.lastChild.addEventListener('click', async () => {
      try {
        const url = `${location.origin}${location.pathname}#/runs/${encodeURIComponent(currentRun.id)}/log`;
        await navigator.clipboard?.writeText(`${url} · seq=${event.seq}`);
        // Notification handled by toast
        const { toast } = await import('../components.js');
        toast('已复制定位信息', { type: 'success', duration: 1500 });
      } catch {
        /* ignore */
      }
    });
    return detail;
  }

  function technicalPayload(event) {
    const payload = { ...(event.payload || {}) };
    if (isModelOutputEvent(event)) {
      for (const key of ['response', 'raw_output', 'raw', 'output', 'content', 'tool_calls',
        'raw_preview', 'output_preview', 'finish_reason', 'usage', 'model', 'response_summary', 'optimizer_output_preview', 'preview_truncated']) {
        delete payload[key];
      }
    }
    return payload;
  }

  function buildModelOutputSection(event) {
    const host = h('div.stack');
    const cacheKey = String(event.seq);
    const cached = state.modelOutputs.get(cacheKey);
    const preview = event.payload?.optimizer_output_preview || event.payload?.output_preview;
    const renderResult = (result) => {
      const response = result?.response;
      const blocks = [];
      if (result?.message && result.source !== 'legacy_case_result') {
        blocks.push(callout({
          tone: result.source === 'unavailable' ? 'warning' : 'info',
          title: result.source === 'legacy_case_result' ? '来源说明' : '输出来源',
          body: result.message,
        }));
      }
      if (result?.source === 'legacy_case_result') {
        blocks.push(callout({
          tone: 'info',
          title: '历史案例级结果',
          body: result.message || '此历史记录没有逐次原始回复；以下结果来自案例保存数据，不代表本次单独模型回复。',
        }));
        if (Array.isArray(result.case_results)) {
          for (const item of result.case_results) {
            blocks.push(h('div.log-detail__legacy-output', null,
              h('div.log-detail__issue-title', { text: `${item.phase || '案例结果'}${item.variant ? ` · ${item.variant}` : ''}${item.repeat_index ? ` · 重复 #${item.repeat_index}` : ''}` }),
              item.selected_skills ? h('div', { text: `技能：${item.selected_skills.join?.('、') || item.selected_skills}` }) : null,
              item.action ? h('div', { text: `动作：${item.action}` }) : null,
              item.tool_calls ? jsonViewer(item.tool_calls, { label: '工具调用', open: true, redact: true, maxHeight: '220px' }) : null,
              item.raw_output ? renderModelContent(item.raw_output, { maxHeight: '240px' }) : null,
              item.missing_fields ? h('div', { text: `缺失字段：${item.missing_fields.join?.('、') || item.missing_fields}` }) : null
            ));
          }
        }
        return blocks;
      }
      if (!response) {
        blocks.push(h('div.log-detail__text', { text: result?.message || '未保存逐次模型输出。' }));
        return blocks;
      }
      const content = response.content;
      const toolCalls = Array.isArray(response.tool_calls) ? response.tool_calls : [];
      if (content) blocks.push(h('div.stack.stack--sm', null, h('div.log-detail__issue-title', { text: '正文' }), renderModelContent(content, { maxHeight: '320px' })));
      else if (toolCalls.length) blocks.push(h('div.log-detail__text', { text: '正文为空；结构化工具调用就是本次模型输出。' }));
      else blocks.push(h('div.log-detail__text', { text: '模型返回为空，未保存可展示的正文或工具调用。' }));
      if (toolCalls.length) blocks.push(jsonViewer(toolCalls, { label: '工具名称与 JSON 参数', open: true, redact: true, maxHeight: '320px' }));
      const meta = { finish_reason: response.finish_reason, usage: response.usage, model: response.model };
      if (Object.values(meta).some((value) => value !== undefined && value !== null)) blocks.push(jsonViewer(meta, { label: '输出元数据', redact: true, maxHeight: '220px' }));
      return blocks;
    };
    if (cached?.status === 'done') {
      mount(host, ...renderResult(cached.data));
    } else if (cached?.status === 'loading') {
      mount(host, loadingState('正在加载模型输出…', { compact: true }));
    } else if (cached?.status === 'error') {
      mount(host, h('div.stack.stack--sm', null,
        inlineError(`加载失败：${cached.error}`),
        button({ label: '重试', variant: 'ghost', size: 'sm', onClick: () => loadModelOutput(event, host, renderResult) })
      ));
    } else if (preview) {
      mount(host,
        callout({ tone: 'info', title: event.payload?.preview_truncated ? '历史摘要（可能截断）' : '历史摘要', body: String(preview) }),
        button({ label: '查看完整模型输出', variant: 'secondary', size: 'sm', iconName: 'eye', onClick: () => loadModelOutput(event, host, renderResult) })
      );
    } else {
      mount(host, h('div.stack', null,
        h('div.log-detail__text', { text: '模型输出按需加载；历史记录若未捕获则无法恢复逐次原始回复。' }),
        button({ label: '查看模型输出', variant: 'secondary', size: 'sm', iconName: 'eye', onClick: () => loadModelOutput(event, host, renderResult) })
      ));
    }
    return buildSection('模型输出', host, true);
  }

  async function loadModelOutput(event, host, renderResult) {
    const key = String(event.seq);
    state.modelOutputs.set(key, { status: 'loading' });
    mount(host, loadingState('正在加载模型输出…', { compact: true }));
    try {
      const data = await api.getModelOutput(currentRun.id, event.seq);
      state.modelOutputs.set(key, { status: 'done', data });
      mount(host, ...renderResult(data));
    } catch (error) {
      state.modelOutputs.set(key, { status: 'error', error: error.message });
      mount(host, inlineError(`加载失败：${error.message}`));
      const retry = button({ label: '重试', variant: 'ghost', size: 'sm', onClick: () => loadModelOutput(event, host, renderResult) });
      host.appendChild(retry);
    }
  }

  function appendSection(host, section) {
    if (section) host.appendChild(section);
  }

  function buildSection(label, content, defaultOpen = false) {
    if (!content) return null;
    const details = h(
      'details.collapsible',
      { open: defaultOpen, class: 'log-detail__block' },
      h('summary.collapsible__summary', null, icon('chevronRight', { size: 13, className: 'collapsible__chevron' }), label),
      h('div.collapsible__body', null, content)
    );
    return details;
  }

  function formatDiffValue(value) {
    if (value === undefined) return '（缺失）';
    if (typeof value === 'string') return JSON.stringify(value);
    return safeJsonStringify(value, 0);
  }

  function renderModelContent(value, { maxHeight = '320px' } = {}) {
    if (typeof value === 'string') {
      return codeBlock(value, { maxHeight, wrap: true,
        label: `文本内容（共 ${value.length} 字符，完整）` });
    }
    return jsonViewer(value, { label: '正文', open: true, redact: true, maxHeight });
  }

  function renderLooseContent(value, { maxHeight = '280px' } = {}) {
    if (typeof value === 'string') {
      const text = value;
      if (text.length > 6000) {
        return codeBlock(`${text.slice(0, 6000)}\n\n…（内容过长，已显示前 6000 字符；完整内容请通过关联资源按需加载）`, {
          maxHeight,
          wrap: true,
          label: `文本内容（共 ${text.length} 字符）`,
        });
      }
      return codeBlock(text, { maxHeight, wrap: true });
    }
    return jsonViewer(value, { label: '内容', redact: true, maxHeight });
  }

  function artifactViewer(artifact) {
    if (!artifact || !artifact.id) return null;
    const key = artifact.id;
    const host = h('div', { style: { marginTop: '6px' } });
    const details = h(
      'details.collapsible',
      null,
      h(
        'summary.collapsible__summary',
        null,
        icon('chevronRight', { size: 13, className: 'collapsible__chevron' }),
        h('span', { text: `${artifact.summary || artifact.kind || '大内容'}（${artifact.size ? `${(artifact.size / 1024).toFixed(1)} KB` : '按需加载'}）` })
      ),
      host
    );
    let loaded = false;
    details.addEventListener('toggle', async () => {
      if (!details.open || loaded) return;
      loaded = true;
      mount(host, loadingState('正在按需加载内容…', { compact: true }));
      try {
        const cache = state.artifacts.get(key);
        let content;
        if (cache && cache.status === 'done') content = cache.content;
        else {
          const result = await api.getTraceArtifact(currentRun.id, key);
          content = result.content;
          state.artifacts.set(key, { status: 'done', content });
        }
        mount(host, renderLooseContent(content, { maxHeight: '320px' }));
      } catch (error) {
        loaded = false;
        mount(host, inlineError(`加载失败：${error.message}`));
      }
    });
    return details;
  }

  /* ---------------- follow / scroll ---------------- */

  function isNearBottom() {
    return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 48;
  }

  function scrollToBottom() {
    scroll.scrollTop = scroll.scrollHeight;
    state.follow = true;
    state.newCount = 0;
    renderFollowBar();
  }

  function renderFollowBar() {
    if (!state.follow && state.newCount > 0) {
      followBar.hidden = false;
      mount(
        followBar,
        button({
          label: `有 ${state.newCount} 条新记录 · 回到最新`,
          variant: 'primary',
          size: 'sm',
          iconName: 'chevronDown',
          onClick: () => scrollToBottom(),
        })
      );
    } else {
      followBar.hidden = true;
      mount(followBar);
    }
  }

  scroll.addEventListener('scroll', () => {
    if (isNearBottom()) {
      if (!state.follow) {
        state.follow = true;
        state.newCount = 0;
        renderFollowBar();
      }
    } else if (state.follow) {
      state.follow = false;
      renderFollowBar();
    }
  });

  /* ---------------- data loading ---------------- */

  function mergeEvent(event) {
    if (!event || typeof event !== 'object') return false;
    const seq = asNumber(event.seq);
    if (seq === null) return false;
    const existing = state.events.get(seq);
    if (existing) {
      // Merge richer payloads from later fetches (live event may be partial).
      const merged = { ...existing, ...event, payload: { ...(existing.payload || {}), ...(event.payload || {}) } };
      state.events.set(seq, merged);
      return false;
    }
    state.events.set(seq, event);
    return true;
  }

  function applyEvents(items, { notify = false } = {}) {
    let added = 0;
    const addedEvents = [];
    for (const raw of items) {
      const event = normalizeExpectedHeuristicEvent(normalizeEvent(raw));
      if (!event) continue;
      if (mergeEvent(event)) {
        added += 1;
        addedEvents.push(event);
      }
    }
    if (added) {
      // A live event is authoritative evidence that the history endpoint was
      // merely empty or behind. Do not keep showing the initial-history empty
      // state, including when the history request resolves after SSE.
      state.historyMissing = false;
      computePendingCalls();
      if (notify) onRunActivity && onRunActivity(addedEvents[addedEvents.length - 1], addedEvents);
      appendNewEvents(addedEvents);
      renderToolbar();
      renderFooter();
      renderBanners();
    }
    return added;
  }

  function appendNewEvents(addedEvents) {
    const visible = addedEvents.filter((event) => rowMatchesEvent(event));
    if (!visible.length) return;
    if (state.historyLoading || state.historyMissing) {
      renderList();
      return;
    }
    if (!state.rowNodes.size) {
      renderList();
      return;
    }
    for (const event of visible) {
      if (state.rowNodes.has(event.seq)) continue;
      const row = buildRow(event);
      state.rowNodes.set(event.seq, row);
      listHost.appendChild(row);
    }
    if (state.follow) {
      requestAnimationFrame(() => {
        scroll.scrollTop = scroll.scrollHeight;
      });
    } else {
      state.newCount += visible.length;
      renderFollowBar();
    }
  }

  function rowMatchesEvent(event) {
    const { mode, scope, includeOptimizer } = state.filters;
    if (mode === 'issues' && event.level !== 'warning' && event.level !== 'error') return false;
    if (scope === 'run' && event.case_id) return false;
    if (scope !== 'all' && scope !== 'run' && event.case_id !== scope) return false;
    if (!includeOptimizer && isOptimizerEvent(event.type)) return false;
    return true;
  }

  async function loadIssues() {
    try {
      const payload = await api.getRunIssues(currentRun.id);
      const items = asItems(payload)
        .map(normalizeIssue)
        .filter(Boolean)
        .map(normalizeExpectedHeuristicIssue)
        .filter((issue) => issue.level === 'error' || issue.level === 'warning');
      state.issues = items;
      state.issueCounts = {
        error: heuristicConfigured() ? items.filter((issue) => issue.level === 'error').length : (payload?.counts?.error ?? items.filter((issue) => issue.level === 'error').length),
        warning: heuristicConfigured() ? items.filter((issue) => issue.level === 'warning').length : (payload?.counts?.warning ?? items.filter((issue) => issue.level === 'warning').length),
      };
      state.issuesLoaded = true;
      renderIssues();
      renderToolbar();
    } catch {
      state.issuesLoaded = true;
      deriveIssuesFromEvents();
    }
  }

  function deriveIssuesFromEvents() {
    const items = sortedEvents()
      .filter((event) => ['warning', 'error'].includes(event.level) && (event.payload?.issue || (event.payload?.error_code && event.payload?.title)))
      .map((event) => normalizeExpectedHeuristicIssue(normalizeIssue({ seq: event.seq, level: event.level, type: event.type, stage: event.stage, case_id: event.case_id, ...(event.payload.issue || event.payload) })));
    state.issues = items.filter((issue) => issue && (issue.level === 'error' || issue.level === 'warning'));
    state.issueCounts = {
      error: state.issues.filter((issue) => issue.level === 'error').length,
      warning: state.issues.filter((issue) => issue.level === 'warning').length,
    };
    renderIssues();
  }

  async function loadHistory({ initial = false } = {}) {
    if (initial) {
      state.historyLoading = true;
      state.historyError = null;
      state.historyMissing = false;
      renderList();
    }
    let afterSeq = 0;
    let loaded = 0;
    let pageCount = 0;
    let total = null;
    try {
      while (pageCount < HISTORY_MAX_PAGES) {
        const payload = await api.getEventHistory(currentRun.id, { afterSeq, limit: HISTORY_PAGE_SIZE });
        const items = asItems(payload);
        if (payload && typeof payload === 'object' && payload.total !== undefined) total = Number(payload.total);
        if (!items.length) break;
        applyEvents(items);
        loaded += items.length;
        afterSeq = Math.max(...items.map((item) => asNumber(item.seq) || 0));
        pageCount += 1;
        if (items.length < HISTORY_PAGE_SIZE) break;
      }
      state.historyTotal = total;
      state.historyMissing = loaded === 0 && state.events.size === 0;
      if (total !== null && total > state.events.size) {
        state.truncatedRange = { loaded: state.events.size, remaining: total - state.events.size };
      } else {
        state.truncatedRange = null;
      }
      state.historyLoading = false;
      computePendingCalls();
      renderList();
      renderToolbar();
      renderBanners();
      renderFooter();
      if (state.events.size) {
        state.follow = true;
        requestAnimationFrame(() => {
          scroll.scrollTop = scroll.scrollHeight;
        });
      }
      startSubscription();
    } catch (error) {
      state.historyLoading = false;
      if (error instanceof ApiError && (error.status === 404 || error.code === 'not_found')) {
        state.historyMissing = true;
      } else {
        state.historyError = error;
      }
      renderList();
      renderFooter();
    }
  }

  async function loadAllHistory() {
    const currentMax = state.events.size ? Math.max(...state.events.keys()) : 0;
    try {
      const payload = await api.getEventHistory(currentRun.id, { afterSeq: currentMax, limit: HISTORY_PAGE_SIZE });
      const items = asItems(payload);
      if (!items.length) {
        state.truncatedRange = null;
        renderBanners();
        return;
      }
      applyEvents(items);
      state.truncatedRange = null;
      renderBanners();
    } catch (error) {
      state.notice = `加载更早记录失败：${error.message}`;
      renderNotice();
    }
  }

  async function fillGap(afterSeq) {
    if (destroyed) return;
    let cursor = asNumber(afterSeq) ?? 0;
    try {
      for (let page = 0; page < 10; page += 1) {
        const payload = await api.getEventHistory(currentRun.id, { afterSeq: cursor, limit: HISTORY_PAGE_SIZE });
        const items = asItems(payload).map(normalizeEvent).filter(Boolean);
        if (!items.length) break;
        applyEvents(items, { notify: true });
        const max = Math.max(...items.map((item) => item.seq));
        if (max <= cursor) break;
        cursor = max;
        if (items.length < HISTORY_PAGE_SIZE) break;
      }
    } catch {
      /* the SSE reconnect will retry; gap fill failures are transient */
    }
  }

  function startSubscription() {
    if (subscription || destroyed) return;
    const afterSeq = state.events.size ? Math.max(...state.events.keys()) : 0;
    state.connection = 'connecting';
    renderBanners();
    renderFooter();
    subscription = subscribeRunEvents(currentRun.id, {
      afterSeq,
      onEvent: (event) => {
        const added = applyEvents([event], { notify: true });
        if (added) {
          // late issues (e.g. new error event) get merged into the summary
          if (event.payload?.issue || event.level === 'error' || event.level === 'warning') {
            if (issueRefreshTimer) clearTimeout(issueRefreshTimer);
            issueRefreshTimer = setTimeout(() => {
              loadIssues().catch(() => {});
            }, 400);
          }
        }
      },
      onStatus: (status) => {
        state.connection = status === 'open' ? 'open' : status === 'error' ? 'error' : status === 'closed' ? 'closed' : 'connecting';
        onConnectionChange && onConnectionChange(state.connection);
        renderBanners();
        renderFooter();
      },
      onGap: (lastSeq) => {
        fillGap(lastSeq);
      },
    });
  }

  /* ---------------- locate ---------------- */

  function isEventVisible(seq) {
    const event = state.events.get(seq);
    if (!event) return false;
    return rowMatchesEvent(event);
  }

  async function locateSeq(seq, { expand = true } = {}) {
    const targetSeq = asNumber(seq);
    if (targetSeq === null) return;
    if (!state.events.has(targetSeq)) {
      try {
        const payload = await api.getEventHistory(currentRun.id, { afterSeq: Math.max(0, targetSeq - 1), limit: 1 });
        const items = asItems(payload).map(normalizeEvent).filter(Boolean);
        if (items.length) applyEvents(items);
      } catch {
        /* fall through */
      }
    }
    if (!state.events.has(targetSeq)) {
      setNotice('未找到目标记录：它可能不在当前已加载的历史中。');
      return;
    }
    if (!isEventVisible(targetSeq)) {
      state.filters = { mode: 'all', scope: 'all', includeOptimizer: true };
      renderToolbar();
      renderList();
      setNotice('已切换到全部记录');
    }
    if (expand) {
      state.expanded.add(targetSeq);
      if (!state.rowNodes.has(targetSeq)) renderList({ keepScroll: true });
      const row = state.rowNodes.get(targetSeq);
      if (row) {
        row.classList.add('is-expanded');
        if (!row.querySelector('.log-detail')) {
          const event = state.events.get(targetSeq);
          if (event) row.appendChild(buildDetail(event));
        }
        row._button.setAttribute('aria-expanded', 'true');
      }
    }
    state.focusSeq = targetSeq;
    state.follow = false;
    renderFollowBar();
    requestAnimationFrame(() => {
      const row = state.rowNodes.get(targetSeq);
      if (!row) return;
      row.scrollIntoView({ block: 'center', behavior: 'smooth' });
      row.classList.remove('is-highlighted');
      // restart the highlight animation
      void row.offsetWidth;
      row.classList.add('is-highlighted');
      const titleNode = row.querySelector('.log-row__title-text');
      focusElement(titleNode || row._button);
      setTimeout(() => row.classList.remove('is-highlighted'), 2600);
    });
  }

  async function locateIssue(issue) {
    if (!issue) return;
    await locateSeq(issue.seq);
  }

  /* ---------------- public API ---------------- */

  function filterByCase(caseId) {
    state.filters = { mode: 'all', scope: caseId || 'all', includeOptimizer: true };
    renderToolbar();
    renderList();
    requestAnimationFrame(() => {
      scroll.scrollTop = 0;
    });
  }

  function updateRun(run) {
    currentRun = run;
    renderFooter();
    renderBanners();
    renderIssues();
  }

  function renderAll() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      renderIssues();
      renderToolbar();
      renderBanners();
      renderNotice();
      renderFooter();
    });
  }

  renderAll();
  loadHistory({ initial: true }).then(() => loadIssues());

  return {
    element,
    destroy() {
      destroyed = true;
      if (subscription) subscription.close();
      if (noticeTimer) clearTimeout(noticeTimer);
      if (issueRefreshTimer) clearTimeout(issueRefreshTimer);
    },
    updateRun,
    filterByCase,
    refresh: async () => {
      state.events.clear();
      state.rowNodes.clear();
      state.truncatedRange = null;
      await loadHistory({ initial: true });
      await loadIssues();
    },
  };
}
