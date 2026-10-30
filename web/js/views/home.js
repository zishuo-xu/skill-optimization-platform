/**
 * 项目首页：一条流水线 —— 准备 → 测试 → 优化 → 验收 → 应用。
 *
 * 每次只突出当前这一步和一个主按钮；历史运行收在下方。
 * 状态全部来自 GET /projects/:id/pipeline，页面不自行推断评测规则。
 */

import { api } from '../api_v3.js';
import { h, mount, icon } from '../dom.js';
import { button, chip, statusBadge, errorState, loadingState, confirmDialog, toast } from '../components.js';
import { formatPercent, formatDelta, formatDateTime, relativeTime, modeLabel } from '../format.js';
import { decisionKey } from '../eventTypes.js';
import { isTaskGuide } from '../onboarding.js';
import { startRunFlow, liveProgress } from '../runActions.js';

const STEP_HELP = {
  prepare: '导入 Skill、配置模型、准备测试集',
  test: '用测试集评测当前 Skill',
  optimize: '根据失败案例生成候选并重跑',
  validate: '用独立验收集确认候选更好',
  apply: '把通过验收的候选设为正式版本',
};

function pct(block) {
  return formatPercent(block?.value, { fallback: '—' });
}

function passText(block) {
  if (!block || typeof block.passed !== 'number' || typeof block.evaluated !== 'number') return '';
  return `${block.passed}/${block.evaluated} 通过`;
}

export default async function homeView(container, ctx) {
  const { project } = ctx;
  const base = `#/projects/${encodeURIComponent(project.id)}`;
  let data = null;
  let error = null;
  let busy = false;
  let pollTimer = null;
  let destroyed = false;

  const host = h('div.home');

  async function load() {
    try {
      data = await api.getPipeline(project.id);
      error = null;
    } catch (err) {
      error = err;
    }
    if (destroyed) return;
    render();
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (data?.active_run) pollTimer = setTimeout(async () => {
      const wasActive = data.active_run?.id;
      await load();
      if (wasActive && !data?.active_run) ctx.refreshShell?.();
    }, 2500);
  }

  const offline = () => Boolean(data?.offline);

  async function act(action) {
    if (busy) return;
    if (action.id === 'view_run') return ctx.navigate(`#/runs/${encodeURIComponent(action.run_id)}`);
    if (action.id === 'settings') return ctx.navigate(`${base}/settings/${action.tab || 'skill'}`);
    if (action.id === 'apply') return applyCandidate(action.run_id);
    busy = true;
    render();
    try {
      await startRunFlow(ctx, project, action.id, {
        sourceRunId: ['continue', 'validate'].includes(action.id) ? action.run_id : null,
        pending: data?.prepare?.workspace?.pending || [],
        offline: offline(),
      });
    } finally {
      busy = false;
      if (!destroyed) render();
    }
  }

  async function applyCandidate(runId) {
    const fresh = await api.getProject(project.id).catch(() => project);
    const ok = await confirmDialog({
      title: '应用候选',
      message: '候选会成为正式 Skill，之后的测试和优化都基于它。旧版本保留在版本历史里，可以随时切回。',
      confirmText: '应用',
    });
    if (!ok) return;
    busy = true;
    render();
    try {
      await api.applyRun(runId, { expectedCurrentVersion: fresh.current_skill_version_id });
      toast('已应用，正式 Skill 已更新', { type: 'success' });
      await ctx.refreshShell?.();
      await load();
    } catch (err) {
      toast(`应用失败：${err.message}`, { type: 'error', duration: 8000 });
    } finally {
      busy = false;
      render();
    }
  }

  /* ------------------------------------------------------------ */

  function stepper() {
    return h('ol.pipeline', { 'aria-label': '流程进度' }, ...data.steps.map((step, index) =>
      h(`li.pipeline__step.is-${step.state}`, null,
        h('span.pipeline__dot', null,
          step.state === 'done' ? icon('check', { size: 13 })
            : step.state === 'failed' ? icon('x', { size: 13 })
              : step.state === 'skipped' ? icon('check', { size: 13 })
                : String(index + 1)),
        h('span.pipeline__text', null,
          h('strong', { text: step.label }),
          h('small', { text: step.state === 'skipped' ? '无需优化' : STEP_HELP[step.key] })))));
  }

  function actionButtons(actions) {
    return h('div.stage-card__actions', null, ...actions.map((action) => button({
      label: action.label,
      variant: action.primary ? 'primary' : 'secondary',
      iconName: action.primary ? ({ evaluate: 'play', optimize: 'spark', continue: 'spark', validate: 'shield', apply: 'check', settings: 'environment', view_run: 'arrowRight' }[action.id] || null) : null,
      disabled: busy || action.disabled,
      onClick: () => act(action),
    })), ...actions.filter((a) => a.primary && a.hint).map((a) => h('span.stage-card__hint', { text: a.hint })));
  }

  function scoreRow(label, before, after) {
    const delta = before && after && typeof before.value === 'number' && typeof after.value === 'number'
      ? after.value - before.value : null;
    return h('div.score-row', null,
      h('span.score-row__label', { text: label }),
      before ? h('span.score-row__before', { text: pct(before) }) : null,
      before && after ? icon('arrowRight', { size: 13 }) : null,
      after ? h('span.score-row__after', { text: pct(after) }) : null,
      delta !== null ? h(`span.score-row__delta.${delta > 0.0001 ? 'is-up' : delta < -0.0001 ? 'is-down' : 'is-flat'}`, { text: formatDelta(delta) }) : null,
      h('span.score-row__count', { text: passText(after || before) }));
  }

  function scores(before, after) {
    return h('div.scores', null,
      scoreRow('整体通过', before?.joint, after?.joint),
      h('details.scores__more', null, h('summary', { text: '分项指标' }),
        scoreRow('选对子技能', before?.routing, after?.routing),
        scoreRow('工具与参数正确', before?.tool_args, after?.tool_args)));
  }

  function countPills(counts) {
    if (!counts) return null;
    return h('div.count-pills', null,
      h('span.count-pill.is-good', null, h('b', { text: String(counts.fixed) }), '修好了'),
      h(`span.count-pill${counts.regressed ? ' is-bad' : ''}`, null, h('b', { text: String(counts.regressed) }), '变差了'),
      h('span.count-pill', null, h('b', { text: String(counts.unchanged_fail) }), '仍失败'),
      h('span.count-pill', null, h('b', { text: String(counts.unchanged_pass) }), '一直通过'));
  }

  function stageCard() {
    const current = data.current;
    const active = data.active_run;
    const parts = [];
    let title = '';
    let lead = '';

    if (active) {
      title = `正在${modeLabel(active.mode)}`;
      lead = '后台运行，关闭页面也不会中断。';
      parts.push(liveProgress(active));
    } else if (current === 'prepare') {
      title = '先完成准备';
      lead = '还差下面几项，补齐后就可以开始测试。';
      parts.push(h('ul.issue-list', null, ...data.prepare.issues.map((text) => h('li', null, icon('alert', { size: 13 }), text))));
    } else if (current === 'test') {
      title = data.prepare.workspace.pending.length ? '有修改尚未测试' : '运行测试，看看现在的表现';
      lead = data.prepare.workspace.pending.length
        ? `${data.prepare.workspace.pending.map((item) => item.label).join('、')} 已修改。运行测试后修改生效，并得到新的分数。`
        : '用测试集评测当前 Skill，得到每个案例的通过情况和失败原因。';
      if (data.official) parts.push(h('p.muted.small', { text: '上次分数（修改前）' }), scores(null, data.official.metrics));
    } else if (current === 'optimize') {
      const joint = data.official?.metrics?.joint;
      const failed = typeof joint?.evaluated === 'number' ? joint.evaluated - joint.passed : null;
      title = failed ? `${failed} 个案例没通过，可以开始优化` : '可以开始优化';
      lead = '优化模型会根据失败案例修改 Skill，生成一个候选并在测试集上重跑对比。正式版本不会改变。';
      if (data.official) parts.push(scores(null, data.official.metrics));
      if (data.last_optimize) parts.push(h('p.stage-card__note', null, icon('info', { size: 13 }),
        `上次优化没有产生候选：${data.last_optimize.reason || '查看运行记录了解原因'}`,
        ' ', h('a', { href: `#/runs/${encodeURIComponent(data.last_optimize.run_id)}`, text: '查看' })));
    } else if (current === 'validate' && data.candidate) {
      const cand = data.candidate;
      const failedValidation = data.validation && data.validation.status === 'completed' && data.validation.decision !== 'accept';
      title = failedValidation ? '候选没有通过独立验收' : `候选已就绪（第 ${cand.round} 轮）`;
      lead = failedValidation
        ? (data.validation.reason || '候选在验收集上没有达到接受规则。可以继续优化，或调整测试集后重来。')
        : '测试集上的对比如下。确认改动合理后做独立验收；也可以先继续优化。';
      parts.push(scores(cand.baseline, cand.metrics), countPills(cand.counts));
      if (cand.summary) parts.push(h('p.stage-card__note', null, icon('spark', { size: 13 }), `改了 ${cand.changed_files} 个文件：${cand.summary}`));
      if (failedValidation && data.validation.metrics) {
        parts.push(h('p.muted.small', { text: '独立验收集' }), scores(data.validation.baseline, data.validation.metrics));
      }
    } else if (current === 'apply' && data.validation) {
      title = '候选通过了独立验收';
      lead = '应用后候选成为正式 Skill，旧版本保留在版本历史中。';
      parts.push(h('p.muted.small', { text: '独立验收集（原版 → 候选）' }), scores(data.validation.baseline, data.validation.metrics));
    } else if (current === 'done') {
      title = '测试集已全部通过';
      lead = '没有可改进的失败案例。补充更难的案例后再测试，或直接查看结果。';
      if (data.official) parts.push(scores(null, data.official.metrics));
    }

    return h('section.stage-card', null,
      h('div.stage-card__head', null,
        h('h2', { text: title }),
        lead ? h('p', { text: lead }) : null),
      ...parts.filter(Boolean),
      actionButtons(data.actions));
  }

  function appliedBanner() {
    if (!data.applied || data.active_run || data.candidate) return null;
    const before = data.applied.baseline?.joint;
    const after = data.applied.metrics?.joint;
    return h('div.applied-banner', null, icon('check', { size: 15 }),
      h('span', null, `上一轮候选已于 ${formatDateTime(data.applied.applied_at)} 应用`,
        before && after ? `，验收集整体通过 ${pct(before)} → ${pct(after)}` : '', '。'),
      h('a', { href: `#/runs/${encodeURIComponent(data.applied.run_id)}`, text: '查看验收' }));
  }

  function guideNote() {
    if (!isTaskGuide(data)) return null;
    return h('details.guide-note', null,
      h('summary', null, icon('info', { size: 14 }), '关于这个入门示例'),
      h('p', { text: '示例 Skill 有两处刻意设置的映射错误；工具返回由 fixture 模拟，测试答案固定。初始预置 3 个子技能、24 条测试案例、12 条独立验收案例和 6 条最终留出案例。需要先在「设置 → Prompt 与模型」接入真实模型，页面不会自动调用模型。' }));
  }

  function history() {
    const runs = data.history || [];
    if (!runs.length) return null;
    return h('details.history', null,
      h('summary', null, `运行记录（${runs.length}）`),
      h('div.history__list', null, ...runs.map((run) => h('a.history__item', { href: `#/runs/${encodeURIComponent(run.id)}` },
        run.decision ? statusBadge(decisionKey(run), 'decision') : statusBadge(run.status, 'run'),
        h('span.history__mode', { text: modeLabel(run.mode) }),
        h('span.muted.small', { text: relativeTime(run.created_at) }),
        run.error_message ? h('span.history__err', { text: run.error_message }) : null))));
  }

  function render() {
    if (!data && !error) {
      mount(host, loadingState('正在加载…'));
      return;
    }
    if (error && !data) {
      mount(host, errorState(error, { title: '无法加载项目', retry: load }));
      return;
    }
    mount(host,
      h('div.home__head', null,
        h('div', null,
          h('h1', { text: data.project.name }),
          data.project.description ? h('p.muted', { text: data.project.description }) : null),
        button({ label: '设置', variant: 'ghost', size: 'sm', iconName: 'environment', href: `${base}/settings` })),
      stepper(),
      appliedBanner(),
      stageCard(),
      guideNote(),
      history());
  }

  mount(container, host);
  render();
  await load();
  return () => {
    destroyed = true;
    clearTimeout(pollTimer);
  };
}
