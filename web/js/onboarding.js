/** Project onboarding is identified by server metadata, never by its name. */
export const TASK_GUIDE_KEY = 'task-collaboration-v1';

export function isTaskGuide(value) {
  return value?.guide?.key === TASK_GUIDE_KEY;
}

export function orderGuideFirst(projects) {
  return [...projects].sort((a, b) => Number(isTaskGuide(b)) - Number(isTaskGuide(a)));
}

/** The overview service owns compatibility and candidate eligibility. */
export function guideNextAction(overview) {
  const active = overview?.active_run || (overview?.recent_runs?.items || [])
    .find((run) => ['queued', 'running', 'cancel_requested'].includes(run.status));
  if (active) return 'active';
  if (!overview?.completeness?.ready) return 'configure';
  const mode = overview?.primary_action?.mode;
  if (['continue', 'apply', 'optimize', 'add_cases'].includes(mode)) return mode;
  return 'evaluate';
}
