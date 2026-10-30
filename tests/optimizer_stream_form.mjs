import test from 'node:test';
import assert from 'node:assert/strict';
// The view's component module registers an overlay listener at import time.
// These tests exercise form conversion, without rendering the DOM.
globalThis.document = { addEventListener() {} };
const { toForm } = await import('../web/js/views/environment.js');

test('optimizer timeout is independent and defaults to 180 seconds', () => {
  assert.equal(toForm(null).optimizer_config.timeout_s, 180);
  for (const status of ['draft', 'published']) {
    assert.equal(toForm({status, optimizer_config: {}}).optimizer_config.timeout_s, 180);
    const env = {status, optimizer_config: {timeout_s: 240}, model_config: {timeout_s: 15}};
    const form = toForm(env);
    assert.equal(form.optimizer_config.timeout_s, 240);
    assert.equal(form.model_config.timeout_s, 15);
    assert.equal(env.optimizer_config.timeout_s, 240);
  }
});

test('new and unset draft optimizer connections default to streaming', () => {
  assert.equal(toForm(null).optimizer_config.stream, true);
  assert.equal(toForm({ status: 'draft', optimizer_config: { provider: 'openai_http' } }).optimizer_config.stream, true);
});

test('explicit stream choices survive reload', () => {
  for (const status of ['draft', 'published']) {
    for (const stream of [true, false]) {
      const env = { status, optimizer_config: { provider: 'openai_http', stream }, model_config: { stream: !stream } };
      const form = toForm(env);
      assert.equal(form.optimizer_config.stream, stream);
      assert.equal(form.model_config.stream, !stream);
      assert.equal(env.optimizer_config.stream, stream);
    }
  }
});
