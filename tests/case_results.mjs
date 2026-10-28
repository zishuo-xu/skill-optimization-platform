import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.document = { addEventListener() {} };
const { matchToolResults } = await import('../web/js/views/caseView.js');

test('matches repeated tools by full arguments, retaining null responses and record order', () => {
  const calls = [
    { name: 'edit', arguments: { method: 'crop', args: { b: 2, a: 1 } } },
    { name: 'edit', arguments: { method: 'tone' } },
    { name: 'edit', arguments: { method: 'tone' } },
  ];
  const results = [
    { name: 'edit', arguments: { method: 'tone' }, response: null },
    { name: 'edit', arguments: { args: { a: 1, b: 2 }, method: 'crop' }, response: { ok: true } },
    { name: 'edit', arguments: { method: 'tone' }, response: 0 },
  ];
  const { matched, unmatched } = matchToolResults(calls, results);
  assert.deepEqual(matched.map((result) => result.response), [{ ok: true }, null, 0]);
  assert.deepEqual(unmatched, []);
});

test('does not invent returns or guess between indistinguishable incomplete calls', () => {
  const calls = [{ name: 'edit', arguments: { id: 1 } }, { name: 'edit', arguments: { id: 1 } }, { name: 'other', arguments: {} }];
  const result = { name: 'edit', arguments: { id: 1 }, response: null };
  assert.deepEqual(matchToolResults(calls, [result]), { matched: [null, null, null], unmatched: [result] });
  assert.deepEqual(matchToolResults([{ name: 'edit', arguments: { id: 2 } }], [result]), { matched: [null], unmatched: [result] });
});

test('actual column shows final response for clarification and null return separately from absent records', async () => {
  class FakeNode {
    constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.dataset = {}; this.textContent = ''; }
    appendChild(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener() {}
  }
  globalThis.Node = FakeNode;
  document.createElement = (tag) => new FakeNode(tag);
  document.createTextNode = (text) => { const n = new FakeNode('#text'); n.textContent = text; return n; };
  const { actualColumn } = await import('../web/js/views/caseView.js');
  const content = (node) => [node.textContent, ...node.children.map(content)].join(' ');
  const view = actualColumn({ action: 'clarify', missing_fields: ['id'], tool_calls: [{ name: 'edit', arguments: {} }, { name: 'other', arguments: {} }], tool_results: [{ name: 'edit', arguments: {}, response: null }], final_response: '请提供图片 ID', status: null, passed: null }, '实际');
  const text = content(view);
  assert.match(text, /请提供图片 ID/);
  assert.match(text, /工具返回（执行记录，不计入参数评分）/);
  assert.match(text, /null/);
  assert.match(text, /未记录工具返回/);
  const failed = content(actualColumn({action: 'tool_call', tool_calls: [{name: 'edit', arguments: {}}], tool_results: [{name: 'edit', arguments: {}, error: 'fixture_miss'}]}, '实际'));
  assert.match(failed, /模拟返回未匹配：已尝试调用/);
  assert.doesNotMatch(failed, /未记录工具返回/);
});
