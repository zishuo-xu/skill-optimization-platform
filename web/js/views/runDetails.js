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
  callout,
  metricCard,
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
  formatToolCall,
  formatRoutes,
  relativeTime,
  shortenId,
  splitLabel,
  truncate,
  jsonPathDiffs,
  actionLabel,
  safeJsonStringify,
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
  eventLabel,
} from '../eventTypes.js';
import { createRunLog } from './runLog.js';

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
      onlyFailed: false,
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

    const elapsedSpan = h('span', { text: active() ? formatElapsed(run.started_at || run.created_at) : formatDuration(run.cost?.duration_ms) });
    const activitySpan = h('span', { text: run.stage_progress?.last_activity_at ? relativeTime(run.stage_progress.last_activity_at) : '—' });
    const activeCases = (run.stage_progress?.current_case_ids || []).length;
    liveRefs = { elapsedSpan, activitySpan };

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
              statusBadge(run.status, 'run'),
              run.decision ? statusBadge(run.decision, 'decision') : null,
              chip(modeMeta.label, 'neutral'),
              isTerminalStatus(run.status) ? null : chip('运行中不可修改配置', 'warning')
            ),
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
              snapshot.environment ? h('span.meta-item', null, icon('environment', { size: 13 }), h('span', { text: `环境 v${snapshot.environment.version_seq || '—'}` })) : null,
              snapshot.dataset ? h('span.meta-item', null, icon('dataset', { size: 13 }), h('span', { text: `数据集 v${snapshot.dataset.version_seq || '—'}` })) : null,
              h('span.meta-item', null, icon('clock', { size: 13 }), h('span', { text: `开始 ${formatDateTime(run.started_at || run.created_at)}` })),
              run.finished_at ? h('span.meta-item', null, icon('check', { size: 13 }), h('span', { text: `结束 ${formatDateTime(run.finished_at)}` })) : null,
              run.rules_version ? h('span.meta-item', null, h('span.xsmall.muted', { text: `规则 ${run.rules_version}` })) : null
            )
          ),
          h(
            'div.run-header__actions',
            null,
            button({ label: '刷新', variant: 'ghost', iconName: 'refresh', onClick: () => refreshRun({ forceTab: true }) }),
            h(
              'div.btn-row',
              { style: { gap: '6px' } },
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
            ),
            active()
              ? button({ label: run.status === 'cancel_requested' ? '正在取消…' : '取消运行', variant: 'danger', size: 'sm', iconName: 'cancel', disabled: run.status === 'cancel_requested' || state.busyCancel, onClick: cancelRun })
              : null
          )
        ),
        h(
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
      ['routing', '分发准确率 R', '允许路线命中率'],
      ['tool_args', '工具入参准确率 A', '业务工具序列与参数正确率'],
      ['joint', '联合通过率 J', '分发、动作、工具与参数全部正确'],
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

  function conclusionSection() {
    const run = state.run;
    const terminal = isTerminalStatus(run.status);
    const decision = run.decision;
    const candVersion = candidateVersionId();
    const applied = Boolean(
      candVersion &&
        ((run.current_skill_version_id && run.current_skill_version_id === candVersion) ||
          (!run.current_skill_version_id && state.project?.current_skill_version_id === candVersion))
    );
    const canApply = run.can_apply !== false && !applied;
    const changedFiles = run.candidate?.changed_files || [];
    const parentVersion = run.snapshot?.skill_version?.label || baselineVersionId();

    if (!terminal) {
      return sectionCard({
        title: '结论与应用',
        description: '运行完成后按冻结规则给出达标 / 未达标 / 证据不足的结论。',
        body: callout({
          tone: 'info',
          title: '运行尚未结束',
          body: '当前显示的指标如标注“临时值”表示结果尚未最终确定；不会提前给出成功结论，也不支持应用候选。',
        }),
      });
    }

    if (decision === 'accept') {
      return sectionCard({
        title: '结论与应用',
        actions: [statusBadge(decision, 'decision')],
        body: h(
          'div.decision-block',
          null,
          h(
            'div.decision-headline',
            null,
            h('h3', { text: applied ? '评测达标，候选已应用' : '评测达标，待应用' }),
            applied ? chip('已应用', 'success', { iconName: 'check' }) : chip('待人工确认', 'warning', { iconName: 'clock' })
          ),
          h('p.decision-reason', { text: run.decision_reason || run.decision_record?.rationale?.reason || run.acceptance?.reason || '候选满足接受规则，等待人工应用。' }),
          kvList(
            [
              { label: '父版本', value: parentVersion || '—', mono: true },
              { label: '变更文件数', value: `${changedFiles.length} 个${changedFiles.length ? `（${changedFiles.join('、')}）` : ''}` },
              { label: '候选版本', value: candVersion || '—', mono: true },
              { label: '应用后效果', value: '仅切换项目当前版本指针，不修改外部生产目录；旧版本保留可回滚。' },
            ],
            { compact: true }
          ),
          h(
            'div.btn-row.btn-row--start',
            { style: { marginTop: '14px' } },
            canApply && candVersion
              ? button({ label: state.applyBusy ? '正在应用…' : '应用候选', variant: 'primary', iconName: 'check', disabled: state.applyBusy, onClick: applyCandidate })
              : null,
            candVersion
              ? button({
                  label: '导出候选包',
                  variant: 'secondary',
                  iconName: 'download',
                  onClick: () => download(api.exportSkillVersionUrl(candVersion), `${candVersion}.zip`),
                })
              : null,
            button({ label: '查看变更文件', variant: 'ghost', iconName: 'versions', onClick: () => switchTab('diff') })
          )
        ),
      });
    }

    if (decision === 'reject') {
      const failedRules = acceptanceRules().filter((rule) => rule.ok === false);
      return sectionCard({
        title: '结论与应用',
        actions: [statusBadge(decision, 'decision')],
        body: h(
          'div.decision-block',
          null,
          h('div.decision-headline', null, h('h3', { text: '评测未达标' }), chip('不可直接应用', 'danger', { iconName: 'lock' })),
          h('p.decision-reason', { text: run.decision_reason || run.acceptance?.reason || '候选未满足接受规则，本次运行已完成，不是系统运行失败。' }),
          h(
            'div.callout.callout--danger',
            null,
            h('div.callout__icon', null, icon('alert', { size: 18 })),
            h(
              'div.callout__content',
              null,
              h('h3.callout__title', { text: '触发规则' }),
              h(
                'ul.small',
                { style: { paddingLeft: '1.1em', marginTop: '4px' } },
                ...(failedRules.length ? failedRules : [{ rule: 'at_least_one_improved', detail: '指标未达到接受条件' }]).map((rule) =>
                  h('li', { text: `${RULE_LABELS[rule.rule] || rule.rule}${rule.detail ? `：${rule.detail}` : ''}` })
                )
              )
            )
          ),
          h(
            'div.btn-row.btn-row--start',
            { style: { marginTop: '14px' } },
            button({
              label: '查看回归案例',
              variant: 'primary',
              iconName: 'versions',
              onClick: () => {
                state.cases.onlyFailed = true;
                switchTab('cases');
                loadCases({ force: true });
              },
            }),
            candVersion
              ? button({
                  label: '导出候选',
                  variant: 'secondary',
                  iconName: 'download',
                  onClick: () => download(api.exportSkillVersionUrl(candVersion), `${candVersion}.zip`),
                })
              : null,
            button({ label: '运行记录', variant: 'ghost', iconName: 'run', onClick: () => switchTab('log') })
          )
        ),
      });
    }

    if (decision === 'inconclusive') {
      const failedRules = acceptanceRules().filter((rule) => rule.ok === false);
      return sectionCard({
        title: '结论与应用',
        actions: [statusBadge(decision, 'decision')],
        body: h(
          'div.decision-block',
          null,
          h('div.decision-headline', null, h('h3', { text: '证据不足' }), chip('需要新一轮验证', 'warning', { iconName: 'info' })),
          h('p.decision-reason', { text: run.decision_reason || run.acceptance?.reason || '候选未生成或验证不完整，本轮无法给出接受结论。' }),
          failedRules.length
            ? h(
                'ul.small',
                { style: { paddingLeft: '1.1em', color: 'var(--text-2)' } },
                ...failedRules.map((rule) => h('li', { text: `${RULE_LABELS[rule.rule] || rule.rule}：${rule.detail || '未满足'}` }))
              )
            : null,
          h(
            'div.btn-row.btn-row--start',
            { style: { marginTop: '14px' } },
            button({ label: '新建验证运行', variant: 'primary', iconName: 'play', onClick: createVerificationRun }),
            button({ label: '查看运行记录', variant: 'secondary', iconName: 'run', onClick: () => switchTab('log') })
          )
        ),
      });
    }

    const statusLabel = {
      failed: '运行失败',
      cancelled: '运行已取消',
      interrupted: '运行已中断',
    }[run.status] || '运行结束';
    return sectionCard({
      title: '结论与应用',
      body: callout({
        tone: run.status === 'failed' ? 'danger' : 'warning',
        title: statusLabel,
        body:
          run.error_message ||
          run.decision_reason ||
          (run.status === 'cancelled'
            ? '运行已取消；已产生的模型调用费用不会回退。可以回到项目概览重新启动运行。'
            : '运行没有产生可应用的候选。可查看运行记录定位原因后重新运行。'),
        actions: h(
          'div',
          null,
          button({
            label: '返回项目概览',
            variant: 'secondary',
            iconName: 'arrowLeft',
            href: `#/projects/${encodeURIComponent(run.project_id)}/overview`,
          }),
          state.log ? null : button({ label: '查看运行记录', variant: 'ghost', iconName: 'run', onClick: () => switchTab('log') })
        ),
      }),
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

  function failedCaseCount() {
    return state.cases.items.filter((item) =>
      item.baseline?.passed === false
      || item.candidate?.passed === false
      || item.passed === false
    ).length;
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
      'p.coverage-hint',
      { style: { marginTop: '10px' } },
      '存在“未评估”的指标：数据集中缺少对应子技能或工具的有效案例，未覆盖项不会记为满分。',
      ' ',
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
    const transitions = computeTransitions();
    const hasCaseData = state.cases.loaded && state.cases.items.length > 0;
    const isBaselineMode = run.mode === 'baseline';

    mount(
      resultHost,
      h(
        'div.stack',
        null,
        h(
          'div.overview-grid',
          null,
          sectionCard({
            title: '指标对比',
            description: temporary ? '运行尚未结束，候选指标为临时值。' : '基线、候选与差值的最终结果。',
            actions: temporary ? [chip('临时值', 'warning', { iconName: 'clock' })] : [],
            body: h(
              'div',
              null,
              metricsTable(metrics, temporary),
              coverageHint()
            ),
          }),
          sectionCard({
            title: '接受规则',
            description: '阈值在运行前冻结，不能为当前候选临时调低。',
            body: ruleList(),
          })
        ),
        h(
          'div.overview-grid',
          null,
          sectionCard({
            title: isBaselineMode ? '基线案例结果' : '案例变化',
            description: isBaselineMode ? '本次只运行了基线，可直接筛选失败案例查看具体参数差异。' : '新增通过与新增失败反映候选的实际影响。',
            actions: hasCaseData
              ? [
                  failedCaseCount()
                    ? button({
                        label: `只看失败案例（${failedCaseCount()}）`,
                        variant: 'primary',
                        size: 'sm',
                        iconName: 'alert',
                        onClick: () => {
                          state.cases.onlyFailed = true;
                          switchTab('cases');
                        },
                      })
                    : null,
                  button({ label: '查看全部案例', variant: 'ghost', size: 'sm', onClick: () => switchTab('cases') }),
                ].filter(Boolean)
              : [],
            body: !hasCaseData
              ? h('p.muted.small', { text: state.cases.loading ? '正在统计案例结果…' : '暂时没有可统计的案例结果。' })
              : isBaselineMode
                ? h(
                    'div.case-transition',
                    null,
                    h(
                      'div.transition-card transition-card--good',
                      null,
                      h('div.transition-card__label', { text: '基线通过' }),
                      h('div.transition-card__value', { text: String(baselineCounts().passed) }),
                      h('div.xsmall.muted', { text: '实际结果与数据集预期一致' })
                    ),
                    h(
                      'div.transition-card transition-card--bad',
                      null,
                      h('div.transition-card__label', { text: '基线失败' }),
                      h('div.transition-card__value', { text: String(baselineCounts().failed) }),
                      h('div.xsmall.muted', { text: '点击上方按钮查看失败字段' })
                    ),
                    h(
                      'div.transition-card',
                      null,
                      h('div.transition-card__label', { text: '未执行/无结果' }),
                      h('div.transition-card__value', { text: String(Math.max(0, state.cases.items.length - baselineCounts().passed - baselineCounts().failed)) }),
                      h('div.xsmall.muted', { text: '案例尚未返回结果' })
                    )
                  )
                : h(
                    'div.case-transition',
                    null,
                    h(
                      `div.transition-card${transitions.fixed ? ' transition-card--good' : ''}`,
                      null,
                      h('div.transition-card__label', { text: '新增通过' }),
                      h('div.transition-card__value', { text: String(transitions.fixed) }),
                      h('div.xsmall.muted', { text: '基线失败 → 候选通过' })
                    ),
                    h(
                      `div.transition-card${transitions.regressed ? ' transition-card--bad' : ''}`,
                      null,
                      h('div.transition-card__label', { text: '新增失败（回归）' }),
                      h('div.transition-card__value', { text: String(transitions.regressed) }),
                      h('div.xsmall.muted', { text: '基线通过 → 候选失败' })
                    ),
                    h(
                      'div.transition-card',
                      null,
                      h('div.transition-card__label', { text: '仍然失败' }),
                      h('div.transition-card__value', { text: String(transitions.unchangedFail) }),
                      h('div.xsmall.muted', { text: '基线失败 → 候选仍失败' })
                    )
                  ),
          }),
          sectionCard({
            title: '执行与成本',
            description: '计划数、完成数、有效数、基础设施错误、耗时与 Token。',
            body: executionStats(),
          })
        ),
        conclusionSection()
      )
    );
  }

  async function applyCandidate() {
    if (state.applyBusy) return;
    const candVersion = candidateVersionId();
    if (!candVersion) return;
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
    const ok = await confirmDialog({
      title: '新建验证运行',
      message: '将基于当前配置与兼容基线重新执行一轮单轮优化，用于补齐验证证据。会产生新的模型调用费用。',
      confirmText: '新建并启动',
    });
    if (!ok) return;
    try {
      const project = await api.getProject(state.run.project_id).catch(() => state.project);
      const response = await api.createRun({
        project_id: state.run.project_id,
        mode: 'optimize',
        parent_run_id: state.run.id,
        expected_current_version: project?.current_skill_version_id,
        reuse_baseline: true,
      });
      const runId = response?.run_id || response?.id;
      toast('验证运行已创建', { type: 'success' });
      if (runId) ctx.navigate(`#/runs/${encodeURIComponent(runId)}`);
    } catch (error) {
      toast(`创建运行失败：${error.message}`, { type: 'error' });
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

  function filteredCases() {
    let items = state.cases.items.slice();
    if (state.cases.onlyFailed) {
      items = items.filter(
        (item) =>
          item.changeType === 'regressed' ||
          item.changeType === 'unchanged_fail' ||
          item.baseline?.passed === false ||
          item.candidate?.passed === false
      );
    }
    const needle = state.cases.search.trim().toLowerCase();
    if (needle) {
      items = items.filter((item) => (item.case_id || '').toLowerCase().includes(needle) || (item.query || '').toLowerCase().includes(needle));
    }
    items.sort((a, b) => changeRank(a.changeType) - changeRank(b.changeType) || String(a.case_id).localeCompare(String(b.case_id)));
    return items;
  }

  function sideCell(side, { label }) {
    if (!side) {
      return h('div.case-result-cell', null, h('span.muted.small', { text: state.run.mode === 'baseline' ? '仅基线运行' : '无结果' }));
    }
    const status = side.status || (side.passed ? 'pass' : 'fail');
    return h(
      'div.case-result-cell',
      null,
      h(
        'div.case-result-line',
        null,
        h('span.case-result-line__label', { text: label }),
        statusBadge(status, 'case')
      ),
      h(
        'div.case-result-line',
        null,
        h('span.case-result-line__label', { text: '路线' }),
        h('span.case-result-line__text', { text: formatRoutes(side.selected_skills ? [side.selected_skills] : []) })
      ),
      h(
        'div.case-result-line',
        null,
        h('span.case-result-line__label', { text: '调用' }),
        h(
          `span.case-result-line__text${side.passed ? ' is-pass' : ' is-fail'}`,
          { text: (side.tool_calls || []).length ? formatToolCall(side.tool_calls[0]) : side.action === 'clarify' ? '请求澄清' : '无工具调用' }
        )
      )
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
    const items = filteredCases();
    const allCount = state.cases.items.length;
    const searchInput = h('input.input', {
      type: 'search',
      placeholder: '搜索 case_id 或 query',
      value: state.cases.search,
      'aria-label': '搜索案例',
      oninput: (event) => {
        state.cases.search = event.target.value;
        renderCasesTable();
      },
    });
    const onlyFailed = h(
      'label.checkbox-line',
      null,
      h('input', {
        type: 'checkbox',
        checked: state.cases.onlyFailed,
        onchange: (event) => {
          state.cases.onlyFailed = event.target.checked;
          renderCases();
        },
      }),
      h('span', { text: '只看失败 / 回归' })
    );

    mount(
      casesHost,
      h(
        'div',
        null,
        h(
          'div.case-toolbar',
          null,
          h('div.search-field', null, icon('search', { size: 15 }), searchInput),
          onlyFailed,
          statusBadge(state.run.status, 'run'),
          !isTerminalStatus(state.run.status) ? chip('结果可能仍会变化', 'warning', { iconName: 'clock' }) : null,
          h('span.case-toolbar__spacer'),
          h('span.muted.xsmall', { text: `显示 ${items.length} / ${allCount} 条，回归优先排序` }),
          button({ label: '刷新', variant: 'ghost', size: 'sm', iconName: 'refresh', onClick: () => loadCases({ force: true }) })
        ),
        h('div', { id: 'cases-table-host' })
      )
    );
    renderCasesTable(items);
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
          description: '当前筛选条件下没有案例，可清除“只看失败”或修改搜索词。',
          actions: [
            {
              label: '清除筛选',
              variant: 'secondary',
              onClick: () => {
                state.cases.onlyFailed = false;
                state.cases.search = '';
                renderCases();
              },
            },
          ],
        })
      );
      return;
    }
    mount(
      host,
      h(
        'div.table-wrap',
        null,
        h(
          'table.data-table',
          null,
          h(
            'thead',
            null,
            h(
              'tr',
              null,
              h('th', { text: 'ID' }),
              h('th', { text: 'query' }),
              h('th', { text: '基线结果' }),
              h('th', { text: '候选结果' }),
              h('th', { text: '变化' })
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
                  onclick: () => openCaseDrawer(item.case_id),
                  onkeydown: (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      openCaseDrawer(item.case_id);
                    }
                  },
                },
                h('td.cell-mono.nowrap', null, item.case_id, item.critical ? chip('关键', 'warning') : null),
                h(
                  'td',
                  { style: { maxWidth: '300px' } },
                  h('div.cell-strong', { text: truncate(item.query || '', 72) }),
                  h('div.xsmall.muted', { text: `${splitLabel(item.split)}${item.repeat_index ? ` · 重复 #${item.repeat_index}` : ''}` })
                ),
                h('td', null, sideCell(item.baseline, { label: '基线' })),
                h('td', null, sideCell(item.candidate, { label: '候选' })),
                h(
                  'td',
                  null,
                  h(
                    'div.row',
                    { style: { gap: '6px' } },
                    state.run.mode === 'baseline' && item.baseline
                      ? statusBadge(item.baseline.passed ? 'pass' : 'fail', 'case')
                      : statusBadge(item.changeType, 'change'),
                    state.run.mode === 'baseline' && item.baseline
                      ? h('span.xsmall.muted', { text: '基线结果' })
                      : null,
                    (item.diagnostics || []).length
                      ? h('span.xsmall.muted', { text: `${item.diagnostics.length} 项差异` })
                      : null
                  ),
                  (item.diagnostics || []).length
                    ? h('div.xsmall.muted.cell-mono', {
                        style: { marginTop: '4px', maxWidth: '220px' },
                        text: item.diagnostics[0].field_path || item.diagnostics[0].category || '查看差异',
                      })
                    : null
                )
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
      subtitle: '并排查看基线与候选的实际路线、工具调用与参数差异。',
      size: 'xl',
      body: loadingState('正在加载案例详情…'),
      footer: h(
        'div.btn-row.btn-row--between',
        null,
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

  function _scopedCallArguments(call) {
    const args = call && typeof call.arguments === 'object' && call.arguments
      ? call.arguments
      : {};
    if (args && typeof args === 'object' && Object.prototype.hasOwnProperty.call(args, 'args')
        && (Object.prototype.hasOwnProperty.call(args, 'method')
          || Object.prototype.hasOwnProperty.call(args, 'script')
          || Object.prototype.hasOwnProperty.call(args, 'request'))) {
      return args.args || {};
    }
    return args;
  }

  function expectedCallBlock(call, index) {
    const path = call.arguments_path;
    const args = call.arguments || {};
    return h(
      'div.expected-call',
      null,
      h(
        'div.row',
        { style: { gap: '6px', alignItems: 'center' } },
        h('span.mono.xsmall', { text: `#${index + 1} ${call.name || '—'}` }),
        path ? chip(`仅比较 arguments.${path}`, 'neutral') : chip('完整 arguments', 'neutral')
      ),
      h('pre.code-block__pre.expected-call__args', {
        text: safeJsonStringify(args, 2),
        style: { maxHeight: '200px', whiteSpace: 'pre-wrap', margin: '6px 0 0' },
      })
    );
  }

  function actualCallBlock(call) {
    if (!call) return h('p.muted.small', { text: '无业务工具调用' });
    const args = call && typeof call.arguments === 'object' && call.arguments
      ? call.arguments
      : {};
    const scoped = _scopedCallArguments(call);
    const method = args && typeof args === 'object' ? args.method : null;
    return h(
      'div.actual-call',
      null,
      h('div.mono.xsmall', {
        text: `${call.name || '—'}${method ? ` · ${method}` : ''}`,
      }),
      h('pre.code-block__pre.actual-call__args', {
        text: safeJsonStringify(scoped, 2),
        style: { maxHeight: '200px', whiteSpace: 'pre-wrap', margin: '6px 0 0' },
      })
    );
  }

  function sideResultBlock(side, label) {
    if (!side) return h('div.compare-column', null, h('div.compare-column__head', null, h('span', { text: label })), h('div.compare-column__body', null, h('p.muted.small', { text: '无结果' })));
    return h(
      'div.compare-column',
      null,
      h(
        'div.compare-column__head',
        null,
        h('span', { text: label }),
        side.status ? statusBadge(side.status, 'case') : side.passed ? statusBadge('pass', 'case') : statusBadge('fail', 'case')
      ),
      h(
        'div.compare-column__body',
        null,
        h('div', null, h('span.xsmall.muted', { text: '实际路线' }), h('div', null, ...(side.selected_skills || []).map((skill) => h('span.route-pill', { text: skill })))),
        h('div', null, h('span.xsmall.muted', { text: '动作' }), h('div.small', { text: actionLabel(side.action) })),
        h(
          'div',
          null,
          h('span.xsmall.muted', { text: '工具调用' }),
          (side.tool_calls || []).length
            ? h('div.stack', null, ...side.tool_calls.map(actualCallBlock))
            : h('div.small.muted', { text: '无业务工具调用' })
        ),
        side.error_code || side.reason
          ? h('div', null, h('span.xsmall.muted', { text: '失败原因' }), h('div.small', { text: side.reason || side.error_code }))
          : null,
        h('div.xsmall.muted', { text: `耗时 ${formatDuration(side.duration_ms)} · Token ${formatNumber(side.usage?.input_tokens ? (side.usage.input_tokens + (side.usage.output_tokens || 0)) : undefined)}` })
      )
    );
  }

  function paramDiffBlock(detail) {
    const diagnostics = detail.diagnostics || [];
    const diffs = [];
    const seen = new Set();
    for (const diff of diagnostics) {
      const key = `${diff.field_path}|${safeJsonStringify(diff.expected_value)}|${safeJsonStringify(diff.actual_value)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      diffs.push({ path: diff.field_path || '$', expected: diff.expected_value, actual: diff.actual_value, category: diff.category });
    }
    if (!diffs.length) {
      for (const path of ['routing', 'tool_args']) {
        const before = detail.baseline?.[path];
        const after = detail.candidate?.[path];
        if (before?.reason || after?.reason) {
          diffs.push({ path: `$.${path}`, expected: before?.reason || '通过', actual: after?.reason || '通过', category: '评分' });
        }
      }
    }
    if (!diffs.length) return h('p.muted.small', { text: '没有参数差异记录。' });
    return h(
      'div.param-diff-list',
      null,
      ...diffs.map((diff) =>
        h(
          'div.param-diff',
          null,
          h('div.param-diff__path', null, h('div', { text: diff.path }), diff.category ? h('div.xsmall.muted', { text: diff.category }) : null),
          h(
            'div.param-diff__cell',
            null,
            h('span.param-diff__label', { text: '预期（数据集）' }),
            h('span.param-diff__value.is-expected', { text: diff.expected === undefined ? '（缺失）' : typeof diff.expected === 'string' ? diff.expected : safeJsonStringify(diff.expected, 0) })
          ),
          h(
            'div.param-diff__cell',
            null,
            h('span.param-diff__label', { text: '实际（运行结果）' }),
            h('span.param-diff__value.is-actual', { text: diff.actual === undefined ? '（缺失）' : typeof diff.actual === 'string' ? diff.actual : safeJsonStringify(diff.actual, 0) })
          )
        )
      )
    );
  }

  function timelineBlock(events) {
    if (!events.length) return h('p.muted.small', { text: '没有该案例的详细事件。' });
    return h(
      'div.timeline',
      null,
      ...events.slice(-60).map((event) =>
        h(
          `div.timeline__item timeline__item--${event.level || 'info'}`,
          null,
          h('span.timeline__time', { text: formatDateTime(event.ts).slice(11) }),
          h('span.timeline__title', { text: event.payload?.title || eventLabel(event.type) }),
          h(
            'div.timeline__detail',
            null,
            [
              event.payload?.skill_id,
              event.payload?.tool,
              event.payload?.finish_reason,
              event.payload?.passed !== undefined ? (event.payload.passed ? '通过' : '未通过') : null,
              event.payload?.issue?.message,
            ]
              .filter(Boolean)
              .join(' · ')
          )
        )
      )
    );
  }

  function buildCaseDetail(detail, events) {
    const expected = detail.expected || {};
    const routes = expected.routes || [];
    const expectedCalls = expected.tool_calls || [];
    const baselineOnly = state.run.mode === 'baseline';
    return h(
      'div.case-detail',
      null,
      h(
        'div.case-section',
        null,
        h(
          'div.case-section__title',
          null,
          h('span', { text: '数据集预期（评分依据）' }),
          expected.action ? chip(actionLabel(expected.action), 'accent') : null
        ),
        h('div.case-detail__query', { text: detail.query || '—' }),
        h(
          'div.row',
          { style: { marginTop: '8px', gap: '6px' } },
          chip(splitLabel(detail.split), 'neutral'),
          detail.critical ? chip('关键案例', 'warning', { iconName: 'alert' }) : null,
          routes.length ? h('span.route-pill', { text: formatRoutes(routes) }) : null,
          detail.changeType ? statusBadge(detail.changeType, 'change') : null
        ),
        expected.missing_fields?.length
          ? h('p.small', null, h('span.muted', { text: '缺少字段：' }), expected.missing_fields.join('、'))
          : null,
        expectedCalls.length
          ? h('div.stack', { style: { marginTop: '10px' } },
              ...expectedCalls.map((call, index) => expectedCallBlock(call, index)))
          : h('p.muted.small', { style: { marginTop: '8px' }, text: '该案例不期望业务工具调用。' })
      ),
      h(
        'div.case-section',
        null,
        h(
          'div.case-section__title',
          null,
          h('span', { text: baselineOnly ? '实际结果（本次基线运行）' : '实际结果（基线与候选）' }),
          baselineOnly ? chip('无候选结果', 'neutral') : null
        ),
        h('div.compare-columns', null,
          sideResultBlock(detail.baseline, '基线实际'),
          sideResultBlock(detail.candidate, baselineOnly ? '候选（本次未运行）' : '候选实际'))
      ),
      h(
        'div.case-section',
        null,
        h('div.case-section__title', null, h('span', { text: '参数差异（预期 vs 实际）' })),
        paramDiffBlock(detail)
      ),
      h(
        'div',
        null,
        h('div.case-section__title', null, h('span', { text: '时间轨迹' })),
        timelineBlock(events)
      ),
      jsonViewer(detail.expected || {}, { label: '预期结果 JSON', maxHeight: '240px', redact: false })
    );
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
      } else if (forceTab && !isTerminalStatus(run.status)) {
        if (state.activeTab === 'cases') loadCases({ force: true });
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
