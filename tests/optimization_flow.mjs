import test from 'node:test';
import assert from 'node:assert/strict';
import { optimizationRequest, toolCallSummary } from '../web/js/optimizationFlow.js';

const project = { current_skill_version_id: 'published', current_environment_id: 'env', current_dataset_id: 'tests' };
const evaluated = { id: 'eval', project_id: 'project', mode: 'evaluate', status: 'completed', baseline_skill_version_id: 'published', environment_id: 'env', dataset_id: 'tests' };
test('evaluation starts optimization with frozen inputs and cache reuse', () => {
  const request = optimizationRequest(evaluated, project);
  assert.equal(request.mode, 'optimize');
  assert.equal(request.expected_dataset_id, 'tests');
  assert.equal(request.reuse_baseline, true);
  assert.ok(!Object.hasOwn(request, 'source_run_id'));
  assert.equal(request.evaluation_run_id, 'eval');
  assert.equal(optimizationRequest(evaluated, { ...project, current_dataset_id: 'other' }).expected_dataset_id, 'other');
});
test('continuation sends source without publishing and permits a new training dataset', () => {
  const run = { ...evaluated, id: 'round2', mode: 'optimize', baseline_skill_version_id: 'candidate1', snapshot: { root_skill_version_id: 'published' }, can_continue_optimization: true };
  const request = optimizationRequest(run, { ...project, current_dataset_id: 'new-tests' }, true);
  assert.equal(request.source_run_id, 'round2');
  assert.equal(request.expected_skill_version_id, 'published');
  assert.equal(request.expected_dataset_id, 'new-tests');
  assert.throws(() => optimizationRequest({ ...run, can_continue_optimization: false }, project, true), /不能继续/);
  assert.equal(optimizationRequest(run, { ...project, current_environment_id: 'new-env' }, true).expected_environment_id, 'new-env');
  assert.throws(() => optimizationRequest(run, { ...project, current_skill_version_id: 'new-skill' }, true), /已变化/);
});
test('tool summary handles tools, clarify and unexecuted cases', () => {
  assert.equal(toolCallSummary(null), '尚未运行');
  assert.equal(toolCallSummary({ action: 'clarify' }), '澄清信息');
  assert.equal(toolCallSummary({ tool_calls: [{ name: 'editImage', arguments: { image_id: 'img', effect: 'crop' } }] }), 'editImage(image_id="img", effect="crop")');
});
