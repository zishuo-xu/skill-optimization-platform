import test from 'node:test';
import assert from 'node:assert/strict';
// The view's component module registers an overlay listener at import time.
// These tests exercise form conversion, without rendering the DOM.
globalThis.document = { addEventListener() {} };
const { toForm } = await import('../web/js/views/environment.js');

test('new and unset draft optimizer connections default to streaming', () => {
  assert.equal(toForm(null).optimizer_config.stream, true);
  assert.equal(toForm({ status: 'draft', optimizer_config: { provider: 'openai_http' } }).optimizer_config.stream, true);
});

test('published missing stream displays effective non-streaming and explicit choices survive reload', () => {
  const published = { status: 'published', optimizer_config: { provider: 'openai_http' } };
  assert.equal(toForm(published).optimizer_config.stream, false);
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
