/** Keep one submission identity until the chosen versions or run options change. */
export function createRunPreparation(api, makeKey = () => `run-${crypto.randomUUID()}`) {
  let signature = null;
  let key = null;
  let completed = new Set();
  return async function prepare({ projectId, selection, mode, baselineId, reuseBaseline, expectedPointers, onStage = () => {} }) {
    const next = JSON.stringify([selection.skill.id, selection.environment.id, selection.dataset.id, mode, baselineId || null, !!reuseBaseline]);
    if (signature !== next) { signature = next; key = makeKey(); completed = new Set(); }
    const steps = [
      ['skill', 'Skill', 'current_skill_version_id'],
      ['environment', '配置 Prompt', 'current_environment_id'],
      ['dataset', '测试集', 'current_dataset_id'],
    ];
    let stage = '检查当前版本';
    try {
      let current = await api.getProject(projectId);
      for (const [kind, label, pointer] of steps) {
        const version = selection[kind];
        if (completed.has(kind)) {
          if (current[pointer] !== version.id) throw new Error(`${label}当前版本已变更，请重新选择`);
          continue;
        }
        if (version.id === current[pointer]) continue;
        if (expectedPointers && current[pointer] !== (expectedPointers[pointer] ?? null)) throw new Error(`${label}当前版本已变更，请重新选择`);
        if (version.status !== 'draft') throw new Error(`${label}当前版本已变更，请重新选择`);
        stage = `发布${label}`;
        onStage(stage);
        if (kind === 'skill') await api.publishSkillVersion(version.id, true);
        else if (kind === 'environment') await api.publishEnvironment(version.id, true);
        else await api.publishDataset(version.id, { expectedHash: version.hash, expectedCurrentVersion: current[pointer] ?? null, setCurrent: true });
        completed.add(kind);
        current = await api.getProject(projectId);
        if (current[pointer] !== version.id) throw new Error(`${label}发布后当前版本已变更`);
      }
      stage = '创建运行';
      onStage(stage);
      return await api.createRun({ project_id: projectId, mode,
        parent_run_id: mode === 'optimize' && baselineId ? baselineId : undefined,
        reuse_baseline: mode === 'optimize' ? !!reuseBaseline : undefined,
        expected_skill_version_id: selection.skill.id,
        expected_environment_id: selection.environment.id,
        expected_dataset_id: selection.dataset.id,
      }, { idempotencyKey: key });
    } catch (error) {
      error.preparationStage = stage;
      throw error;
    }
  };
}
