/** 项目概览：配置完整性、最近指标、最近运行、随状态变化的主操作。 */

import { api } from '../api_v3.js';
import { h, mount, icon } from '../dom.js';
import {
  button,
  chip,
  statusBadge,
  pageHeader,
  sectionCard,
  callout,
  emptyState,
  errorState,
  loadingState,
  metricCard,
  openDrawer,
  formField,
  toast,
  kvList,
} from '../components.js';
import {
  formatDateTime,
  formatDelta,
  formatPercent,
  formatDate,
  formatTokens,
  metricsSummary,
  modeLabel,
  relativeTime,
  shortenId,
  splitLabel,
} from '../format.js';
import { DECISION_META, MODE_META, RULE_LABELS, STAGE_LABELS } from '../eventTypes.js';

export default async function overviewView(container, ctx) {
  const { project } = ctx;
  let overview = null;
  let loading = true;
  let error = null;

  const contentHost = h('div');

  function primaryActionView() {
    const completeness = overview?.completeness;
    const baseline = overview?.baseline;
    const active =
      overview?.active_run ||
      (overview?.recent_runs?.items || []).find((run) => ['queued', 'running', 'cancel_requested'].includes(run.status));
    const primary = overview?.primary_action;
    const ready = completeness?.ready;

    if (active) {
      return h(
        'div.primary-action-block',
        null,
        button({ label: '查看运行进度', variant: 'primary', iconName: 'play', href: `#/runs/${encodeURIComponent(active.id)}` }),
        h('span.start-hint', { text: `${STAGE_LABELS[active.stage] || active.stage || '准备中'} · 后台运行不依赖页面保持打开` })
      );
    }

    if (!ready) {
      const firstMissing = (completeness?.missing || []).find((key) => completeness.items[key]?.route);
      const item = firstMissing ? completeness.items[firstMissing] : null;
      return h(
        'div.primary-action-block',
        null,
        button({
          label: '完成配置',
          variant: 'primary',
          iconName: 'arrowRight',
          href: item?.route || `#/projects/${encodeURIComponent(project.id)}/skill`,
        }),
        h('span.start-hint', { text: primary?.reason || '配置尚未完成，补齐后即可运行基线评测。' })
      );
    }

    const needsBaseline = !baseline?.available || !baseline?.compatible;
    const mode = needsBaseline ? 'baseline' : 'optimize';
    const label = needsBaseline ? '运行基线' : '单轮优化';
    if (primary && primary.enabled === false) {
      return h(
        'div.primary-action-block',
        null,
        button({ label: primary.label || label, variant: 'primary', disabled: true, iconName: 'play' }),
        h('span.start-hint', { text: primary.reason || '当前不能启动运行。' })
      );
    }
    return h(
      'div.primary-action-block',
      null,
      button({ label, variant: 'primary', iconName: 'play', onClick: () => openStartDrawer(mode) }),
      h('span.start-hint', {
        text: needsBaseline ? '将冻结当前环境与数据，执行一次基线评测。' : '将复用兼容基线，生成一个候选并配对复测。',
      })
    );
  }

  function completenessSection() {
    const completeness = overview?.completeness;
    if (!completeness) return null;
    const items = completeness.items || {};
    const itemKeys = ['skill_package', 'environment', 'dataset', 'baseline'];
    const missingLabels = (completeness.missing || [])
      .filter((key) => key !== 'baseline')
      .map((key) => items[key]?.label || key);
    return sectionCard({
      title: completeness.ready ? '当前配置' : '完成运行配置',
      collapsible: completeness.ready,
      open: !completeness.ready,
      description: completeness.ready ? '基础配置已齐备，可以运行评测。' : `还缺 ${missingLabels.length || completeness.missing.length} 项配置`,
      body: h(
        'div',
        null,
        !completeness.ready
          ? callout({
              tone: 'warning',
              title: '完成以下配置后才能启动运行',
              body: `缺少：${missingLabels.join('、') || '基础配置'}。每项都可以直接跳转到对应页面补齐。`,
            })
          : null,
        h(
          'div.config-check',
          null,
          ...itemKeys.map((key) => {
            const item = items[key];
            if (!item) return null;
            const ok = Boolean(item.ok);
            const action =
              !ok && item.route
                ? h('a', { class: 'config-item__action', href: item.route }, item.label === '兼容基线' ? '去运行' : '去配置')
                : ok && item.route
                ? h('a', { class: 'config-item__action muted', href: item.route }, '查看')
                : null;
            return h(
              `div.config-item ${ok ? 'is-ok' : 'is-missing'}`,
              null,
              h(`span.config-item__icon`, null, icon(ok ? 'check' : 'alert', { size: 12 })),
              h(
                'div.config-item__body',
                null,
                h('div.config-item__label', { text: item.label || key }),
                h('div.config-item__detail', { title: item.detail || '', text: item.detail || (ok ? '已就绪' : '未配置') })
              ),
              action
            );
          })
        )
      ),
    });
  }

  function metricSection() {
    const metrics = overview?.metrics;
    const baseline = overview?.baseline;
    if (!metrics) return h('p.overview-empty-metrics', { text: '当前配置还没有基线成绩；运行基线评测后，这里会显示分发、工具参数和联合通过率。' });
    const datasetInfo = metrics.dataset;
    const evaluatedText = metrics.evaluated_at ? formatDateTime(metrics.evaluated_at) : '—';
    const samples = metrics.routing?.evaluated ?? metrics.tool_args?.evaluated ?? null;
    const stale = Boolean(metrics.stale);
    const coverageMissing = [metrics.routing, metrics.tool_args, metrics.joint].some((block) => block && block.status === 'not_evaluated');

    const cards = [];
    for (const [key, label, hint] of [
      ['joint', '联合通过率 J', '分发、动作与工具参数全部正确'],
      ['routing', '分发准确率 R', '子技能分发是否正确'],
      ['tool_args', '工具入参准确率 A', '工具选择与完整参数是否正确'],
    ]) {
      const block = metrics[key];
      if (!block) continue;
      cards.push(
        metricCard({
          label,
          value: formatPercent(block.value),
          sub: hint,
          tone: block.status === 'not_evaluated' ? 'neutral' : 'neutral',
          status: block.status === 'not_evaluated' ? null : null,
        })
      );
    }

    return sectionCard({
      title: '最近指标',
      description: datasetInfo
        ? `${formatDate(evaluatedText)} 评测 · 数据集 v${datasetInfo.version_seq || '?'}${datasetInfo.case_count ? ` · ${datasetInfo.case_count} 条案例` : ''}`
        : `评测时间 ${evaluatedText}`,
      actions: [
        stale ? chip('历史成绩', 'warning', { iconName: 'clock' }) : chip('配置一致', 'success', { iconName: 'check' }),
        overview?.baseline?.run_id
          ? button({ label: '查看基线运行', variant: 'ghost', size: 'sm', href: `#/runs/${encodeURIComponent(overview.baseline.run_id)}` })
          : null,
      ].filter(Boolean),
      body: h(
        'div',
        null,
        metrics.meta
          ? h('div.row', { style: { gap: '16px', marginBottom: '14px' } }, h('span.muted.small', { text: metrics.meta }))
          : null,
        h('div.metric-grid', null, ...cards),
        h(
          'div.dataset-summary',
          { style: { marginTop: '14px', marginBottom: 0 } },
          h('span', null, h('strong', { text: '数据集' }), ` ${datasetInfo ? `v${datasetInfo.version_seq}` : '—'}`),
          h('span', null, h('strong', { text: '有效样本' }), ` ${samples === null ? '—' : samples}`),
          h('span', null, h('strong', { text: '评测时间' }), ` ${evaluatedText}`),
          datasetInfo?.id
            ? h('a', { href: `#/projects/${encodeURIComponent(project.id)}/dataset` }, '查看数据集')
            : null
        ),
        stale
          ? h(
              'p.coverage-hint',
              { style: { marginTop: '10px' } },
              `当前 Skill、环境或数据版本与基线不一致，以上数值标为历史成绩，需要重新运行基线后才能用于对比。`
            )
          : null,
        coverageMissing
          ? h(
              'p.coverage-hint',
              { style: { marginTop: '10px' } },
              '部分指标为“未评估”：数据集中缺少对应子技能或工具的有效案例，未覆盖项不会记为满分。',
              ' ',
              h('a', { href: `#/projects/${encodeURIComponent(project.id)}/dataset` }, '检查数据集覆盖')
            )
          : null
      ),
    });
  }

  function recentRunsSection() {
    const runs = overview?.recent_runs?.items || [];
    return sectionCard({
      title: '最近运行',
      description: '刷新页面不会中断后台运行；可随时回到运行详情查看实时日志。',
      padded: false,

      body: runs.length
        ? h(
            'div.run-list',
            null,
            ...runs.map((run) => {
              const decision = run.decision ? DECISION_META[run.decision] : null;
              const historical = [
                ['baseline_skill_version_id', 'current_skill_version_id'],
                ['environment_id', 'current_environment_id'],
                ['dataset_id', 'current_dataset_id'],
              ].some(([field, current]) => run[field] && project[current] && run[field] !== project[current]);
              const provider = run.snapshot?.environment?.provider;
              const source = provider === 'demo_scripted' ? '离线演示' : provider === 'openai_http' ? '标准 API' : provider === 'enterprise_ws' ? '企业网关' : '';

              return h(
                'a.run-list__item',
                { href: `#/runs/${encodeURIComponent(run.id)}` },
                h('span', null, statusBadge(run.status, 'run')),
                h(
                  'div.run-list__main',
                  null,
                  h(
                    'div.run-list__title',
                    null,
                    h('span', { text: modeLabel(run.mode) }),

                    decision ? statusBadge(run.decision, 'decision') : null,
                    historical ? chip('历史配置', 'neutral') : null
                  ),
                  h('div.run-list__meta', {
                    text: `${source ? `${source} · ` : ''}${formatDateTime(run.created_at)} · ${relativeTime(run.created_at)}`,
                  })
                ),
                h('span.run-list__metrics', { text: metricsSummary(run.metrics?.candidate || run.metrics?.baseline) })
              );
            })
          )
        : emptyState({
            iconName: 'run',
            title: '还没有运行',
            description: '配置完成后运行基线评测，这里会出现运行记录与指标。',

          }),
    });
  }

  function render() {
    if (loading) {
      mount(contentHost, loadingState('正在加载概览…'));
      return;
    }
    if (error) {
      mount(
        contentHost,
        errorState(error, { title: '无法加载项目概览', retry: () => load() })
      );
      return;
    }
    const header = container.querySelector('.page-head');
    if (header) header.replaceWith(makeHeader());
    mount(
      contentHost,
      completenessSection(),
      metricSection(),
      recentRunsSection()
    );
  }

  function acceptanceRules() {
    const rules = overview?.environment?.acceptance_config || {};
    const list = [
      ['routing_not_decreased', RULE_LABELS.routing_not_decreased],
      ['tool_args_not_decreased', RULE_LABELS.tool_args_not_decreased],
      ['at_least_one_improved', RULE_LABELS.at_least_one_improved],
      ['joint_not_decreased', RULE_LABELS.joint_not_decreased],
      ['critical_cases_no_regression', RULE_LABELS.critical_cases_no_regression],
      ['no_infra_errors', RULE_LABELS.no_infra_errors],
      ['package_integrity', RULE_LABELS.package_integrity],
      ['validation_complete', RULE_LABELS.validation_complete],
    ];
    void rules;
    return list.map(([, label]) => label);
  }

  async function openStartDrawer(mode) {
    if (loading || error || !overview) return;
    const completeness = overview.completeness;
    if (!completeness.ready) {
      toast('请先完成配置并发布对应版本', { type: 'warning' });
      return;
    }
    const baseline = overview.baseline;
    let environment = overview.environment || project.current_environment || null;
    const dataset = overview.dataset || project.current_dataset || null;
    const skillVersion = overview.skill_version || project.current_skill_version || null;
    if (environment && !environment.run_config && project.current_environment_id) {
      try {
        environment = await api.getEnvironment(project.current_environment_id);
      } catch {
        /* keep summary */
      }
    }
    const runConfig = environment?.run_config || {};
    const budget = environment?.budget_config || {};
    const reuseBaseline = mode === 'optimize' && Boolean(baseline?.available && baseline?.compatible);
    const reuseInput = h('input', { type: 'checkbox', checked: reuseBaseline });
    const errorHost = h('div');
    let submitting = false;

    const startButton = button({ label: mode === 'baseline' ? '开始基线评测' : '开始单轮优化', variant: 'primary', iconName: 'play' });
    startButton.addEventListener('click', submit);

    const drawer = openDrawer({
      title: mode === 'baseline' ? '运行基线评测' : '启动单轮优化',
      subtitle: '启动时冻结 Skill 包、执行环境与数据集；运行期间不可修改配置。',
      size: 'md',
      body: h(
        'div.start-summary',
        null,
        h(
          'div.start-summary__section',
          null,
          h('h3', { text: '版本快照' }),
          kvList([
            { label: 'Skill 包', value: skillVersion ? `${skillVersion.label || shortenId(skillVersion.id, 14)} · ${skillVersion.status === 'draft' ? '草稿' : '已发布'}` : '—', mono: true },
            { label: '执行环境', value: environment ? `v${environment.version_seq} · ${environment.model_config?.model || environment.model_config?.provider || '已配置'}` : '—' },
            { label: '数据集', value: dataset ? `v${dataset.version_seq} · ${dataset.case_count ?? 0} 条案例` : '—', mono: true },
            { label: '评分器', value: environment?.scorer_config?.scorer_version || 'score-v1', mono: true },
          ]),
          environment?.status === 'draft'
            ? h('p.start-summary__note', { style: { marginTop: '8px' } }, '当前执行环境是草稿，建议先发布版本再运行。')
            : null
        ),
        h(
          'div.start-summary__section',
          null,
          h('h3', { text: '基线与复用' }),
          baseline?.available
            ? h(
                'div',
                null,
                h(
                  'div.row',
                  null,
                  chip(baseline.compatible ? '基线兼容' : '基线不兼容', baseline.compatible ? 'success' : 'warning', {
                    iconName: baseline.compatible ? 'check' : 'alert',
                  }),
                  h('a', { href: `#/runs/${encodeURIComponent(baseline.run_id)}` }, `基线运行 ${shortenId(baseline.run_id, 14)}`)
                ),
                h('p.start-summary__note', { style: { marginTop: '6px' } }, baseline.reason || ''),
                mode === 'optimize'
                  ? h(
                      'label.checkbox-line',
                      { style: { marginTop: '10px' } },
                      reuseInput,
                      h('span', { text: '复用兼容基线结果（不重新执行基线评测）' })
                    )
                  : null
              )
            : h('p.start-summary__note', { text: '尚无兼容基线，本次会先执行基线评测。' })
        ),
        h(
          'div.start-summary__section',
          null,
          h('h3', { text: '运行参数与预算' }),
          kvList(
            [
              { label: '候选数量', value: mode === 'optimize' ? '1 个候选' : '—' },
              { label: '验证重复', value: `${environment?.acceptance_config?.validation_repeats || runConfig.validation_repeats || 3} 次配对` },
              { label: '并发', value: `${runConfig.concurrency || 3} 个案例` },
              { label: '案例上限', value: `${runConfig.max_model_calls || 12} 次模型调用 / ${runConfig.case_timeout_s || 120} 秒` },
              { label: 'Token 预算', value: budget.max_tokens ? `上限 ${formatTokens(budget.max_tokens)} Token` : '未设置（启动前必须设置）' },
              { label: '金额预算', value: budget.max_cost ? `上限 ${budget.max_cost}` : '未配置价格，以 Token 硬限制为准' },
            ],
            { compact: true }
          )
        ),
        h(
          'details.start-summary__section',
          null,
          h('summary', { text: '查看完整接受条件' }),
          h(
            'ul',
            { style: { paddingLeft: '1.1em', color: 'var(--text-2)', fontSize: '12.5px', lineHeight: '1.8' } },
            ...acceptanceRules().map((label) => h('li', { text: label }))
          )
        ),
        h(
          'p.start-summary__note',
          null,
          '运行会产生模型调用费用；取消运行不会回退已经产生的费用。后台运行不依赖页面保持打开。'
        ),
        errorHost
      ),
      footer: h(
        'div.btn-row',
        null,
        button({ label: '取消', variant: 'ghost', onClick: () => drawer.close() }),
        startButton
      ),
    });

    async function submit() {
      if (submitting) return;
      submitting = true;
      startButton.disabled = true;
      startButton.textContent = '正在创建运行…';
      mount(errorHost);
      try {
        const response = await api.createRun({
          project_id: project.id,
          mode,
          parent_run_id: mode === 'optimize' && baseline?.run_id ? baseline.run_id : undefined,
          expected_current_version: project.current_skill_version_id,
          reuse_baseline: mode === 'optimize' ? Boolean(reuseInput.checked) : undefined,
        });
        const runId = response?.run_id || response?.id;
        drawer.close();
        toast('运行已创建，正在跳转到运行详情', { type: 'success' });
        if (runId) ctx.navigate(`#/runs/${encodeURIComponent(runId)}`);
        else await ctx.reload();
      } catch (err) {
        submitting = false;
        startButton.disabled = false;
        startButton.textContent = mode === 'baseline' ? '开始基线评测' : '开始单轮优化';
        const active = err.details?.run_id || overview?.active_run?.id;
        mount(
          errorHost,
          h(
            'div',
            null,
            h('div.inline-error', { role: 'alert' }, icon('alert', { size: 14 }), h('span', { text: err.message })),
            active && err.code === 'run_in_progress'
              ? h('p.small', { style: { marginTop: '8px' } }, h('a', { href: `#/runs/${encodeURIComponent(active)}` }, '查看正在进行的运行'))
              : null
          )
        );
      }
    }
  }

  async function load() {
    loading = true;
    error = null;
    render();
    try {
      overview = await api.getOverview(project.id);
    } catch (err) {
      error = err;
    } finally {
      loading = false;
      render();
    }
  }

  function makeHeader() {
    return pageHeader({
      title: project.name,
      description: project.description || '从失败案例改进 Skill，再用独立案例验证',
      actions: [
        button({ label: '刷新', variant: 'ghost', iconName: 'refresh', onClick: () => load() }),
        loading ? null : primaryActionView(),
      ].filter(Boolean),
    });
  }

  mount(container, makeHeader(), contentHost);
  await load();
  return () => {};
}
