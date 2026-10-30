/** Frozen inputs for daily optimization; candidates are never published here. */
export function optimizationRequest(run, project, fromCandidate = false) {
  if (run.status !== 'completed') throw new Error('请等待本次运行完成');
  const root = run.snapshot?.root_skill_version_id || run.baseline_skill_version_id;
  if (project.current_skill_version_id !== (fromCandidate ? root : run.baseline_skill_version_id)) {
    throw new Error('Skill 或模型配置已变化，请回首页重新测试');
  }
  if (fromCandidate) {
    if (run.mode !== 'optimize' || !run.can_continue_optimization) {
      throw new Error('此候选暂不能继续优化，请刷新运行结果');
    }
  } else if (!['evaluate', 'baseline'].includes(run.mode)) {
    throw new Error('测试集已变化，请回首页重新测试');
  }
  return {
    project_id: run.project_id, mode: 'optimize',
    ...(fromCandidate ? { source_run_id: run.id } : { evaluation_run_id: run.id }),
    expected_skill_version_id: project.current_skill_version_id,
    expected_environment_id: project.current_environment_id,
    expected_dataset_id: project.current_dataset_id,
    reuse_baseline: true,
  };
}

export function toolCallSummary(result) {
  if (!result) return '尚未运行';
  const calls = result.tool_calls || [];
  if (!calls.length) return result.action === 'clarify' ? '澄清信息' : result.action === 'no_action' ? '无需调用' : '未调用工具';
  return calls.map(call => {
    const args = Object.entries(call.arguments || {}).slice(0, 2)
      .map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(', ');
    const text = `${call.name}(${args})`;
    return text.length > 100 ? `${text.slice(0, 97)}…` : text;
  }).join(' → ');
}
