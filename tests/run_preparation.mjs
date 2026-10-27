import assert from 'node:assert/strict';
import { createRunPreparation } from '../web/js/runPreparation.js';
let project = { current_skill_version_id: 's0', current_environment_id: 'e0', current_dataset_id: 'd0' };
let calls = [], fail = 'dataset', n = 0;
const api = {
  async getProject() { return { ...project }; },
  async publishSkillVersion(id) { calls.push('skill'); project.current_skill_version_id = id; },
  async publishEnvironment(id) { calls.push('environment'); project.current_environment_id = id; },
  async publishDataset(id, options) { calls.push('dataset'); assert.equal(options.expectedCurrentVersion, 'd0'); if (fail === 'dataset') throw Error('invalid'); project.current_dataset_id = id; },
  async createRun(payload, options) { calls.push(['run', options.idempotencyKey, payload]); if (fail === 'run') throw Error('network'); return { id: 'r' }; },
};
const prepare = createRunPreparation(api, () => `key${++n}`);
const selection = { skill: { id: 's1', status: 'draft' }, environment: { id: 'e1', status: 'draft' }, dataset: { id: 'd1', status: 'draft', hash: 'hash' } };
const args = { projectId: 'p', selection, mode: 'baseline', expectedPointers: { ...project } };
await assert.rejects(prepare(args), (e) => e.preparationStage === '发布测试集');
assert.deepEqual(calls, ['skill', 'environment', 'dataset']);
fail = 'run'; calls = [];
await assert.rejects(prepare(args), (e) => e.preparationStage === '创建运行');
assert.equal(calls[0], 'dataset'); assert.equal(calls[1][1], 'key1');
fail = null; calls = []; await prepare(args); assert.equal(calls[0][1], 'key1');
assert.equal(calls[0][2].expected_dataset_id, 'd1');
const changed = { ...selection, dataset: { id: 'd2', status: 'draft', hash: 'hash' } };
project.current_dataset_id = 'd0'; calls = []; await prepare({ ...args, selection: changed, expectedPointers: { ...project } });
assert.equal(calls[1][1], 'key2');
project.current_skill_version_id = 'someone-else';
await assert.rejects(prepare({ ...args, selection: changed, expectedPointers: { current_skill_version_id: 's1', current_environment_id: 'e1', current_dataset_id: 'd1' } }), /已变更/);
console.log('run preparation: ok');
