/** Shared run actions and live progress used by the project home and run page. */

import { api } from './api_v3.js';
import { h, icon } from './dom.js';
import { confirmDialog, toast, progressBar } from './components.js';
import { formatClockDuration } from './format.js';
import { STAGE_LABELS } from './eventTypes.js';

export const MODE_COPY = {
  evaluate: {
    title: '运行测试',
    message: '用测试集评测当前 Skill，记录每个案例是否通过及原因。独立验收集不参与。',
    confirm: '开始测试',
  },
  optimize: {
    title: '优化 Skill',
    message: '优化模型根据失败案例修改 Skill，生成一个候选，并在测试集上重跑对比。正式版本不会改变。',
    confirm: '开始优化',
  },
  continue: {
    title: '基于候选继续优化',
    message: '在上一轮候选的基础上再改一轮，仍然只在测试集上重跑对比。',
    confirm: '继续优化',
  },
  validate: {
    title: '独立验收',
    message: '用优化时没见过的验收集，对原版和候选各跑多次配对比较。通过冻结的接受规则后才能应用。',
    confirm: '开始验收',
  },
  final_test: {
    title: '最终留出测试',
    message: '用最终测试集做一次阶段检查，不参与候选选择。',
    confirm: '开始',
  },
};

/**
 * Confirm and start a run. Pending settings edits take effect automatically
 * for testing and fresh optimisation; continuing and validation never pick
 * them up.
 */
export async function startRunFlow(ctx, project, action, { sourceRunId = null, pending = [], offline = false } = {}) {
  const mode = action === 'continue' ? 'optimize' : action;
  const copy = MODE_COPY[action] || MODE_COPY.evaluate;
  const usesEdits = action === 'evaluate' || (action === 'optimize' && !sourceRunId);
  const notes = [];
  if (pending.length && usesEdits) notes.push(`${pending.map((item) => item.label).join('、')} 的修改会在这次运行中生效。`);
  if (pending.length && !usesEdits) notes.push(`${pending.map((item) => item.label).join('、')} 的修改不会用于这次运行（它沿用候选当时的配置）。`);
  notes.push(offline ? '使用离线演示模型，不产生费用。' : '会调用已配置的模型，产生相应用量。可以随时取消，关闭页面也会在后台继续。');
  const ok = await confirmDialog({
    title: copy.title,
    message: copy.message,
    details: h('ul.confirm-notes', null, ...notes.map((text) => h('li', { text }))),
    confirmText: copy.confirm,
  });
  if (!ok) return null;
  try {
    const result = await api.startRun(project.id, { mode, sourceRunId });
    const frozen = result.frozen || [];
    toast(frozen.length ? `已启动，${frozen.map((item) => item.label).join('、')}的修改已生效` : '已启动', { type: 'success', duration: 2500 });
    ctx.navigate(`#/runs/${encodeURIComponent(result.run.id)}`);
    return result.run;
  } catch (error) {
    const kind = error.details?.kind;
    const tab = { skill: 'skill', environment: 'model', dataset: 'dataset' }[kind];
    toast(`启动失败：${error.message}`, {
      type: 'error',
      duration: 8000,
      action: tab ? { label: '去修改', onClick: () => ctx.navigate(`#/projects/${encodeURIComponent(project.id)}/settings/${tab}`) } : null,
    });
    return null;
  }
}

/** Estimated remaining time from cases finished in this stage (reused ones excluded). */
export function estimateRemaining(progress, now = Date.now()) {
  const total = Number(progress?.total || 0);
  const completed = Number(progress?.completed || 0);
  const reused = Number(progress?.reused || 0);
  const executed = completed - reused;
  const started = Date.parse(progress?.started_at || '');
  if (!total || executed < 2 || completed >= total || !Number.isFinite(started)) return null;
  const perCase = (now - started) / executed;
  return Math.max(0, Math.round(perCase * (total - completed)));
}

/** Compact live progress: stage, bar, pass/fail tally, ETA and latest failures. */
export function liveProgress(run, { compact = false, onCase = null } = {}) {
  const progress = run.stage_progress || {};
  const total = Number(progress.total || 0);
  const completed = Number(progress.completed || 0);
  const outcomes = progress.outcomes || {};
  const stage = STAGE_LABELS[progress.stage || run.stage] || progress.stage || run.stage || '准备中';
  const remaining = estimateRemaining(progress);
  const queued = run.status === 'queued';
  const tally = [
    ['passed', '通过', 'ok'],
    ['failed', '未通过', 'fail'],
    ['infra', '出错', 'warn'],
  ].filter(([key]) => outcomes[key] || key !== 'infra');
  const failures = (progress.recent_failures || []).slice().reverse();
  return h(`div.live${compact ? ' live--compact' : ''}`, null,
    h('div.live__head', null,
      h('span.spinner.spinner--sm'),
      h('strong', { text: queued ? '排队中' : run.status === 'cancel_requested' ? '正在取消…' : stage }),
      total ? h('span.live__count', { text: `${completed} / ${total}` }) : h('span.muted.small', { text: progress.message || '准备中…' }),
      h('span.live__spacer'),
      remaining !== null ? h('span.muted.small', { text: `预计还需 ${formatClockDuration(remaining)}` }) : null),
    progressBar({ value: completed, max: total || 1, indeterminate: !total }),
    total && Object.keys(outcomes).length
      ? h('div.live__tally', null, ...tally.map(([key, label, tone]) =>
        h(`span.tally.tally--${tone}`, null, h('b', { text: String(outcomes[key] || 0) }), label)),
      progress.reused ? h('span.muted.xsmall', { text: `其中 ${progress.reused} 条复用了之前的结果` }) : null)
      : null,
    !compact && failures.length
      ? h('div.live__failures', null,
        h('div.muted.xsmall', { text: '最近未通过' }),
        ...failures.map((item) => h(onCase ? 'button.live__failure' : 'div.live__failure',
          onCase ? { type: 'button', onclick: () => onCase(item.case_id) } : null,
          icon(item.kind === 'infra' ? 'alert' : 'cancel', { size: 12 }),
          h('span.mono', { text: item.case_id }),
          h('span.live__reason', { text: item.reason || (item.kind === 'infra' ? '执行出错' : '未通过') }))))
      : null);
}
