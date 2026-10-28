/** 项目概览：配置完整性、最近指标、最近运行、随状态变化的主操作。 */

import { api } from '../api_v3.js';
import { isTaskGuide, guideNextAction, activeOverviewRun } from '../onboarding.js';
import { createRunPreparation } from '../runPreparation.js';
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
  toast,
  kvList,
} from '../components.js';
import {
  formatDateTime,
  formatPercent,
  formatDate,
  metricsSummary,
  modeLabel,
  shortenId,
} from '../format.js';
import { DECISION_META, RULE_LABELS, STAGE_LABELS, decisionKey } from '../eventTypes.js';

export default async function overviewView(container, ctx) {
  const { project } = ctx;
  let overview = null;
  let loading = true;
  let error = null;

  const contentHost = h('div');

  function primaryActionView() {
    const completeness = overview?.completeness;
    const active = activeOverviewRun(overview);
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

    if (!ready && isTaskGuide(overview)) {
      return h('div.primary-action-block', null,
        button({ label: '配置模型并发布', variant: 'primary', iconName: 'environment', href: `#/projects/${encodeURIComponent(project.id)}/environment` }),
        h('span.start-hint', { text: '先配置业务、优化和评分模型，保存并发布环境。' }));
    }

    if (!ready) {
      return h('div.primary-action-block', null,
        button({ label: '选择版本并运行评测', variant: 'primary', iconName: 'play', onClick: () => openStartDrawer('evaluate') }),
        h('span.start-hint', { text: '可选择已保存的草稿，发布后开始运行。' }));
    }

    if (primary?.mode === 'apply' && primary.run_id) {
      const pending = overview?.pending_candidate || {};
      const change =
        typeof pending.baseline_joint === 'number' && typeof pending.candidate_joint === 'number'
          ? `联合通过率 ${formatPercent(pending.baseline_joint)} → ${formatPercent(pending.candidate_joint)}，`
          : '';
      return h(
        'div.primary-action-block',
        null,
        button({ label: primary.label || '查看并应用候选', variant: 'primary', iconName: 'check', href: `#/runs/${encodeURIComponent(primary.run_id)}/result` }),
        h('span.start-hint', { text: `候选已通过评测：${change}应用后才会成为当前版本。` })
      );
    }

    if (primary?.mode === 'continue' && primary.run_id) {
      return h('div.primary-action-block', null,
        button({ label: primary.label, variant: 'primary', iconName: 'spark', href: `#/runs/${encodeURIComponent(primary.run_id)}/result` }),
        h('span.start-hint', { text: primary.reason }));
    }

    if (guideNextAction(overview) === 'complete') {
      return h('div.primary-action-block', null,
        button({ label: '查看已应用的验收结果', variant: 'primary', iconName: 'check', href: `#/runs/${encodeURIComponent(overview.guide.completed_run_id)}/result` }),
        h('span.start-hint', { text: '本轮闭环已完成，当前 Skill 已通过独立验收并应用。' }));
    }

    if (primary?.mode === 'add_cases') {
      return h(
        'div.primary-action-block',
        null,
        button({ label: primary.label || '补充测试案例', variant: 'primary', iconName: 'plus', href: `#/projects/${encodeURIComponent(project.id)}/dataset` }),
        h(
          'span.start-hint',
          null,
          '基线在测试集上已全部通过，没有可改进的失败案例。',
          h('button.link-button', { type: 'button', onClick: () => openStartDrawer('optimize') }, '仍要运行优化 Skill')
        )
      );
    }

    if (isTaskGuide(overview) && primary?.mode === 'baseline') {
      return h('div.primary-action-block', null,
        button({ label: '运行当前版本基线', variant: 'primary', iconName: 'play', onClick: () => openStartDrawer('baseline') }),
        h('span.start-hint', { text: '评测当前版本的日常测试集，查看失败依据。' }));
    }

    return h(
      'div.primary-action-block',
      null,
      button({ label: primary?.mode === 'optimize' ? '根据测试结果优化' : '运行测试集', variant: 'primary', iconName: 'play', onClick: () => openStartDrawer(primary?.mode === 'optimize' ? 'optimize' : 'evaluate') }),
      h('span.start-hint', { text: primary?.mode === 'optimize' ? '复用兼容测试结果，生成候选并重跑。' : '只评测测试集；独立验收集和最终测试集不参与。' })
    );
  }

  function guideSection() {
    if (!isTaskGuide(overview)) return null;
    const next = guideNextAction(overview);
    const nextText = {
      configure: '下一步：配置并发布模型', active: '运行进行中，请查看进度',
      evaluate: '下一步：运行当前版本的测试集', optimize: '下一步：根据测试结果优化',
      continue: '下一步：查看候选，继续优化或启动独立验收',
      apply: '下一步：查看验收结果并应用候选', add_cases: '当前测试集已全部通过，可查看结果或补充案例',
      complete: '本轮闭环已完成：候选已通过独立验收，并成为当前 Skill。',
    }[next];
    const steps = [
      ['配置并发布环境', '业务、优化和评分模型需接入真实服务；Prompt 已预填。'],
      ['运行基线', '运行日常测试集，查看失败依据。'],
      ['优化并重跑', '生成候选，在日常集重跑并检查差异。'],
      ['独立验收', '在候选结果页启动独立验收，默认各重复 3 次配对比较。'],
      ['应用候选', '验收通过后，在结果页应用候选版本。'],
    ];
    return sectionCard({
      title: '任务协作入门',
      description: '跟随一个完整示例，体验从发现问题到验收应用。',
      body: h('div.guide', null,
        h('p.guide__next', { text: nextText }),
        h('ol.guide__steps', null, ...steps.map(([title, description]) =>
          h('li', null, h('strong', { text: title }), h('span', { text: description })))),
        h('p.guide__note', { text: '示例有两处刻意设置的映射错误；工具返回由 fixture 模拟，测试答案固定。不会自动调用模型。' }),
        h('details', null, h('summary', { text: '数据与评分说明' }),
          h('p', { text: '初始预置 3 个子技能、24 条日常案例、12 条独立验收案例和 6 条最终留出案例。最终留出集供 API 教程使用，不参与日常优化和独立验收。description 参数按语义比较，其余参数严格比较。' }))
      ),
    });
  }

  function completenessSection() {
    const completeness = overview?.completeness;
    if (!completeness) return null;
    const items = completeness.items || {};
    const itemKeys = ['skill_package', 'environment', 'dataset'];
    const missingLabels = (completeness.missing || [])
      .filter((key) => key !== 'baseline')
      .map((key) => items[key]?.label || key);
    return sectionCard({
      title: '运行准备',
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
                h('div.config-item__label', { text: ({skill_package: 'Skill', environment: '配置 Prompt', dataset: '测试集'})[key] || item.label || key }),
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
    if (!metrics && guideNextAction(overview) === 'complete') return null;
    if (!metrics) return h('p.overview-empty-metrics', { text: '当前配置还没有评测成绩；运行评测后，这里会显示分发、工具参数和联合通过率。' });
    const datasetInfo = metrics.dataset;
    const evaluatedMode = metrics.mode || 'baseline';
    const metricsRunId = metrics.run_id || overview?.baseline?.run_id;
    const evaluatedText = metrics.evaluated_at ? formatDateTime(metrics.evaluated_at) : '—';
    const stale = Boolean(metrics.stale);
    const coverageMissing = [metrics.routing, metrics.tool_args, metrics.joint].some((block) => block && block.status === 'not_evaluated');

    const cards = [];
    for (const [key, label, hint] of [
      ['joint', '整体通过 J', '技能、动作、工具、参数全部正确'],
      ['routing', '选对技能 R', '模型选中了允许的子技能'],
      ['tool_args', '参数正确 A', '业务工具与参数都对'],
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
        ? `${formatDate(evaluatedText)} ${evaluatedMode === 'evaluate' ? '普通评测（测试集）' : evaluatedMode === 'baseline' ? '基线评测（测试集）' : '优化运行'} · 数据集 v${datasetInfo.version_seq || '?'}`
        : `评测时间 ${evaluatedText} · ${evaluatedMode === 'evaluate' ? '普通评测：测试集' : evaluatedMode === 'baseline' ? '基线评测：测试集' : '优化运行'}`,
      actions: [
        stale ? chip('历史成绩', 'warning', { iconName: 'clock' }) : chip('配置一致', 'success', { iconName: 'check' }),
        metricsRunId
          ? button({ label: '查看对应运行', variant: 'ghost', size: 'sm', href: `#/runs/${encodeURIComponent(metricsRunId)}` })
          : null,
      ].filter(Boolean),
      body: h(
        'div',
        null,
        metrics.meta
          ? h('div.row', { style: { gap: '16px', marginBottom: '14px' } }, h('span.muted.small', { text: metrics.meta }))
          : null,
        h('div.metric-grid', null, ...cards),
        stale
          ? h(
              'p.coverage-hint',
              { style: { marginTop: '10px' } },
              `当前 Skill、环境或数据版本与该次运行不一致，以上数值标为历史成绩；请重新运行评测。`
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
      collapsible: true,
      open: false,
      padded: false,

      body: runs.length
        ? h(
            'div.run-list',
            null,
            ...runs.map((run) => {
              const decision = run.decision ? DECISION_META[decisionKey(run)] : null;
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

                    decision ? statusBadge(decisionKey(run), 'decision') : null,
                    historical ? chip('历史配置', 'neutral') : null
                  ),
                  h('div.run-list__meta', {
                    text: `${source ? `${source} · ` : ''}${formatDateTime(run.created_at)}`,
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
      guideSection(),
      completenessSection(),
      metricSection(),
      recentRunsSection()
    );
  }

  function acceptanceRules(environment) {
    const repeats = environment?.acceptance_config?.validation_repeats || environment?.run_config?.validation_repeats || 3;
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
    return [...list.map(([, label]) => label), `验证配对重复 ${repeats} 次`];
  }

  async function openStartDrawer(mode) {
    if (loading || error || !overview) return;
    const body = h('div', null, loadingState('正在加载已保存版本…'));
    const errorHost = h('div');
    const stageHost = h('p.start-summary__note', { role: 'status' });
    const startButton = button({ label: '开始运行', variant: 'primary', iconName: 'play', disabled: true });
    const drawer = openDrawer({
      title: mode === 'optimize' ? '优化 Skill' : '运行测试集',
      subtitle: '默认使用当前版本；需要修改时展开版本选择。', size: 'md',
      body: h('div.start-summary', null, body, stageHost, errorHost),
      footer: h('div.btn-row', null, button({ label: '取消', variant: 'ghost', onClick: () => drawer.close() }), startButton),
    });
    try {
      const [fresh, skills, environments, datasets] = await Promise.all([
        api.getProject(project.id), api.listSkillVersions(project.id),
        api.listEnvironments(project.id), api.listDatasets(project.id),
      ]);
      const asItems = (value) => Array.isArray(value) ? value : (value?.items || []);
      const groups = [
        ['skill', 'Skill', asItems(skills), fresh.current_skill_version_id],
        ['environment', 'Prompt 与模型', asItems(environments), fresh.current_environment_id],
        ['dataset', '测试集', asItems(datasets), fresh.current_dataset_id],
      ];
      const selects = {};
      const details = h('div');
      const currentSummary = h('div');
      const advanced = h('div');
      const prepare = createRunPreparation(api);
      let submitting = false;
      const selection = () => Object.fromEntries(groups.map(([kind, , items]) => [kind, items.find((item) => item.id === selects[kind].value)]));
      const changed = () => groups.some(([kind, , , current]) => selects[kind].value !== (current || ''));
      const update = async () => {
        const chosen = selection();
        const valid = groups.every(([kind]) => chosen[kind]);
        mount(currentSummary, kvList(groups.map(([kind, label]) => ({
          label, value: chosen[kind] ? `${chosen[kind].label || (chosen[kind].version_seq ? `v${chosen[kind].version_seq}` : shortenId(chosen[kind].id, 14))}${chosen[kind].status === 'draft' ? '（草稿）' : ''}` : '未配置',
        }))));
        startButton.disabled = !valid || submitting;
        startButton.textContent = groups.some(([kind]) => chosen[kind]?.status === 'draft') ? '发布并开始' : '开始运行';
        mount(details, h('p.start-summary__note', { text: mode === 'evaluate' ? '只评测测试集，不生成候选；独立验收集和最终测试集不参与。' : mode === 'baseline' ? '基线只评测测试集；独立验收集和最终测试集保持隔离。' : '优化会在需要时自动运行测试集基线并重跑候选；正式验收需另行启动。' }));
        mount(advanced);
        if (chosen.environment) {
          const id = chosen.environment.id;
          const env = await api.getEnvironment(id).catch(() => chosen.environment);
          if (selects.environment.value !== id) return;
          const config = env.run_config || {};
          mount(advanced, kvList([
            { label: '模型', value: env.model_config?.model || env.model_config?.provider || '未配置' },
          ]), h('details', null, h('summary', { text: '高级参数与正式验收条件' }),
            kvList([{ label: '并发', value: `${config.concurrency || 3} 个案例` }, { label: '调用上限', value: `${config.max_model_calls || 12} 次` }, { label: '超时', value: `${config.case_timeout_s || 120} 秒` }]),
            h('p.small.muted', { text: '以下条件仅在正式验收时用于判定。' }),
            h('ul', null, ...acceptanceRules(env).map((label) => h('li', { text: label })))));
        }
      };
      const controls = groups.map(([kind, label, items, current]) => {
        const eligible = items.filter((item) => item.id === current || item.status === 'draft');
        const select = h('select', { 'aria-label': label },
          h('option', { value: '', text: '请选择已保存版本' }),
          ...eligible.map((item) => h('option', { value: item.id, text: `${item.label || (item.version_seq ? `v${item.version_seq}` : shortenId(item.id, 14))} · ${item.id === current ? '当前版本' : '草稿'}` })));
        select.value = eligible.some((item) => item.id === current) ? current : '';
        select.addEventListener('change', update);
        selects[kind] = select;
        return h('label.form-field', null, h('span', { text: label }), select,
          !eligible.length ? h('a', { href: `#/projects/${encodeURIComponent(project.id)}/${kind === 'environment' ? 'environment' : kind}`, onClick: () => drawer.close() }, '去配置') : null);
      });
      mount(body, h('div.start-summary__section', null, h('h3', { text: '本轮使用' }), currentSummary,
          h('details', { open: groups.some(([, , , current]) => !current) },
            h('summary', { text: '切换版本或选择草稿' }), ...controls)),
        details, h('div.start-summary__section', null, advanced),
        h('p.start-summary__note', { text: '运行会产生模型调用费用；后台运行不依赖页面保持打开。' }));
      startButton.addEventListener('click', async () => {
        if (submitting) return;
        const chosen = selection();
        if (groups.some(([kind]) => !chosen[kind])) return;
        submitting = true; startButton.disabled = true;
        Object.values(selects).forEach((select) => { select.disabled = true; });
        mount(errorHost);
        try {
          const result = await prepare({ projectId: project.id, selection: chosen, mode, expectedPointers: fresh,
            baselineId: !changed() ? overview.baseline?.run_id : null,
            reuseBaseline: !changed() && Boolean(overview.baseline?.compatible),
            onStage: (stage) => { stageHost.textContent = `${stage}中…`; } });
          const runId = result?.run_id || result?.id;
          if (!runId) throw new Error('服务未返回运行 ID，请检查运行列表');
          drawer.close(); toast('运行已创建', { type: 'success' }); ctx.navigate(`#/runs/${encodeURIComponent(runId)}`);
        } catch (err) {
          stageHost.textContent = '';
          mount(errorHost, h('div.inline-error', { role: 'alert' }, icon('alert', { size: 14 }),
            h('span', { text: `${err.preparationStage || '启动'}失败：${err.message}。已发布的版本会保留，可重试。` })));
          submitting = false;
          Object.values(selects).forEach((select) => { select.disabled = false; });
          update();
        }
      });
      update();
    } catch (err) {
      mount(body, errorState(err, { title: '无法加载版本', retry: () => { drawer.close(); openStartDrawer(mode); } }));
    }
  }

  let pollTimer = null;
  let disposed = false;

  function hasActiveRun() {
    return Boolean(
      overview?.active_run ||
        (overview?.recent_runs?.items || []).some((run) => ['queued', 'running', 'cancel_requested'].includes(run.status))
    );
  }

  function schedulePoll() {
    if (pollTimer || disposed || !hasActiveRun()) return;
    // A run is in progress: keep the page current without a manual refresh.
    pollTimer = setTimeout(async () => {
      pollTimer = null;
      if (disposed) return;
      await load({ silent: true });
    }, 5000);
  }

  async function load({ silent = false } = {}) {
    if (!silent) {
      loading = true;
      error = null;
      render();
    }
    try {
      overview = await api.getOverview(project.id);
      error = null;
    } catch (err) {
      if (!silent) error = err;
    } finally {
      loading = false;
      if (!disposed) render();
      schedulePoll();
    }
  }

  function makeHeader() {
    return pageHeader({
      title: project.name,
      description: project.description || '从失败案例改进 Skill，再用独立案例验证',
      actions: [
        loading ? null : primaryActionView(),
        !loading && !activeOverviewRun(overview) && (guideNextAction(overview) === 'complete' || ['optimize', 'continue', 'apply', 'add_cases'].includes(overview?.primary_action?.mode))
          ? button({ label: '重新运行测试集', variant: 'ghost', iconName: 'play', onClick: () => openStartDrawer('evaluate') }) : null,
        !loading && !activeOverviewRun(overview) && guideNextAction(overview) !== 'complete' && overview?.completeness?.ready && overview?.primary_action?.mode === 'baseline'
          ? button({ label: '直接优化 Skill', variant: 'secondary', iconName: 'spark', onClick: () => openStartDrawer('optimize') }) : null,
      ].filter(Boolean),
    });
  }

  mount(container, makeHeader(), contentHost);
  await load();
  return () => {
    disposed = true;
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
  };
}
