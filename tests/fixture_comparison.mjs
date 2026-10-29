import test from 'node:test';
import assert from 'node:assert/strict';
import { fixtureComparison } from '../web/js/fixtureComparison.js';

test('fixture comparison labels conditions separately from business expected values', () => {
  class FakeNode {
    constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.dataset = {}; this.textContent = ''; }
    appendChild(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener() {}
  }
  globalThis.Node = FakeNode;
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => { const n = new FakeNode('#text'); n.textContent = text; return n; },
  };
  const content = (node) => [node.textContent, ...node.children.map(content)].join(' ');
  const view = fixtureComparison([{fixture_index: 0, field_path: '$.method', actual: '"getVersion"', expected: '"applyStyle"', condition: '"applyStyle"'}], '不是评分标准答案');
  const text = content(view);
  for (const label of ['实际调用值', '业务预期（案例声明）', '模拟匹配条件', 'getVersion', 'applyStyle', '$.method', '不是评分标准答案']) assert.ok(text.includes(label));
  assert.equal(fixtureComparison(), null);
  assert.ok(content(fixtureComparison([], '无法恢复')).includes('无法恢复'));
});
