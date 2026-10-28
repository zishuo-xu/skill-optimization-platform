import test from 'node:test';
import assert from 'node:assert/strict';
import { isTaskGuide, orderGuideFirst, guideNextAction } from '../web/js/onboarding.js';

test('only the guide marker identifies the example and ordering is stable', () => {
  const named = { name: '入门引导：任务协作' };
  const guide = { name: 'renamed', guide: { key: 'task-collaboration-v1' } };
  assert.equal(isTaskGuide(named), false);
  assert.equal(isTaskGuide(guide), true);
  assert.deepEqual(orderGuideFirst([named, guide]), [guide, named]);
  assert.deepEqual(orderGuideFirst([]), []);
});

test('guide action uses readiness and server eligibility, not historical runs', () => {
  assert.equal(guideNextAction({}), 'configure');
  assert.equal(guideNextAction({ completeness: { ready: false }, primary_action: { mode: 'apply', run_id: 'old' } }), 'configure');
  assert.equal(guideNextAction({ completeness: { ready: true }, primary_action: { mode: 'baseline' }, recent_runs: { items: [{ status: 'completed', decision: 'accept' }] } }), 'evaluate');
  assert.equal(guideNextAction({ completeness: { ready: true }, primary_action: { mode: 'continue' } }), 'continue');
  assert.equal(guideNextAction({ completeness: { ready: true }, primary_action: { mode: 'apply' } }), 'apply');
  assert.equal(guideNextAction({ active_run: { id: 'r' } }), 'active');
});

test('applied guide completes only with server evidence and yields to ongoing work', () => {
  const done = { guide: { key: 'task-collaboration-v1', completed_run_id: 'accepted' }, completeness: { ready: true }, primary_action: { mode: 'baseline' } };
  assert.equal(guideNextAction(done), 'complete');
  assert.equal(guideNextAction({ ...done, guide: { key: 'task-collaboration-v1' } }), 'evaluate');
  assert.equal(guideNextAction({ ...done, completeness: { ready: false } }), 'configure');
  assert.equal(guideNextAction({ ...done, primary_action: { mode: 'continue' } }), 'continue');
  for (const status of ['queued', 'running', 'cancel_requested']) {
    assert.equal(guideNextAction({ ...done, recent_runs: { items: [{ status }] } }), 'active');
  }
});
