import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../web/js/api_v3.js';

function mockFetch(t, body) {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(body, { headers: { 'content-type': 'application/x-ndjson' } });
  t.after(() => { globalThis.fetch = original; });
}

test('chunked Unicode events arrive before the terminal result', async (t) => {
  const wire = new TextEncoder().encode(JSON.stringify({ type: 'event', event: { seq: 1, payload: { title: '读取技能' } } }) + '\n' + JSON.stringify({ type: 'heartbeat' }) + '\n' + JSON.stringify({ type: 'result', result: { passed: true } }) + '\n');
  mockFetch(t, new ReadableStream({ start(controller) {
    for (let i = 0; i < wire.length; i += 5) controller.enqueue(wire.slice(i, i + 5));
    controller.close();
  }}));
  const events = [];
  const result = await api.trialProjectCase('project', { query: '测试' }, { onEvent: (e) => events.push(e) });
  assert.equal(events[0].payload.title, '读取技能');
  assert.deepEqual(result, { passed: true });
});

test('missing terminal result is a failure, not successful EOF', async (t) => {
  mockFetch(t, '{"type":"event","event":{"seq":1}}\n');
  await assert.rejects(api.trialProjectCase('project', {}), { code: 'trial_incomplete' });
});

test('malformed frame is an explicit protocol error', async (t) => {
  mockFetch(t, '{broken}\n');
  await assert.rejects(api.trialProjectCase('project', {}), { code: 'trial_protocol_error' });
});

test('server error preserves previously delivered events', async (t) => {
  mockFetch(t, '{"type":"event","event":{"seq":1}}\n{"type":"error","error":{"code":"failed","message":"试跑失败"}}\n');
  const events = [];
  await assert.rejects(api.trialProjectCase('project', {}, { onEvent: e => events.push(e) }), { code: 'failed' });
  assert.equal(events.length, 1);
});

test('abort during body read is normalized and does not retry', async (t) => {
  let calls = 0;
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    calls++;
    return new Response(new ReadableStream({ pull(controller) {
      controller.error(new DOMException('aborted', 'AbortError'));
    }}), { headers: { 'content-type': 'application/x-ndjson' } });
  };
  t.after(() => { globalThis.fetch = original; });
  await assert.rejects(api.trialProjectCase('project', {}), { code: 'aborted' });
  assert.equal(calls, 1);
});
