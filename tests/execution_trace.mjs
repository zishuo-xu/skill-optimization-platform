import test from 'node:test';
import assert from 'node:assert/strict';
import { projectExecutionTrace, groupExecutionTrace } from '../web/js/executionTrace.js';

const event = (type, data = {}) => {
  const {stage, variant, repeat_index, attempt, call_id, ...payload} = data;
  return {type, stage, variant, repeat_index, attempt, call_id, payload};
};

test('skill selection is pending until a matching read confirms the load', () => {
  const pending = projectExecutionTrace([
    event('skill_selected', { skill_id: 'image_adjust_celia', skill_path: 'references/image-adjust-celia.md' }),
  ]);
  assert.equal(pending.skills.length, 1);
  assert.equal(pending.skills[0].status, 'pending');

  const loaded = projectExecutionTrace([
    event('skill_selected', { skill_id: 'image_adjust_celia', skill_path: 'references/image-adjust-celia.md' }),
    event('skill_read', { skill_id: 'image_adjust_celia', path: 'references/image-adjust-celia.md', source: 'select_skill' }),
  ]);
  assert.equal(loaded.skills[0].status, 'loaded');
});

test('cached reads consolidate into one skill loading step', () => {
  const result = projectExecutionTrace([
    event('skill_selected', { skill_id: 'tone', skill_path: 'references/tone.md' }),
    event('skill_read', { path: 'references/tone.md', source: 'select_skill' }),
    event('skill_read', { path: 'references/tone.md', cached: true, source: 'read_skill' }),
  ]);
  assert.equal(result.skills.length, 1);
  assert.equal(result.skills[0].status, 'loaded');
  assert.equal(result.skills[0].cached, true);
});

test('history from different executions does not share skill loading state', () => {
  const result = projectExecutionTrace([
    event('skill_selected', { skill_id: 'tone', path: 'references/tone.md', stage: 'baseline_eval', variant: 'baseline', repeat_index: 1 }),
    event('skill_read', { skill_id: 'tone', path: 'references/tone.md', stage: 'baseline_eval', variant: 'baseline', repeat_index: 1 }),
    event('skill_selected', { skill_id: 'tone', path: 'references/tone.md', stage: 'retest', variant: 'candidate', repeat_index: 1 }),
  ]);
  assert.equal(result.skills.length, 2);
  assert.equal(result.skills[0].status, 'loaded');
  assert.equal(result.skills[1].status, 'pending');
});

test('a standalone historical skill read remains visible as loaded', () => {
  const result = projectExecutionTrace([
    event('skill_read', { path: 'references/legacy.md', skill_id: 'legacy' }),
  ]);
  assert.deepEqual(result.skills, [{ key: 'references/legacy.md', skillId: 'legacy', path: 'references/legacy.md', status: 'loaded', cached: false }]);
});

test('business calls group start, finish and result without merging repeated calls', () => {
  const result = projectExecutionTrace([
    event('tool_call_started', { tool: 'editImage', arguments: { method: 'crop' } }),
    event('tool_call_finished', { tool: 'editImage', ok: true }),
    event('tool_result', { tool: 'editImage', response: { code: 0 } }),
    event('tool_call_started', { tool: 'editImage', arguments: { method: 'adjust_tone' } }),
    event('fixture_miss', { tool: 'editImage', issue: { message: 'fixture missing' } }),
  ]);
  assert.equal(result.calls.length, 2);
  assert.equal(result.calls[0].status, 'returned');
  assert.deepEqual(result.calls[0].response, { code: 0 });
  assert.equal(result.calls[1].status, 'failed');
});

test('finished calls retain arguments and response, including fixture failures', () => {
  const result = projectExecutionTrace([
    event('tool_call_started', { tool: 'editImage', call_id: 'batch-1', arguments: { method: 'crop' } }),
    event('tool_call_finished', { tool: 'editImage', call_id: 'batch-1', ok: true, arguments: { method: 'crop' } }),
    event('tool_result', { tool: 'editImage', call_id: 'batch-1', arguments: { method: 'crop' }, response: { code: 0 } }),
    event('tool_call_started', { tool: 'editImage', call_id: 'batch-1', arguments: { method: 'tone' } }),
    event('tool_call_finished', { tool: 'editImage', call_id: 'batch-1', ok: false, arguments: { method: 'tone' } }),
    event('fixture_miss', { tool: 'editImage', call_id: 'batch-1', arguments: { method: 'tone' }, issue: { message: 'fixture missing' } }),
  ]);
  assert.equal(result.calls.length, 2);
  assert.deepEqual(result.calls[0].arguments, { method: 'crop' });
  assert.deepEqual(result.calls[0].response, { code: 0 });
  assert.equal(result.calls[1].status, 'failed');
});

test('same model call id does not merge distinct business tools', () => {
  const result = projectExecutionTrace([
    event('tool_call_started', { call_id: 'model-1', tool: 'editImage', arguments: { method: 'crop' } }),
    event('tool_call_started', { call_id: 'model-1', tool: 'lookupImage', arguments: { id: 'A' } }),
    event('tool_result', { call_id: 'model-1', tool: 'editImage', response: { code: 0 } }),
    event('tool_result', { call_id: 'model-1', tool: 'lookupImage', response: { found: true } }),
  ]);
  assert.equal(result.calls.length, 2);
  assert.deepEqual(result.calls.map((call) => call.name), ['editImage', 'lookupImage']);
  assert.ok(result.calls.every((call) => call.status === 'returned'));
});

test('finish, model and judge events stay in internal details', () => {
  const result = projectExecutionTrace([
    event('model_call_started'),
    event('finish_called', { action: 'tool_call' }),
    event('judge_completed', { passed: true, purpose: 'judge' }),
  ]);
  assert.equal(result.skills.length, 0);
  assert.equal(result.calls.length, 0);
  assert.equal(result.internal.length, 3);
});

test('incomplete business events become unconfirmed only at completion', () => {
  const running = projectExecutionTrace([
    event('tool_call_started', { tool: 'editImage', arguments: { method: 'crop' } }),
  ]);
  assert.equal(running.calls[0].status, 'running');
  const complete = projectExecutionTrace(running.raw, { complete: true });
  assert.equal(complete.calls[0].status, 'unconfirmed');
});

test('empty and non-business events do not invent user-facing steps', () => {
  const events = [event('model_call_started'), event('model_call_finished'), event('score_completed')];
  const result = projectExecutionTrace(events, { complete: true });
  assert.deepEqual(result.skills, []);
  assert.deepEqual(result.calls, []);
  assert.equal(result.internal.length, 3);
});

test('projection does not mutate the event input', () => {
  const events = [event('tool_call_started', { tool: 'editImage', arguments: { method: 'crop' } })];
  const before = JSON.stringify(events);
  projectExecutionTrace(events, { complete: true });
  assert.equal(JSON.stringify(events), before);
});


test('interleaved same-name calls with distinct call IDs retain their own result', () => {
  const result = projectExecutionTrace([
    event('tool_call_started', {tool:'editImage',call_id:'a',arguments:{image:'A'}}),
    event('tool_call_started', {tool:'editImage',call_id:'b',arguments:{image:'B'}}),
    event('tool_result', {tool:'editImage',call_id:'a',response:{image:'A'}}),
    event('tool_result', {tool:'editImage',call_id:'b',response:{image:'B'}}),
  ]);
  assert.equal(result.calls.length, 2);
  assert.deepEqual(result.calls.map(c => c.response), [{image:'A'}, {image:'B'}]);
});

test('all original records remain available inside protocol details', () => {
  const events = [event('skill_selected',{skill_id:'tone',skill_path:'tone.md'}),
    event('skill_read',{path:'tone.md'}), event('trial_started'),
    event('tool_call_started',{tool:'editImage'}),event('tool_result',{tool:'editImage',response:{ok:true}})];
  assert.equal(projectExecutionTrace(events).internal.length, events.length);
});


test('history without a start frame still combines finish and result', () => {
  const trace = projectExecutionTrace([event('tool_call_finished',{tool:'editImage',call_id:'a',ok:true}), event('tool_result',{tool:'editImage',call_id:'a',response:{code:0}})], {complete:true});
  assert.equal(trace.calls.length, 1);
  assert.equal(trace.calls[0].status, 'returned');
});

test('judge tool events cannot become business calls', () => {
  const trace = projectExecutionTrace([event('tool_call_started',{tool:'judge',purpose:'judge'})]);
  assert.equal(trace.calls.length, 0);
  assert.equal(trace.internal.length, 1);
});


test('transport retry metadata does not split a logical skill load', () => {
  const trace = projectExecutionTrace([event('skill_selected',{skill_id:'tone',skill_path:'tone.md',variant:'baseline',repeat_index:1,attempt:1}), event('skill_read',{path:'tone.md',variant:'baseline',repeat_index:1,attempt:2})]);
  assert.equal(trace.skills.length, 1);
  assert.equal(trace.skills[0].status, 'loaded');
});

test('variant-less case verdict joins the sole matching execution without losing raw evidence', () => {
  const records = [event('skill_read', {variant:'main', stage:'baseline_eval', repeat_index:1, path:'a.md'}), event('case_failed', {stage:'baseline_eval', repeat_index:1})];
  const groups = groupExecutionTrace(records);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].events, records);
});

test('ambiguous variant-less audit events remain separate', () => {
  const records = ['baseline', 'candidate', undefined].map(variant => event('model_call_finished', {variant, stage:'eval', repeat_index:1}));
  assert.equal(groupExecutionTrace(records).length, 3);
});
