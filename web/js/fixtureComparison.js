import { h } from './dom.js';

/** A fixture condition is not an expected business answer. Keep labels explicit. */
export function fixtureComparison(rows = [], note = '') {
  if (!rows.length && !note) return null;
  return h('div.stack.stack--sm', null,
    ...rows.map((row) => h('div.param-diff.fixture-comparison__row', null,
      h('div.param-diff__path', { text: `${row.field_path} · 模拟条件 #${row.fixture_index + 1}` }),
      h('div.param-diff__cell', null,
        h('span.param-diff__label', { text: '实际调用值' }),
        h('span.param-diff__value.is-actual', { text: row.actual })),
      h('div.param-diff__cell', null,
        h('span.param-diff__label', { text: '业务预期（案例声明）' }),
        h('span.param-diff__value.is-expected', { text: row.expected || '未记录该字段的业务预期，请核对 Skill 规则' })),
      h('div.param-diff__cell', null,
        h('span.param-diff__label', { text: '模拟匹配条件' }),
        h('span.param-diff__value', { text: row.condition })))),
    note ? h('p.small.muted', { text: note }) : null);
}
