/** 运行详情：四标签页（结果概览 / 案例对比 / 文件差异 / 运行记录）与结论应用。 */

import { api, download } from '../api_v3.js';
import { h, mount, icon } from '../dom.js';
import {
  button,
  iconButton,
  chip,
  statusBadge,
  pageHeader,
  sectionCard,
  tabsBar,
  emptyState,
  errorState,
  loadingState,
  openModal,
  openDrawer,
  confirmDialog,
  toast,
  kvList,
  jsonViewer,
  unifiedDiff,
  splitDiff,
  progressBar,
} from '../components.js';
import {
  formatDateTime,
  formatDelta,
  formatDuration,
  formatElapsed,
  formatNumber,
  formatPercent,
  formatCost,
  formatTokens,
  relativeTime,
  shortenId,
  splitLabel,
  truncate,
  explainCaseSide,
  FAILURE_GROUPS,
} from '../format.js';
import {
  ACTIVE_STATUSES,
  CHANGE_TYPE_META,
  DECISION_META,
  MODE_META,
  RULE_LABELS,
  RUN_STATUS_META,
  STAGE_LABELS,
  TERMINAL_STATUSES,
  decisionKey,
} from '../eventTypes.js';
import { createRunLog } from './runLog.js';
import {
  actualColumn,
  expectedColumn,
  judgmentsBlock,
  openTrialDrawer,
  sideBadge,
  timelineDetails,
  verdictRows,
} from './caseView.js';

const TABS = [
  { id: 'result', label: '结果概览' },
  { id: 'cases', label: '案例对比' },
  { id: 'diff', label: '文件差异' },
  { id: 'log', label: '运行记录' },
];

function asItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.items)) return payload.items;
  return [];
}

function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(status);
}

function changeRank(changeType) {
  return (CHANGE_TYPE_META[changeType] || { rank: 9 }).rank;
}

function normalizeCaseItem(item) {
  return {
    ...item,
    changeType: item.change_type || item.changeType || 'pending',
    baseline: item.baseline || null,
    candidate: item.candidate || null,
    diagnostics: item.diagnostics || item.baseline?.diagnostics || item.candidate?.diagnostics || [],
  };
}

export default async function runDetailsView(container, ctx) {
  const state = {
    run: ctx.run,
    project: ctx.project,
    activeTab: TABS.some((tab) => tab.id === ctx.activeTab) ? ctx.activeTab : 'result',
    resultLoaded: false,
    resultError: null,
    cases: {
      loaded: false,
      loading: false,
      error: null,
      items: [],
      filter: 'all',
      search: '',
    },
    diff: { loaded: false, loading: false, error: null, changes: [], hypotheses: [], selected: 0, mode: 'unified' },
    log: null,
    applyBusy: false,
    pollTimer: null,
    tickTimer: null,
    refreshTimer: null,
    busyCancel: false,
  };

  const headerHost = h('div');
  const tabsHost = h('div');
  const resultHost = h('div.run-tab-body');
  const casesHost = h('div.run-tab-body', { hidden: true });
  const diffHost = h('div.run-tab-body', { hidden: true });
  const logHost = h('div.run-tab-body', { hidden: true });
  let liveRefs = {};
  let casesRenderState = { drawer: null };

  const tabBodies = { result: resultHost, cases: casesHost, diff: diffHost, log: logHost };

  function active() {
    return ACTIVE_STATUSES.has(state.run.status);
  }

  function candidateVersionId() {
    return state.run.candidate?.skill_version_id || state.run.candidate_skill_version_id || null;
  }

  function baselineVersionId() {
    return state.run.snapshot?.skill_version?.id || state.run.baseline_skill_version_id || null;
  }

  /* ---------------- header ---------------- */

  function progressText() {
    const progress = state.run.stage_progress || {};
    if (state.run.status === 'queued') {
      return { label: '排队中', detail: progress.message || '等待 Worker 领取任务', completed: 0, total: 0 };
    }
    const stage = progress.stage || state.run.stage;
    const total = Number(progress.total || 0);
    const completed = Number(progress.completed || 0);
    if (isTerminalStatus(state.run.status)) {
      return {
        label: STAGE_LABELS[stage] || stage || '已结束',
        detail: total ? `本阶段已完成 ${completed}/${total} 个案例` : '运行已结束',
        completed: total ? completed : 1,
        total: total || 1,
      };
    }
    return {
      label: STAGE_LABELS[stage] || stage || '执行中',
      detail: total ? `本阶段已完成 ${completed}/${total} 个案例` : '正在准备案例…',
      completed,
      total: total || 0,
    };
  }

  function renderHeader() {
    const run = state.run;
    const progress = progressText();
    const statusMeta = RUN_STATUS_META[run.status] || RUN_STATUS_META.running;
    const modeMeta = MODE_META[run.mode] || { label: run.mode };
    const snapshot = run.snapshot || {};
    const skillVersion = snapshot.skill_version || null;
    const projectId = run.project_id;
    const candidateVersion = candidateVersionId();
    const snapshotHash = snapshot.fingerprint || snapshot.fingerprints?.skill || null;

    const elapsedSpan = h('span', { text: active() ? formatElapsed(run.started_at || run.created_at) : formatDuration(run.cost?.duration_ms) });
    const activitySpan = h('span', { text: run.stage_progress?.last_activity_at ? relativeTime(run.stage_progress.last_activity_at) : '—' });
    const activeCases = (run.stage_progress?.current_case_ids || []).length;
    liveRefs = { elapsedSpan, activitySpan };

    const technicalDetails = h(
      'details.run-header__details',
      null,
      h('summary', { text: '查看运行详情、版本与导出' }),
      h(
        'div.run-header__details-body',
        null,
        h(
          'div.run-header__meta',
          null,
          h(
            'span.meta-item',
            null,
            icon('skill', { size: 13 }),
            h('span', { text: '父版本 ' }),
            skillVersion ? h('a.mono', { href: `#/projects/${encodeURIComponent(projectId)}/skill` }, skillVersion.label || skillVersion.id || shortenId(baselineVersionId(), 14)) : h('span.mono', { text: shortenId(baselineVersionId(), 14) })
          ),
          run.parent_run_id
            ? h('span.meta-item', null, icon('versions', { size: 13 }), h('a', { href: `#/runs/${encodeURIComponent(run.parent_run_id)}` }, `父运行 ${shortenId(run.parent_run_id, 14)}`))
            : null,
          candidateVersion ? h('span.meta-item', null, icon('versions', { size: 13 }), h('span', { text: '候选版本 ' }), h('span.mono', { text: candidateVersion })) : null,
          snapshot.environment ? h('span.meta-item', null, icon('environment', { size: 13 }), h('span', { text: `环境 v${snapshot.environment.version_seq || '—'}` })) : null,
          snapshot.dataset ? h('span.meta-item', null, icon('dataset', { size: 13 }), h('span', { text: `数据集 v${snapshot.dataset.version_seq || '—'}` })) : null,
          h('span.meta-item', null, icon('clock', { size: 13 }), h('span', { text: `开始 ${formatDateTime(run.started_at || run.created_at)}` })),
          run.finished_at ? h('span.meta-item', null, icon('check', { size: 13 }), h('span', { text: `结束 ${formatDateTime(run.finished_at)}` })) : null,
          run.rules_version ? h('span.meta-item', null, h('span.xsmall.muted', { text: `规则 ${run.rules_version}` })) : null,
          snapshotHash ? h('span.meta-item', null, h('span', { text: '快照哈希 ' }), h('span.mono', { text: shortenId(snapshotHash, 18) }), copyButtonFor(snapshotHash)) : null
        ),
        h(
          'div.btn-row',
          { style: { marginTop: '8px' } },
          button({
            label: '报告 JSON',
            variant: 'ghost',
            size: 'sm',
            iconName: 'download',
            onClick: () => download(api.downloadReportUrl(run.id, 'json'), `${run.id}-report.json`),
          }),
          button({
            label: '报告 CSV',
            variant: 'ghost',
            size: 'sm',
            iconName: 'download',
            onClick: () => download(api.downloadReportUrl(run.id, 'csv'), `${run.id}-report.csv`),
          })
        )
      )
    );

    mount(
      headerHost,
      h(
        'div.run-header',
        null,
        h(
          'div.run-header__top',
          null,
          h(
            'div',
            { style: { minWidth: '0' } },
            h(
              'div.run-header__id-row',
              null,
              h('span.run-header__id', { text: `运行 ${run.id}` }),
              copyButtonFor(run.id),
              // One status chip: the conclusion once there is one, otherwise the run state.
              run.decision ? statusBadge(decisionKey(run), 'decision') : statusBadge(run.status, 'run'),
              chip(modeMeta.label, 'neutral'),
              isTerminalStatus(run.status) ? null : chip('运行中不可修改配置', 'warning')
            )
          ),
          h(
            'div.run-header__actions',
            null,
            active()
              ? button({ label: run.status === 'cancel_requested' ? '正在取消…' : '取消运行', variant: 'danger', size: 'sm', iconName: 'cancel', disabled: run.status === 'cancel_requested' || state.busyCancel, onClick: cancelRun })
              : null,
            technicalDetails
          )
        ),
        active()
          ? h(
          'div.stage-progress',
          null,
          h(
            'div.stage-progress__head',
            null,
            h(
              'div.stage-progress__stage',
              null,
              icon(statusMeta.icon, { size: 15 }),
              h('span', { text: progress.label }),
              run.status === 'cancel_requested' ? chip('取消中', 'warning') : null,
              active() ? h('span.spinner.spinner--sm') : null
            ),
            h(
              'div.stage-progress__stats',
              null,
              h('span', { text: progress.detail }),
              h('span.sep', { 'aria-hidden': 'true' }, '·'),
              h('span', null, '活动案例 ', h('strong', { text: String(activeCases) })),
              active()
                ? h(
                    'span',
                    null,
                    h('span.sep', { 'aria-hidden': 'true' }, '· '),
                    h('span', { text: '已等待 ' }),
                    elapsedSpan
                  )
                : null,
              active()
                ? h('span', null, h('span.sep', { 'aria-hidden': 'true' }, '· '), h('span', { text: '最近活动 ' }), activitySpan)
                : null,
              run.finished_at ? h('span', null, h('span.sep', { 'aria-hidden': 'true' }, '· '), h('span', { text: `完成于 ${formatDateTime(run.finished_at)}` })) : null
            )
          ),
          h('div', null, progressBar({ value: progress.completed, max: progress.total || 0, tone: run.status === 'failed' ? 'danger' : isTerminalStatus(run.status) ? 'success' : 'accent' })),
          h(
            'p.xsmall.muted',
            { style: { marginTop: '6px' } },
            run.stage_progress?.message || (active() ? '进度按当前阶段统计，不混合其它阶段的案例总数。后台运行不依赖页面保持打开。' : '运行已结束，结果与过程记录可随时回看。')
          )
          )
          : null
      )
    );
  }

  function copyButtonFor(text) {
    return iconButton('copy', {
      label: '复制运行编号',
      title: '复制运行编号',
      onClick: async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast('已复制运行编号', { type: 'success', duration: 1500 });
        } catch {
          toast('复制失败', { type: 'warning' });
        }
      },
    });
  }

  /* ---------------- tabs ---------------- */

  function renderTabs() {
    mount(
      tabsHost,
      tabsBar({
        tabs: TABS.map((tab) => ({
          id: tab.id,
          label: tab.label,
          count:
            tab.id === 'cases' && state.cases.loaded
              ? state.cases.items.filter((item) => ['regressed', 'fixed'].includes(item.changeType)).length || undefined
              : undefined,
        })),
        active: state.activeTab,
        onSelect: (id) => switchTab(id),
      })
    );
  }

  function switchTab(tabId, { syncHash = true } = {}) {
    if (!TABS.some((tab) => tab.id === tabId)) tabId = 'result';
    state.activeTab = tabId;
    if (syncHash) {
      const newHash = `#/runs/${encodeURIComponent(state.run.id)}/${tabId}`;
      if (location.hash !== newHash) history.replaceState(null, '', newHash);
    }
    for (const [id, host] of Object.entries(tabBodies)) {
      host.hidden = id !== tabId;
    }
    renderTabs();
    if (tabId === 'log') ensureLog();
    if (tabId === 'result') loadResult();
    if (tabId === 'cases') loadCases();
    if (tabId === 'diff') loadDiff();
  }

  /* ---------------- results ---------------- */

  function computeTransitions() {
    const items = state.cases.items;
    const counts = { fixed: 0, regressed: 0, unchangedPass: 0, unchangedFail: 0, pending: 0, invalid: 0 };
    for (const item of items) {
      if (item.changeType === 'fixed') counts.fixed += 1;
      else if (item.changeType === 'regressed') counts.regressed += 1;
      else if (item.changeType === 'unchanged_pass') counts.unchangedPass += 1;
      else if (item.changeType === 'unchanged_fail') counts.unchangedFail += 1;
      else if (item.changeType === 'invalid') counts.invalid += 1;
      else counts.pending += 1;
    }
    return counts;
  }

  function metricsTable(metrics, temporary) {
    const rows = [
      ['routing', '选对技能 R', '分发准确率：选中了允许的子技能'],
      ['tool_args', '参数正确 A', '工具入参准确率：业务工具与参数都对'],
      ['joint', '整体通过 J', '联合通过率：技能、动作、工具、参数全部正确'],
    ];
    const body = rows.map(([key, label, hint]) => {
      const base = metrics?.baseline?.[key];
      const cand = metrics?.candidate?.[key];
      const delta = metrics?.delta?.[key];
      const valueCell = (block) => {
        if (!block || block.status === 'not_evaluated' || block.value === null || block.value === undefined) {
          return h('span.metric-value--na', { text: '未评估' });
        }
        return h('span', { text: formatPercent(block.value) });
      };
      const deltaClass = delta === null || delta === undefined ? 'delta-flat' : delta > 0.0001 ? 'delta-up' : delta < -0.0001 ? 'delta-down' : 'delta-flat';
      return h(
        'tr',
        null,
        h('td', null, h('div.metric-name', null, h('span', { text: label }), h('small', { text: hint }))),
        h('td', null, valueCell(base)),
        h('td', null, valueCell(cand), temporary ? h('span.xsmall.muted', { text: ' 临时' }) : null),
        h('td', null, h(`span.${deltaClass}`, { text: formatDelta(delta) }))
      );
    });
    return h(
      'table.overview-metrics-table',
      null,
      h(
        'thead',
        null,
        h(
          'tr',
          null,
          h('th', { text: '指标' }),
          h('th', { text: '基线' }),
          h('th', { text: '候选' }),
          h('th', { text: '差值' })
        )
      ),
      h('tbody', null, ...body)
    );
  }

  function acceptanceRules() {
    const record = state.run.decision_record || {};
    const fromRecord = record.rationale?.rules;
    if (Array.isArray(fromRecord) && fromRecord.length) return fromRecord;
    const fromRun = state.run.acceptance?.rules;
    if (Array.isArray(fromRun) && fromRun.length) return fromRun;
    return [];
  }

  function ruleList() {
    const rules = acceptanceRules();
    if (!rules.length) {
      return h('p.muted.small', {
        text: isTerminalStatus(state.run.status) ? '本次运行没有返回逐项规则判定。' : '运行结束后展示逐项接受规则判定。',
      });
    }
    return h(
      'div.rule-list',
      null,
      ...rules.map((rule) => {
        const ok = rule.ok === true;
        const skipped = rule.ok === null || rule.ok === undefined;
        const iconName = ok ? 'check' : skipped ? 'minus' : 'x';
        const tone = ok ? 'ok' : skipped ? 'skip' : 'fail';
        return h(
          `div.rule-item is-${tone}`,
          null,
          h('span.rule-item__icon', null, icon(iconName, { size: 14 })),
          h(
            'div.rule-item__body',
            null,
            h('div.rule-item__name', { text: RULE_LABELS[rule.rule] || rule.rule }),
            rule.detail ? h('div.rule-item__detail', { text: rule.detail }) : null
          )
        );
      })
    );
  }

  function executionStats() {
    const execution = state.run.execution || state.run.metrics?.execution || {};
    const stats = state.run.case_stats || {};
    const cost = state.run.cost || {};
    return kvList(
      [
        { label: '计划案例', value: formatNumber(execution.planned ?? stats.planned) },
        { label: '完成案例', value: formatNumber(execution.completed ?? stats.completed) },
        { label: '有效案例', value: formatNumber(execution.valid ?? stats.valid) },
        { label: '基础设施错误', value: formatNumber(execution.infra_errors ?? stats.errors) },
        { label: '耗时', value: formatDuration(cost.duration_ms ?? execution.duration_ms) },
        { label: 'Token', value: formatTokens(cost.total_tokens ?? cost.tokens ?? execution.tokens) },
        { label: '费用', value: formatCost(cost.amount !== undefined ? cost : null) },
        { label: '模拟返回缺失', value: formatNumber(execution.fixture_misses) },
      ],
      { compact: true }
    );
  }

  /** The three core metrics, named for what they mean rather than by letter. */
  const CORE_METRICS = [
    { key: 'routing', label: '选对技能', letter: 'R', hint: '分发准确率 R：模型选中了允许的子技能' },
    { key: 'tool_args', label: '参数正确', letter: 'A', hint: '工具入参准确率 A：业务工具与参数都对' },
    { key: 'joint', label: '整体通过', letter: 'J', hint: '联合通过率 J：技能、动作、工具、参数全部正确' },
  ];

  /** Acceptance rules that failed, rephrased as what went wrong. */
  const RULE_FAILURE_TEXT = {
    R_no_decrease: '选对技能的比例下降了',
    routing_not_decreased: '选对技能的比例下降了',
    A_no_decrease: '参数正确的比例下降了',
    tool_args_not_decreased: '参数正确的比例下降了',
    J_no_decrease: '整体通过率下降了',
    joint_not_decreased: '整体通过率下降了',
    metrics_evaluable: '有指标无法评估（数据集缺少对应案例）',
    strict_improvement: '选对技能、参数正确都没有提高',
    at_least_one_improved: '选对技能、参数正确都没有提高',
    critical_no_regression: '有关键案例从通过变成了失败',
    critical_cases_no_regression: '有关键案例从通过变成了失败',
    repeats_sufficient: '重复验证次数不够',
    no_infra_errors: '有案例因模型调用出错没能评分',
    package_integrity: '候选 Skill 包检查没有通过',
    validation_complete: '验证没有跑完',
    baseline_paired: '基线和候选的验证方式不一致',
  };

  function isBaselineRun() {
    return state.run.mode === 'baseline' || !candidateVersionId();
  }

  /**
   * Which numbers the headline shows: the validation split decides acceptance,
   * so an optimize run shows it; a baseline run shows the whole dataset.
   */
  function headlineMetrics() {
    const metrics = state.run.metrics || {};
    const validation = metrics.validation;
    if (!isBaselineRun() && validation?.baseline && validation?.candidate) {
      const repeats = Number(validation.repeats ?? state.run.acceptance?.validation_repeats ?? 0) || null;
      const total = validation.baseline?.joint?.total_cases;
      const unique = total && repeats ? Math.round(total / repeats) : null;
      return {
        baseline: validation.baseline,
        candidate: validation.candidate,
        delta: validation.delta || {},
        note: unique
          ? `按验证集统计：${unique} 个没参与优化的案例，每个跑 ${repeats} 次`
          : '按验证集（没参与优化的案例）统计',
      };
    }
    return {
      baseline: metrics.baseline,
      candidate: isBaselineRun() ? null : metrics.candidate,
      delta: metrics.delta || {},
      note: isBaselineRun() ? '当前版本在全部案例上的表现' : '按优化集统计',
    };
  }

  function metricValue(block) {
    if (!block || block.status === 'not_evaluated' || block.value === null || block.value === undefined) return null;
    return block.value;
  }

  function metricStrip() {
    const { baseline, candidate, delta, note } = headlineMetrics();
    if (!baseline && !candidate) return null;
    const temporary = !isTerminalStatus(state.run.status);
    const tiles = CORE_METRICS.map((metric) => {
      const before = metricValue(baseline?.[metric.key]);
      const after = metricValue(candidate?.[metric.key]);
      const shown = candidate ? after : before;
      const counts = (candidate || baseline)?.[metric.key];
      const given = candidate ? delta?.[metric.key] : null;
      const diff = given ?? (candidate && before !== null && after !== null ? after - before : null);
      const tone = diff === null || diff === undefined ? '' : diff > 0.0001 ? ' is-up' : diff < -0.0001 ? ' is-down' : '';
      return h(
        `div.result-metric${tone}`,
        { title: metric.hint },
        h('div.result-metric__label', null, h('span', { text: metric.label }), h('span.result-metric__letter', { text: metric.letter })),
        h('div.result-metric__value', { text: shown === null ? '未评估' : formatPercent(shown) }),
        candidate
          ? h(
              'div.result-metric__change',
              null,
              h('span', { text: `原来 ${before === null ? '未评估' : formatPercent(before)}` }),
              diff !== null && diff !== undefined ? h('span.result-metric__delta', { text: formatDelta(diff) }) : null
            )
          : h('div.result-metric__change', { text: counts?.evaluated ? `${counts.passed ?? 0} / ${counts.evaluated} 个案例` : '—' })
      );
    });
    return h(
      'div.result-metrics',
      null,
      h('div.result-metrics__grid', null, ...tiles),
      h('p.result-metrics__note', null, note, temporary ? '（运行未结束，数值可能变化）' : '', coverageHint())
    );
  }

  function failedRuleTexts() {
    return acceptanceRules()
      .filter((rule) => rule.ok === false)
      .map((rule) => ({ text: RULE_FAILURE_TEXT[rule.rule] || RULE_LABELS[rule.rule] || rule.rule, detail: rule.detail || '' }));
  }

  function applyState() {
    const run = state.run;
    const candVersion = candidateVersionId();
    const applied = Boolean(
      candVersion &&
        ((run.current_skill_version_id && run.current_skill_version_id === candVersion) ||
          (!run.current_skill_version_id && state.project?.current_skill_version_id === candVersion))
    );
    const appliedAt = run.acceptance?.applied_at || run.decision_record?.applied_at;
    const applicability = run.applicability || {};
    const isCurrent = applicability.current !== false && !(applicability.stale_reasons || []).length;
    return {
      candVersion,
      applied,
      appliedElsewhere: Boolean(appliedAt && candVersion && !applied),
      isCurrent,
      canApply: run.can_apply !== false && isCurrent && !applied,
      staleMessage: applicability.message || '评测之后项目配置有变化，需要重新评测。',
    };
  }

  /** Headline: what happened, in one sentence, plus what to do next. */
  function verdict() {
    const run = state.run;
    const counts = computeTransitions();
    const base = baselineCounts();
    const { baseline, candidate } = headlineMetrics();
    const jointBefore = metricValue(baseline?.joint);
    const jointAfter = metricValue(candidate?.joint);
    const jointText = jointBefore !== null && jointAfter !== null ? `整体通过率 ${formatPercent(jointBefore)} → ${formatPercent(jointAfter)}` : '';
    const decisionErrorCode = run.decision_record?.rationale?.error_code || run.acceptance?.error_code;

    if (!isTerminalStatus(run.status)) {
      return { tone: 'info', title: '正在运行', text: '运行结束后给出结论；下面的数字会随进度更新。' };
    }
    if (['failed', 'cancelled', 'interrupted'].includes(run.status)) {
      return {
        tone: run.status === 'failed' ? 'danger' : 'warning',
        title: { failed: '运行失败', cancelled: '运行已取消', interrupted: '运行已中断' }[run.status],
        text: run.error_message || run.decision_reason || '运行没有产生可应用的候选，可在运行记录里查看原因。',
      };
    }
    if (decisionErrorCode === 'no_failures') {
      return { tone: 'success', title: '没有发现需要改进的问题', text: run.decision_reason || '当前案例都已通过，优化器没有可依据的失败案例。' };
    }
    if (isBaselineRun()) {
      const total = base.passed + base.failed;
      return {
        tone: base.failed ? 'neutral' : 'success',
        title: base.failed ? `${total} 个案例中 ${base.failed} 个没通过` : `全部 ${total} 个案例都通过了`,
        text: base.failed ? '可以启动单轮优化，让优化器根据这些失败案例修改 Skill。' : '当前版本在这个数据集上没有失败案例。',
      };
    }
    if (run.decision === 'accept') {
      const { applied, appliedElsewhere } = applyState();
      const regress = counts.regressed ? `，但有 ${counts.regressed} 个案例变差，建议先看一下` : '，没有案例变差';
      return {
        tone: 'success',
        title: applied ? '候选已应用' : appliedElsewhere ? '候选曾应用，当前已换成其他版本' : '候选更好，建议应用',
        text: `${jointText || '核心指标提升'}${regress}。`,
      };
    }
    if (run.decision === 'reject') {
      const failed = failedRuleTexts();
      return {
        tone: 'danger',
        title: '候选没有变好，不建议应用',
        text: failed.length ? `${failed.map((item) => item.text).join('；')}。` : run.decision_reason || '候选未满足接受规则。',
      };
    }
    if (run.decision === 'inconclusive') {
      const failed = failedRuleTexts();
      return {
        tone: 'warning',
        title: '暂时无法判断',
        text: failed.length ? `${failed.map((item) => item.text).join('；')}。` : run.decision_reason || '候选没有生成或验证不完整。',
      };
    }
    return { tone: 'neutral', title: '运行结束', text: run.decision_reason || '' };
  }

  function verdictActions() {
    const run = state.run;
    if (!isTerminalStatus(run.status)) return [];
    const { candVersion, applied, appliedElsewhere, isCurrent, canApply, staleMessage } = applyState();
    const exportBtn = candVersion
      ? button({ label: '导出候选包', variant: 'ghost', iconName: 'download', onClick: () => download(api.exportSkillVersionUrl(candVersion), `${candVersion}.zip`) })
      : null;
    if (run.decision === 'accept') {
      return [
        candVersion && !applied
          ? button({ label: canApply ? (state.applyBusy ? '正在应用…' : '应用候选') : '需重新评测后应用', variant: canApply ? 'primary' : 'secondary', iconName: canApply ? 'check' : 'lock', disabled: state.applyBusy || !canApply, onClick: applyCandidate })
          : null,
        applied ? button({ label: '继续下一轮', variant: 'primary', iconName: 'play', onClick: continueNextRound }) : null,
        button({ label: '看改了什么', variant: 'secondary', iconName: 'versions', onClick: () => switchTab('diff') }),
        exportBtn,
        applied ? button({ label: '恢复应用前版本', variant: 'ghost', iconName: 'refresh', onClick: restoreAppliedVersion }) : null,
        !isCurrent && !applied ? h('span.small.muted', { text: staleMessage }) : null,
        appliedElsewhere ? h('span.small.muted', { text: '当前版本已变化，不能从本轮候选重复恢复。' }) : null,
      ];
    }
    if (run.decision === 'reject' || run.decision === 'inconclusive') {
      const counts = computeTransitions();
      return [
        counts.regressed
          ? button({ label: `看变差的 ${counts.regressed} 个案例`, variant: 'primary', iconName: 'alert', onClick: () => openCases('regressed') })
          : button({ label: '看失败案例', variant: 'primary', iconName: 'alert', onClick: () => openCases('failed') }),
        run.decision === 'inconclusive' ? button({ label: '回概览重新验证', variant: 'secondary', iconName: 'play', onClick: createVerificationRun }) : null,
        candVersion ? button({ label: '看改了什么', variant: 'ghost', iconName: 'versions', onClick: () => switchTab('diff') }) : null,
      ];
    }
    if (isBaselineRun() && run.status === 'completed') {
      return [
        baselineCounts().failed ? button({ label: '看失败案例', variant: 'primary', iconName: 'alert', onClick: () => openCases('failed') }) : null,
        button({ label: '回项目概览', variant: baselineCounts().failed ? 'secondary' : 'primary', iconName: 'arrowLeft', href: `#/projects/${encodeURIComponent(run.project_id)}/overview` }),
      ];
    }
    return [
      button({ label: '查看运行记录', variant: 'primary', iconName: 'run', onClick: () => switchTab('log') }),
      button({ label: '回项目概览', variant: 'ghost', iconName: 'arrowLeft', href: `#/projects/${encodeURIComponent(run.project_id)}/overview` }),
    ];
  }

  function verdictCard() {
    const v = verdict();
    const actions = verdictActions().filter(Boolean);
    return h(
      `section.result-verdict.result-verdict--${v.tone}`,
      null,
      h('h2.result-verdict__title', { text: v.title }),
      v.text ? h('p.result-verdict__text', { text: v.text }) : null,
      metricStrip(),
      actions.length ? h('div.btn-row.btn-row--start.result-verdict__actions', null, ...actions) : null
    );
  }

  function openCases(filter) {
    state.cases.filter = filter;
    switchTab('cases');
  }

  /** Clickable case-change tiles; every tile opens the case list filtered. */
  function caseChangeCard() {
    const hasCaseData = state.cases.loaded && state.cases.items.length > 0;
    let body;
    if (!hasCaseData) {
      body = h('p.muted.small', { text: state.cases.loading ? '正在统计案例结果…' : '暂时没有可统计的案例结果。' });
    } else {
      const tiles = caseFilterOptions().filter((option) => option.id !== 'all');
      body = h(
        'div.change-tiles',
        null,
        ...tiles.map((option) =>
          h(
            `button.change-tile.change-tile--${option.tone}${option.count ? '' : ' is-empty'}`,
            { type: 'button', onclick: () => openCases(option.id), disabled: !option.count },
            h('span.change-tile__value', { text: String(option.count) }),
            h('span.change-tile__label', { text: option.label }),
            h('span.change-tile__hint', { text: option.hint })
          )
        )
      );
    }
    return sectionCard({
      title: '案例结果',
      description: isBaselineRun() ? '点击数字查看对应案例和失败原因。' : '基线 → 候选，每个案例只算一次（验证集取第一次）。点击数字查看。',
      body: h('div.stack', null, body, hasCaseData ? failureBreakdown() : null),
    });
  }

  /** Top failure reasons among the cases that still fail. */
  function failureBreakdown() {
    const sideKey = isBaselineRun() ? 'baseline' : 'candidate';
    const counts = new Map();
    for (const item of state.cases.items) {
      const explained = explainCaseSide(item[sideKey]);
      if (!explained || explained.passed) continue;
      // Count each case once, by its most decisive reason.
      const group = explained.reasons[0]?.group || 'error';
      counts.set(group, (counts.get(group) || 0) + 1);
    }
    if (!counts.size) return null;
    const rows = Object.keys(FAILURE_GROUPS)
      .filter((group) => counts.get(group))
      .map((group) => h('li', null, h('span', { text: FAILURE_GROUPS[group] }), h('strong', { text: `${counts.get(group)} 个` })));
    return h(
      'div.failure-breakdown',
      null,
      h('div.failure-breakdown__title', { text: isBaselineRun() ? '没通过的主要原因（每个案例按最主要的一条计）' : '候选仍没通过的主要原因' }),
      h('ul.failure-breakdown__list', null, ...rows)
    );
  }

  /** What the optimizer changed and why, one paragraph; details live in 文件差异. */
  function changeSummaryCard() {
    if (isBaselineRun()) return null;
    const candidate = state.run.candidate || {};
    const diagnosis = candidate.diagnosis || {};
    const why = diagnosis.summary || diagnosis.reason || (typeof diagnosis === 'string' ? diagnosis : '');
    const files = candidate.changed_files || [];
    const hypotheses = (candidate.hypotheses || []).length;
    if (!why && !files.length) return null;
    return sectionCard({
      title: '改了什么',
      actions: [button({ label: '查看文件差异', variant: 'ghost', size: 'sm', iconName: 'versions', onClick: () => switchTab('diff') })],
      body: h(
        'div.stack.stack--sm',
        null,
        why ? h('p.small', { text: why }) : null,
        files.length
          ? h('p.small.muted', { text: `改了 ${files.length} 个文件${hypotheses ? `、${hypotheses} 处` : ''}：${files.join('、')}` })
          : null
      ),
    });
  }

  async function loadResult() {
    if (state.resultLoaded && !state.resultError) {
      renderResult();
      return;
    }
    state.resultError = null;
    renderResultLoading();
    try {
      await loadCases({ silent: true });
    } catch {
      /* cases errors render in cases tab; result can still show metrics */
    }
    state.resultLoaded = true;
    renderResult();
  }

  function baselineCounts() {
    const passed = state.cases.items.filter((item) => item.baseline?.passed === true).length;
    const failed = state.cases.items.filter((item) => item.baseline?.passed === false).length;
    return { passed, failed };
  }

  function renderResultLoading() {
    mount(resultHost, loadingState('正在汇总运行结果…'));
  }

  function coverageHint() {
    const metrics = state.run.metrics || {};
    const blocks = [metrics.baseline?.routing, metrics.baseline?.tool_args, metrics.candidate?.routing, metrics.candidate?.tool_args];
    const notEvaluated = blocks.some((block) => block && block.status === 'not_evaluated');
    if (!notEvaluated) return null;
    return h(
      'span.coverage-hint',
      null,
      '「未评估」表示数据集里没有对应的有效案例，不会按满分计。',
      h('a', { href: `#/projects/${encodeURIComponent(state.run.project_id)}/dataset` }, '检查数据集覆盖')
    );
  }

  function renderResult() {
    if (state.resultError) {
      mount(resultHost, errorState(state.resultError, { title: '无法加载结果概览', retry: () => loadResult() }));
      return;
    }
    const run = state.run;
    const metrics = run.metrics || {};
    const temporary = Boolean(metrics.temporary) || !isTerminalStatus(run.status);
    const reason = run.decision_reason || run.decision_record?.rationale?.reason || '';
    const validation = metrics.validation;
    const metricDetails = h(
      'details.result-details',
      null,
      h('summary', { text: '更多：完整指标、判定规则、成本' }),
      h(
        'div.stack',
        null,
        reason ? h('p.small.muted', null, h('span', { text: '判定说明：' }), reason) : null,
        h(
          'div.overview-grid',
          null,
          sectionCard({
            title: validation?.baseline ? '优化集指标' : '指标',
            description: temporary ? '运行尚未结束，候选指标为临时值。' : '优化器看得到的案例上的结果。',
            actions: temporary ? [chip('临时值', 'warning', { iconName: 'clock' })] : [],
            body: metricsTable(metrics, temporary),
          }),
          validation?.baseline
            ? sectionCard({ title: '验证集指标', description: '没参与优化的案例，重复执行后汇总；结论按这一组判定。', body: metricsTable(validation, false) })
            : null
        ),
        isBaselineRun() ? null : sectionCard({ title: '判定规则', description: '规则在运行前冻结，不会为当前候选临时调整。', body: ruleList() }),
        sectionCard({ title: '执行与成本', body: executionStats() })
      )
    );
    mount(resultHost, h('div.stack', null, verdictCard(), caseChangeCard(), changeSummaryCard(), metricDetails));
  }

  async function applyCandidate() {
    if (state.applyBusy) return;
    const candVersion = candidateVersionId();
    if (!candVersion) return;
    if (state.run.can_apply === false || state.run.applicability?.current === false) {
      toast(state.run.applicability?.message || '当前评测条件已变化，需要重新评测后才能应用。', { type: 'warning' });
      return;
    }
    let currentVersion = state.run.current_skill_version_id || state.project?.current_skill_version_id || null;
    try {
      const fresh = await api.getProject(state.run.project_id);
      currentVersion = fresh.current_skill_version_id || currentVersion;
      state.project = fresh;
    } catch {
      /* use cached pointer; the server still validates */
    }
    const changedFiles = state.run.candidate?.changed_files || [];
    const ok = await confirmDialog({
      title: '应用候选版本',
      message: '应用前会校验项目当前版本是否仍等于本轮基线；若已变更会返回冲突，不会自动覆盖。应用只切换平台当前版本指针，不修改外部生产目录。',
      details: kvList(
        [
          { label: '父版本', value: state.run.snapshot?.skill_version?.label || baselineVersionId() || '—', mono: true },
          { label: '候选版本', value: candVersion, mono: true },
          { label: '变更文件', value: `${changedFiles.length} 个${changedFiles.length ? `：${changedFiles.join('、')}` : ''}` },
          { label: '当前项目版本', value: currentVersion || '—', mono: true },
        ],
        { compact: true }
      ),
      confirmText: '应用候选',
    });
    if (!ok) return;
    state.applyBusy = true;
    renderResult();
    try {
      const result = await api.applyRun(state.run.id, {
        expectedCurrentVersion: currentVersion,
        candidateId: state.run.candidate?.id,
      });
      toast('候选已应用，项目当前版本已更新', { type: 'success' });
      if (state.project) state.project.current_skill_version_id = result?.project?.current_skill_version_id || candVersion;
      await refreshRun({ forceTab: true });
      await ctx.refreshShell();
    } catch (error) {
      if (error.status === 409 || error.code === 'conflict') {
        showConflict(error);
      } else {
        toast(`应用失败：${error.message}`, { type: 'error' });
      }
    } finally {
      state.applyBusy = false;
      renderResult();
    }
  }

  function showConflict(error) {
    const details = error.details || {};
    const modal = openModal({
      title: '版本冲突，候选未应用',
      size: 'md',
      body: h(
        'div.stack',
        null,
        h('p', { text: error.message || '项目当前版本在本轮运行后已经变更，不能自动覆盖。' }),
        kvList([
          { label: '运行基线版本', value: baselineVersionId() || '—', mono: true },
          { label: '项目当前版本', value: details.current_version || state.project?.current_skill_version_id || '—', mono: true },
          { label: '请求期望版本', value: details.expected_current_version || '—', mono: true },
        ]),
        h('p.small.muted', { text: '请刷新后重新比较版本差异，再决定是否应用。' })
      ),
      footer: h(
        'div.btn-row',
        null,
        button({ label: '关闭', variant: 'ghost', onClick: () => modal.close() }),
        button({
          label: '刷新并重新比较',
          variant: 'primary',
          iconName: 'refresh',
          onClick: async () => {
            modal.close();
            await refreshRun({ forceTab: true });
            await ctx.refreshShell();
            switchTab('diff');
            toast('已刷新项目当前版本，请重新确认差异', { type: 'info' });
          },
        })
      ),
    });
  }

  async function createVerificationRun() {
    continueNextRound();
  }

  function continueNextRound() {
    ctx.navigate(`#/projects/${encodeURIComponent(state.run.project_id)}/overview`);
    toast('已返回项目概览，请确认配置后再启动下一轮；不会自动发起模型调用。', { type: 'info' });
  }

  async function restoreAppliedVersion() {
    const previousVersion = baselineVersionId();
    const candidateVersion = candidateVersionId();
    if (!previousVersion || !candidateVersion) return;
    let currentVersion = state.project?.current_skill_version_id || state.run.current_skill_version_id || null;
    try {
      const fresh = await api.getProject(state.run.project_id);
      state.project = fresh;
      currentVersion = fresh.current_skill_version_id || null;
    } catch (error) {
      toast(`无法确认项目当前版本：${error.message}`, { type: 'error' });
      return;
    }
    if (currentVersion !== candidateVersion) {
      showConflict({
        message: '项目当前版本已不是本轮已采用候选，不能静默恢复。',
        details: { current_version: currentVersion, expected_current_version: candidateVersion },
      });
      return;
    }
    const ok = await confirmDialog({
      title: '恢复应用前版本',
      message: '将把项目当前版本恢复为本轮应用前的版本。恢复前会再次校验当前版本仍是本轮候选；不会删除版本内容。',
      details: kvList([
        { label: '应用前版本', value: previousVersion, mono: true },
        { label: '当前候选版本', value: candidateVersion, mono: true },
      ], { compact: true }),
      confirmText: '确认恢复',
      danger: true,
    });
    if (!ok) return;
    try {
      const restored = await api.activateSkillVersion(previousVersion, candidateVersion);
      if (state.project) state.project.current_skill_version_id = restored?.project?.current_skill_version_id || previousVersion;
      toast('已恢复应用前版本', { type: 'success' });
      await refreshRun({ forceTab: true });
      if (ctx.refreshShell) await ctx.refreshShell();
    } catch (error) {
      if (error.status === 409 || error.code === 'conflict' || error.code === 'version_conflict') showConflict(error);
      else toast(`恢复失败：${error.message}`, { type: 'error' });
    }
  }

  async function cancelRun() {
    const ok = await confirmDialog({
      title: '取消运行',
      message: '取消后会停止派发新案例，并尽力终止在途请求。已经产生的模型调用费用不会回退。后台任务状态会独立更新。',
      confirmText: '取消运行',
      danger: true,
    });
    if (!ok) return;
    state.busyCancel = true;
    renderHeader();
    try {
      await api.cancelRun(state.run.id);
      toast('已请求取消运行', { type: 'info' });
      await refreshRun({ forceTab: false });
    } catch (error) {
      toast(`取消失败：${error.message}`, { type: 'error' });
    } finally {
      state.busyCancel = false;
      renderHeader();
    }
  }

  /* ---------------- cases tab ---------------- */

  /** Filter chips for the case list, with counts; also used by the result tiles. */
  function caseFilterOptions() {
    const items = state.cases.items;
    const count = (predicate) => items.filter(predicate).length;
    if (isBaselineRun()) {
      return [
        { id: 'all', label: '全部', count: items.length, tone: 'neutral', hint: '' },
        { id: 'failed', label: '没通过', count: count((item) => item.baseline?.passed === false), tone: 'bad', hint: '点开看失败原因' },
        { id: 'pass', label: '通过', count: count((item) => item.baseline?.passed === true), tone: 'good', hint: '结果与预期一致' },
      ];
    }
    return [
      { id: 'all', label: '全部', count: items.length, tone: 'neutral', hint: '' },
      { id: 'fixed', label: '修好了', count: count((item) => item.changeType === 'fixed'), tone: 'good', hint: '基线失败 → 候选通过' },
      { id: 'regressed', label: '变差了', count: count((item) => item.changeType === 'regressed'), tone: 'bad', hint: '基线通过 → 候选失败' },
      { id: 'still_fail', label: '仍然失败', count: count((item) => item.changeType === 'unchanged_fail'), tone: 'warn', hint: '两边都没通过' },
      { id: 'pass', label: '一直通过', count: count((item) => item.changeType === 'unchanged_pass'), tone: 'neutral', hint: '两边都通过' },
    ];
  }

  function matchesFilter(item, filter) {
    switch (filter) {
      case 'fixed': return item.changeType === 'fixed';
      case 'regressed': return item.changeType === 'regressed';
      case 'still_fail': return item.changeType === 'unchanged_fail';
      case 'pass': return isBaselineRun() ? item.baseline?.passed === true : item.changeType === 'unchanged_pass';
      case 'failed':
        return isBaselineRun()
          ? item.baseline?.passed === false
          : item.changeType === 'regressed' || item.changeType === 'unchanged_fail' || item.candidate?.passed === false;
      default: return true;
    }
  }

  function filteredCases() {
    const filter = state.cases.filter || 'all';
    let items = state.cases.items.filter((item) => matchesFilter(item, filter));
    const needle = state.cases.search.trim().toLowerCase();
    if (needle) {
      items = items.filter((item) => (item.case_id || '').toLowerCase().includes(needle) || (item.query || '').toLowerCase().includes(needle));
    }
    const rank = (item) => (isBaselineRun() ? (item.baseline?.passed === false ? 0 : 1) : changeRank(item.changeType));
    items.sort((a, b) => rank(a) - rank(b) || String(a.case_id).localeCompare(String(b.case_id)));
    return items;
  }

  /** One-line "why" for a case row: the failing side's first reason in plain words. */
  function reasonCell(item) {
    const baseline = explainCaseSide(item.baseline);
    const candidate = explainCaseSide(item.candidate);
    let prefix = '';
    let explained = null;
    if (isBaselineRun()) {
      explained = baseline;
    } else if (item.changeType === 'fixed') {
      prefix = '原来：';
      explained = baseline;
    } else {
      explained = candidate && !candidate.passed ? candidate : null;
    }
    if (!explained || explained.passed || !explained.reasons.length) {
      return h('td.case-reason.muted', { text: item.changeType === 'pending' ? '等待结果' : '—' });
    }
    const [first, ...rest] = explained.reasons;
    return h(
      `td.case-reason${item.changeType === 'fixed' ? ' is-fixed' : ''}`,
      { title: explained.reasons.map((reason) => `${reason.text}${reason.basis ? `\n  依据：${reason.basis}` : ''}`).join('\n') },
      h('span', { text: `${prefix}${first.text}` }),
      rest.length ? h('span.case-reason__more', { text: ` 等 ${explained.reasons.length} 处` }) : null,
      first.basis ? h('div.case-reason__basis', { text: `依据：${truncate(first.basis, 80)}` }) : null
    );
  }

  function renderCases() {
    if (state.cases.loading && !state.cases.items.length) {
      mount(casesHost, loadingState('正在加载案例对比…'));
      return;
    }
    if (state.cases.error) {
      mount(casesHost, errorState(state.cases.error, { title: '无法加载案例对比', retry: () => loadCases({ force: true }) }));
      return;
    }
    const options = caseFilterOptions();
    if (!options.some((option) => option.id === state.cases.filter)) state.cases.filter = 'all';
    const searchInput = h('input.input', {
      type: 'search',
      placeholder: '搜索案例编号或问题',
      value: state.cases.search,
      'aria-label': '搜索案例',
      oninput: (event) => {
        state.cases.search = event.target.value;
        renderCasesTable();
      },
    });
    const filterBar = h(
      'div.segmented.case-filter',
      { role: 'tablist', 'aria-label': '筛选案例' },
      ...options.map((option) =>
        h(
          `button.segmented__btn${state.cases.filter === option.id ? ' is-active' : ''}`,
          {
            type: 'button',
            role: 'tab',
            'aria-selected': String(state.cases.filter === option.id),
            onclick: () => {
              state.cases.filter = option.id;
              renderCases();
            },
          },
          option.label,
          h(`span.case-filter__count.case-filter__count--${option.tone}`, { text: String(option.count) })
        )
      )
    );

    mount(
      casesHost,
      h(
        'div',
        null,
        h(
          'div.case-toolbar',
          null,
          filterBar,
          h('span.case-toolbar__spacer'),
          !isTerminalStatus(state.run.status) ? chip('结果可能仍会变化', 'warning', { iconName: 'clock' }) : null,
          h('div.search-field', null, icon('search', { size: 15 }), searchInput),
          !isTerminalStatus(state.run.status)
            ? button({ label: '刷新', variant: 'ghost', size: 'sm', iconName: 'refresh', onClick: () => loadCases({ force: true }) })
            : null
        ),
        h('div', { id: 'cases-table-host' })
      )
    );
    renderCasesTable();
  }

  function renderCasesTable(items = filteredCases()) {
    const host = document.getElementById('cases-table-host');
    if (!host) return;
    if (!state.cases.items.length) {
      mount(
        host,
        emptyState({
          iconName: 'run',
          title: '还没有案例结果',
          description: isTerminalStatus(state.run.status)
            ? '本次运行没有返回案例级结果。请查看运行记录定位原因。'
            : '案例结果会在执行过程中逐步出现，可稍后刷新或查看运行记录。',
          actions: [{ label: '查看运行记录', variant: 'secondary', iconName: 'run', onClick: () => switchTab('log') }],
        })
      );
      return;
    }
    if (!items.length) {
      mount(
        host,
        emptyState({
          iconName: 'search',
          title: '没有匹配的案例',
          description: '换一个筛选，或清空搜索词。',
          actions: [
            {
              label: '显示全部',
              variant: 'secondary',
              onClick: () => {
                state.cases.filter = 'all';
                state.cases.search = '';
                renderCases();
              },
            },
          ],
        })
      );
      return;
    }
    const baselineOnly = isBaselineRun();
    const open = (item) => openCaseDrawer(item.case_id);
    mount(
      host,
      h(
        'div.table-wrap',
        null,
        h(
          'table.data-table.case-table',
          null,
          h(
            'thead',
            null,
            h(
              'tr',
              null,
              h('th', { text: '案例' }),
              baselineOnly ? h('th.case-table__status', { text: '结果' }) : h('th.case-table__status', { text: '基线' }),
              baselineOnly ? null : h('th.case-table__status', { text: '候选' }),
              h('th', { text: '原因' })
            )
          ),
          h(
            'tbody',
            null,
            ...items.map((item) =>
              h(
                'tr.is-clickable',
                {
                  tabindex: '0',
                  onclick: () => open(item),
                  onkeydown: (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      open(item);
                    }
                  },
                },
                h(
                  'td.case-table__case',
                  null,
                  h('div.cell-strong', { text: truncate(item.query || '', 90) }),
                  h(
                    'div.case-table__meta',
                    null,
                    h('span.mono', { text: item.case_id }),
                    item.split === 'validation' ? h('span', { text: '验证集' }) : null,
                    item.critical ? chip('关键', 'warning') : null
                  )
                ),
                h('td.case-table__status', null, sideBadge(item.baseline)),
                baselineOnly ? null : h('td.case-table__status', null, sideBadge(item.candidate)),
                reasonCell(item)
              )
            )
          )
        )
      )
    );
  }

  function deriveChangeType(baseline, candidate) {
    if (baseline && candidate) {
      if (baseline.passed && candidate.passed) return 'unchanged_pass';
      if (!baseline.passed && candidate.passed) return 'fixed';
      if (baseline.passed && !candidate.passed) return 'regressed';
      return 'unchanged_fail';
    }
    if (baseline || candidate) return 'pending';
    return 'pending';
  }

  async function mergeValidationPhases(items) {
    const pending = items.filter((item) => !item.baseline || !item.candidate);
    if (!pending.length) return items;
    try {
      const [baselinePayload, candidatePayload] = await Promise.all([
        api.getRunCases(state.run.id, { phase: 'validation_baseline', limit: 200 }),
        api.getRunCases(state.run.id, { phase: 'validation_candidate', limit: 200 }),
      ]);
      const hasSideData = (side) =>
        Boolean(
          side &&
            (side.passed !== undefined ||
              side.status ||
              side.selected_skills ||
              side.tool_calls ||
              side.error_code ||
              side.action)
        );
      const baselineById = new Map();
      for (const raw of asItems(baselinePayload)) {
        const item = normalizeCaseItem(raw);
        if ((item.repeat_index || 1) !== 1) continue;
        const side = item.baseline || (hasSideData(item) ? item : null);
        if (side) baselineById.set(item.case_id, side);
      }
      const candidateById = new Map();
      for (const raw of asItems(candidatePayload)) {
        const item = normalizeCaseItem(raw);
        if ((item.repeat_index || 1) !== 1) continue;
        const side = item.candidate || (hasSideData(item) ? item : null);
        if (side) candidateById.set(item.case_id, side);
      }
      for (const item of items) {
        if (item.baseline && item.candidate) continue;
        const baseline = item.baseline || baselineById.get(item.case_id) || null;
        const candidate = item.candidate || candidateById.get(item.case_id) || null;
        if (baseline || candidate) {
          item.baseline = baseline;
          item.candidate = candidate;
          item.changeType = deriveChangeType(baseline, candidate);
          const diags = [...(baseline?.diagnostics || []), ...(candidate?.diagnostics || [])];
          if (diags.length) item.diagnostics = diags;
        }
      }
    } catch {
      /* phase-specific merge is best-effort */
    }
    return items;
  }

  async function loadCases({ force = false, silent = false } = {}) {
    if (state.cases.loading) return;
    if (state.cases.loaded && !force) {
      renderCases();
      return;
    }
    state.cases.loading = true;
    state.cases.error = null;
    if (!silent) renderCases();
    try {
      const payload = await api.getRunCases(state.run.id, { limit: 200, offset: 0 });
      let items = asItems(payload).map(normalizeCaseItem);
      items = await mergeValidationPhases(items);
      state.cases.items = items;
      state.cases.loaded = true;
    } catch (error) {
      state.cases.error = error;
    } finally {
      state.cases.loading = false;
      if (state.activeTab === 'cases') renderCases();
      if (state.activeTab === 'result' && state.resultLoaded) renderResult();
      renderTabs();
    }
  }

  async function openCaseDrawer(caseId) {
    const drawer = openDrawer({
      title: `案例 ${caseId}`,
      subtitle: '为什么通过或没通过，以及预期与实际的对照。',
      size: 'xl',
      body: loadingState('正在加载案例详情…'),
      footer: h(
        'div.btn-row.btn-row--between',
        null,
        h(
          'div.btn-row',
          null,
          isBaselineRun()
            ? button({ label: '再跑一次', variant: 'secondary', iconName: 'play', onClick: () => trialCase(caseId, 'baseline') })
            : button({ label: '用候选版本再跑', variant: 'secondary', iconName: 'play', onClick: () => trialCase(caseId, 'candidate') }),
          isBaselineRun() ? null : button({ label: '用基线版本再跑', variant: 'ghost', iconName: 'play', onClick: () => trialCase(caseId, 'baseline') })
        ),
        button({
          label: '在运行记录中查看',
          variant: 'ghost',
          iconName: 'run',
          onClick: () => {
            drawer.close();
            switchTab('log');
            if (state.log) state.log.filterByCase(caseId);
          },
        }),
        button({ label: '关闭', variant: 'secondary', onClick: () => drawer.close() })
      ),
    });
    try {
      const [rawDetail, history] = await Promise.all([
        api.getRunCase(state.run.id, caseId),
        api.getEventHistory(state.run.id, { caseId, limit: 200 }).catch(() => ({ items: [] })),
      ]);
      const rowItem = state.cases.items.find((item) => item.case_id === caseId) || {};
      const detail = { ...rowItem, ...rawDetail, ...(rawDetail.case || {}) };
      let expected = detail.expected;
      if (!expected && state.run.snapshot?.dataset?.id) {
        try {
          const datasetCase = await api.getDatasetCase(state.run.snapshot.dataset.id, caseId);
          expected = datasetCase.expected;
        } catch {
          /* expected is optional in the drawer */
        }
      }
      drawer.setBody(buildCaseDetail({ ...detail, expected }, asItems(history)));
    } catch (error) {
      drawer.setBody(errorState(error, { title: '无法加载案例详情' }));
    }
  }

  function buildCaseDetail(detail, events) {
    const baselineOnly = isBaselineRun();
    const sides = baselineOnly ? [['结果', detail.baseline]] : [['基线', detail.baseline], ['候选', detail.candidate]];
    return h(
      'div.case-detail',
      null,
      h(
        'div.case-section',
        null,
        h('div.case-detail__query', { text: detail.query || '—' }),
        h(
          'div.row',
          { style: { marginTop: '8px', gap: '6px' } },
          chip(splitLabel(detail.split), 'neutral'),
          detail.critical ? chip('关键案例', 'warning', { iconName: 'alert' }) : null,
          !baselineOnly && detail.changeType ? statusBadge(detail.changeType, 'change') : null
        ),
        verdictRows(sides)
      ),
      h(
        'div.case-section',
        null,
        h('div.case-section__title', null, h('span', { text: '预期与实际' })),
        h(
          `div.compare-columns${baselineOnly ? '' : ' compare-columns--3'}`,
          null,
          expectedColumn(detail.expected),
          actualColumn(detail.baseline, baselineOnly ? '实际' : '基线'),
          baselineOnly ? null : actualColumn(detail.candidate, '候选')
        )
      ),
      judgmentsBlock(sides),
      timelineDetails(events),
      jsonViewer(detail.expected || {}, { label: '预期结果 JSON', maxHeight: '240px', redact: false })
    );
  }

  /** Re-run one case of this run right now, with the baseline or candidate Skill. */
  function trialCase(caseId, variant) {
    const label = variant === 'candidate' ? '候选版本' : isBaselineRun() ? '本次的 Skill 版本' : '基线版本';
    openTrialDrawer({
      title: `试跑 ${caseId}`,
      context: `用${label}和本次运行的执行环境、数据集重跑这个案例`,
      runTrial: (options) => api.trialRunCase(state.run.id, { case_id: caseId, variant }, options),
    });
  }

  /* ---------------- diff tab ---------------- */

  async function loadDiff({ force = false } = {}) {
    if (state.diff.loading) return;
    if (state.diff.loaded && !force) {
      renderDiff();
      return;
    }
    state.diff.loading = true;
    state.diff.error = null;
    renderDiff();
    try {
      const payload = await api.getRunDiff(state.run.id);
      const changes = payload?.changes || payload?.items || [];
      state.diff.changes = changes;
      state.diff.hypotheses = payload?.hypotheses || state.run.candidate?.hypotheses || [];
      state.diff.diagnosis = payload?.diagnosis || state.run.candidate?.diagnosis || null;
      state.diff.expectedEffect = payload?.expected_effect || state.run.candidate?.expected_effect || '';
      state.diff.riskCases = payload?.risk_cases || state.run.candidate?.risk_cases || [];
      state.diff.relatedCases = payload?.related_cases || payload?.diagnosis?.related_cases || [];
      state.diff.selected = 0;
      state.diff.loaded = true;
    } catch (error) {
      state.diff.error = error;
    } finally {
      state.diff.loading = false;
      renderDiff();
    }
  }

  function renderDiff() {
    if (state.diff.loading && !state.diff.loaded) {
      mount(diffHost, loadingState('正在加载文件差异…'));
      return;
    }
    if (state.diff.error) {
      mount(diffHost, errorState(state.diff.error, { title: '无法加载文件差异', retry: () => loadDiff({ force: true }) }));
      return;
    }
    const changes = state.diff.changes;
    const hypotheses = state.diff.hypotheses || [];
    const riskCases = state.diff.riskCases || [];
    const relatedCases = state.diff.relatedCases || [];
    const headerSections = [
      hypotheses.length
        ? sectionCard({
            title: '修改假设',
            description: state.diff.diagnosis?.summary || '',
            body: h(
              'div.hypotheses-list',
              null,
              ...hypotheses.map((hypothesis) =>
                h(
                  'div.hypothesis-item',
                  null,
                  h('div', { text: typeof hypothesis === 'string' ? hypothesis : hypothesis.summary || hypothesis.text || JSON.stringify(hypothesis) }),
                  typeof hypothesis === 'object' && (hypothesis.confidence || hypothesis.evidence)
                    ? h(
                        'div.hypothesis-item__meta',
                        null,
                        hypothesis.confidence ? h('span', { text: `置信度 ${hypothesis.confidence}` }) : null,
                        ...(hypothesis.evidence || []).slice(0, 6).map((caseId) => h('span.mono', { text: String(caseId) }))
                      )
                    : null
                )
              )
            ),
          })
        : null,
      relatedCases.length || riskCases.length
        ? sectionCard({
            title: '关联案例',
            description: '这些案例与修改假设或风险判断相关。',
            body: h(
              'div',
              null,
              relatedCases.length
                ? h(
                    'div.related-cases',
                    { style: { marginBottom: riskCases.length ? '8px' : 0 } },
                    ...relatedCases.map((caseId) =>
                      h(
                        'button.route-pill',
                        { type: 'button', style: { cursor: 'pointer' }, onclick: () => openCaseDrawer(String(caseId)) },
                        String(caseId)
                      )
                    )
                  )
                : null,
              riskCases.length
                ? h(
                    'div',
                    null,
                    h('div.xsmall.muted', { style: { marginBottom: '4px' }, text: '风险案例' }),
                    ...riskCases.map((risk) =>
                      h(
                        'div.hypothesis-item',
                        { style: { marginBottom: '6px' } },
                        h('div', null, typeof risk === 'string' ? risk : risk.case_id || ''),
                        typeof risk === 'object' && risk.reason ? h('div.hypothesis-item__meta', null, h('span', { text: risk.reason })) : null
                      )
                    )
                  )
                : null
            ),
          })
        : null,
      state.diff.expectedEffect
        ? sectionCard({ title: '预期效果', body: h('p.small', { text: state.diff.expectedEffect }) })
        : null,
    ].filter(Boolean);

    if (!changes.length) {
      mount(
        diffHost,
        h(
          'div.stack',
          null,
          ...headerSections,
          emptyState({
            iconName: 'versions',
            title: state.run.mode === 'baseline' ? '基线运行没有文件差异' : '本轮没有候选补丁',
            description:
              state.run.mode === 'baseline'
                ? '基线评测只执行当前 Skill 包。启动单轮优化后，这里会展示候选相对基线的文件差异。'
                : state.run.decision_reason || '优化器未生成可应用的修改；可查看运行记录了解直接原因。',
            actions: [
              { label: '查看运行记录', variant: 'secondary', iconName: 'run', onClick: () => switchTab('log') },
              { label: '查看结果概览', variant: 'ghost', onClick: () => switchTab('result') },
            ],
          })
        )
      );
      return;
    }
    const selected = Math.min(state.diff.selected || 0, changes.length - 1);
    const change = changes[selected] || {};
    const fileList = h(
      'div.change-file-list',
      null,
      ...changes.map((item, index) =>
        h(
          `button.change-file${index === selected ? ' is-active' : ''}`,
          {
            type: 'button',
            onclick: () => {
              state.diff.selected = index;
              renderDiff();
            },
          },
          icon('file', { size: 14 }),
          h('span.change-file__path', { title: item.path, text: item.path }),
          h(
            'span.change-file__stat',
            null,
            h('span.add', { text: `+${item.additions ?? 0}` }),
            ' ',
            h('span.del', { text: `−${item.deletions ?? 0}` })
          )
        )
      )
    );
    const modeToggle = h(
      'div.segmented',
      null,
      h(
        `button.segmented__btn${state.diff.mode === 'unified' ? ' is-active' : ''}`,
        { type: 'button', onclick: () => { state.diff.mode = 'unified'; renderDiff(); } },
        '统一'
      ),
      h(
        `button.segmented__btn${state.diff.mode === 'split' ? ' is-active' : ''}`,
        { type: 'button', onclick: () => { state.diff.mode = 'split'; renderDiff(); } },
        '并排'
      )
    );
    mount(
      diffHost,
      h(
        'div.stack',
        null,
        ...headerSections,
        h(
          'div.file-diff-layout',
          null,
          fileList,
          h(
            'div.diff-pane',
            null,
            h(
              'div.diff-pane__head',
              null,
              h('span.diff-pane__path', { text: change.path || '—' }),
              chip(change.status === 'added' ? '新增文件' : change.status === 'removed' ? '删除文件' : '修改', change.status === 'added' ? 'success' : change.status === 'removed' ? 'danger' : 'accent'),
              h('span.mono.xsmall.muted', { text: `+${change.additions ?? 0} −${change.deletions ?? 0}` }),
              h('span.spacer'),
              modeToggle
            ),
            h(
              'div.diff-pane__body',
              null,
              state.diff.mode === 'split' ? splitDiff(change.diff || '') : unifiedDiff(change.diff || '', { wrap: true })
            )
          )
        ),
        h('p.xsmall.muted', { text: '长行已换行显示；补丁只允许修改白名单内的 Markdown 文件，不得新增或删除文件。' })
      )
    );
  }

  /* ---------------- log tab ---------------- */

  function ensureLog() {
    if (state.log) {
      state.log.updateRun(state.run);
      return;
    }
    const log = createRunLog({
      run: state.run,
      onRunActivity: () => {
        scheduleRefresh(450);
      },
    });
    state.log = log;
    mount(logHost, log.element);
  }

  /* ---------------- polling ---------------- */

  function scheduleRefresh(delay = 600) {
    if (state.refreshTimer) return;
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      refreshRun({ forceTab: false });
    }, delay);
  }

  async function refreshRun({ forceTab = false } = {}) {
    const previousStatus = state.run.status;
    try {
      const run = await api.getRun(state.run.id);
      state.run = run;
      if (typeof ctx.updateRunState === 'function') ctx.updateRunState(run);
      else if (previousStatus !== run.status && typeof ctx.refreshShell === 'function') await ctx.refreshShell();
      renderHeader();
      if (state.log) state.log.updateRun(run);
      const becameTerminal = !isTerminalStatus(previousStatus) && isTerminalStatus(run.status);
      if (becameTerminal) {
        stopPolling();
        state.resultLoaded = false;
        if (state.activeTab === 'result') loadResult();
        else if (state.activeTab === 'cases') loadCases({ force: true });
        else if (state.activeTab === 'diff') loadDiff({ force: true });
        renderTabs();
      } else if (forceTab) {
        if (state.activeTab === 'result') loadResult();
        else if (state.activeTab === 'cases') loadCases({ force: true });
        else if (state.activeTab === 'diff') loadDiff({ force: true });
      }
      if (active()) startPolling();
    } catch (error) {
      if (error.status === 404) {
        toast('运行不存在或已被删除', { type: 'error' });
      }
    }
  }

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(() => {
      if (!active()) {
        stopPolling();
        return;
      }
      refreshRun();
    }, 5000);
  }

  function stopPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  function startTicker() {
    state.tickTimer = setInterval(() => {
      if (!active()) return;
      if (liveRefs.elapsedSpan) liveRefs.elapsedSpan.textContent = formatElapsed(state.run.started_at || state.run.created_at);
      if (liveRefs.activitySpan) liveRefs.activitySpan.textContent = relativeTime(state.run.stage_progress?.last_activity_at);
    }, 1000);
  }

  /* ---------------- mount ---------------- */

  mount(
    container,
    headerHost,
    h('div.run-tabs-wrap', null, tabsHost),
    resultHost,
    casesHost,
    diffHost,
    logHost
  );

  renderHeader();
  renderTabs();
  switchTab(state.activeTab, { syncHash: false });
  if (active()) {
    startPolling();
    startTicker();
  }
  renderHeader();

  return () => {
    stopPolling();
    if (state.tickTimer) clearInterval(state.tickTimer);
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    if (state.log) state.log.destroy();
  };
}
