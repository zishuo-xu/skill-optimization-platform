/** 数据集：版本、导入预检（上传→校验→划分预览→覆盖）、筛选表格、案例侧栏表单/JSON 切换。 */

import { api, request } from '../api_v3.js';
import { h, mount, icon, debounce } from '../dom.js';
import {
  button,
  chip,
  statusBadge,
  pageHeader,
  sectionCard,
  callout,
  emptyState,
  errorState,
  loadingState,
  openModal,
  openDrawer,
  formField,
  toast,
  inlineError,
  jsonViewer,
  codeBlock,
} from '../components.js';
import {
  actionLabel,
  formatDate,
  formatDateTime,
  formatRoutes,
  formatToolCall,
  jsonPretty,
  splitLabel,
  summarizeQuery,
  truncate,
} from '../format.js';
import { ACTION_LABELS } from '../eventTypes.js';

function asItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.items)) return payload.items;
  return [];
}

/** Dataset list rows use flat expected_action/routes/tools; detail rows use `expected`. */
function normalizeDatasetCase(item) {
  if (!item) return item;
  const expected =
    item.expected && typeof item.expected === 'object'
      ? item.expected
      : {
          action: item.expected_action || 'tool_call',
          routes: item.expected_routes || [],
          tool_calls: (item.expected_tools || []).map((name) => ({ name, arguments: {} })),
          missing_fields: [],
          call_match: 'ordered_exact',
        };
  const status =
    item.status ||
    item.validation?.status ||
    (item.validation?.warnings?.length ? 'warning' : 'valid');
  return {
    ...item,
    expected,
    status,
    validation: item.validation && typeof item.validation === 'object' ? item.validation : { status },
  };
}

const SPLITS = ['optimize', 'validation', 'final_test'];

const SPLIT_STYLES = {
  optimize: 'accent',
  validation: 'info',
  final_test: 'warning',
};

export default async function datasetView(container, ctx) {
  const { project } = ctx;
  const state = {
    datasets: [],
    dataset: null,
    coverage: null,
    cases: [],
    total: 0,
    loading: true,
    casesLoading: false,
    error: null,
    filters: { search: '', split: '', tag: '', skill: '', tool: '', status: '' },
    filterOptions: { tags: [], skills: [], tools: [] },
  };

  const versionHost = h('div');
  const summaryHost = h('div');
  const filtersHost = h('div');
  const tableHost = h('div');

  const searchInput = h('input.input', {
    type: 'search',
    placeholder: '搜索 query 或 case_id',
    'aria-label': '搜索案例',
    oninput: debounce((event) => {
      state.filters.search = event.target.value.trim();
      loadCases();
    }, 220),
  });

  function filterSelect(name, options, placeholder) {
    return h(
      'select.select.toolbar-select',
      {
        'aria-label': placeholder,
        onchange: (event) => {
          state.filters[name] = event.target.value;
          loadCases();
        },
      },
      h('option', { value: '', selected: state.filters[name] === '' }, placeholder),
      ...options.map((option) =>
        h('option', { value: option.value, selected: state.filters[name] === option.value }, option.label)
      )
    );
  }

  function renderVersionBar() {
    if (state.loading && !state.datasets.length) {
      mount(versionHost, loadingState('正在加载数据集…', { compact: true }));
      return;
    }
    mount(
      versionHost,
      h(
        'div.dataset-toolbar',
        null,
        h('span.muted.small', { text: '数据集版本' }),
        state.datasets.length
          ? h(
              'select.select',
              {
                style: { width: 'auto', minWidth: '230px' },
                'aria-label': '选择数据集版本',
                onchange: (event) => selectDataset(event.target.value),
              },
              ...state.datasets.map((dataset) =>
                h(
                  'option',
                  { value: dataset.id, selected: state.dataset && dataset.id === state.dataset.id },
                  `v${dataset.version_seq} · ${dataset.case_count || 0} 条 · ${dataset.status === 'draft' ? '草稿' : '已发布'} · ${dataset.source_name || dataset.id}`
                )
              )
            )
          : h('span.muted.small', { text: '尚无数据集版本' }),
        state.dataset ? statusBadge(state.dataset.status, 'version') : null,
        state.dataset && state.dataset.id === project.current_dataset_id ? chip('当前版本', 'accent') : null,
        state.dataset ? h('span.mono.xsmall.muted', { text: state.dataset.hash || '' }) : null,
        state.dataset ? h('span.xsmall.muted', { text: `导入于 ${formatDateTime(state.dataset.created_at)}` }) : null,
        h('span.dataset-toolbar__spacer'),
        state.dataset && state.dataset.status === 'draft'
          ? button({ label: '发布数据集', variant: 'primary', size: 'sm', iconName: 'check', onClick: publishDataset })
          : null,
        button({ label: '刷新', variant: 'ghost', size: 'sm', iconName: 'refresh', onClick: () => load() }),
        button({ label: '导入数据集', variant: 'primary', size: 'sm', iconName: 'upload', onClick: openImportModal })
      )
    );
  }

  function renderSummary() {
    const dataset = state.dataset;
    if (!dataset) {
      mount(summaryHost);
      return;
    }
    const coverage = state.coverage || dataset.coverage || {};
    const splitCounts = coverage.split_counts || dataset.split_counts || {};
    const report = dataset.import_report || {};
    mount(
      summaryHost,
      h(
        'div.dataset-summary',
        null,
        h('span', null, h('strong', { text: '案例总数' }), ` ${dataset.case_count ?? state.total ?? 0}`),
        h('span', null, h('strong', { text: '优化集' }), ` ${splitCounts.optimize ?? 0}`),
        h('span', null, h('strong', { text: '验证集' }), ` ${splitCounts.validation ?? 0}`),
        h('span', null, h('strong', { text: '最终测试集' }), ` ${splitCounts.final_test ?? 0}`),
        report.invalid ? h('span', null, h('strong', { text: '导入时剔除' }), ` ${report.invalid} 条无效记录`) : null,
        h('span', null, h('strong', { text: '标签' }), ` ${(state.filterOptions.tags || []).length} 个`)
      )
    );
  }

  function renderFilters() {
    const options = state.filterOptions;
    mount(
      filtersHost,
      h(
        'div.filter-bar',
        null,
        h('div.search-field', null, icon('search', { size: 15 }), searchInput),
        filterSelect('split', SPLITS.map((split) => ({ value: split, label: splitLabel(split) })), '全部划分'),
        filterSelect('skill', options.skills.map((skill) => ({ value: skill, label: skill })), '全部子技能'),
        filterSelect('tool', options.tools.map((tool) => ({ value: tool, label: tool })), '全部工具'),
        state.filterOptions.tags.length
          ? filterSelect('tag', options.tags.map((tag) => ({ value: tag, label: tag })), '全部标签')
          : null,
        filterSelect(
          'status',
          [
            { value: 'valid', label: '有效' },
            { value: 'warning', label: '有警告' },
          ],
          '全部校验状态'
        ),
        hasActiveFilters()
          ? button({ label: '清除筛选', variant: 'ghost', size: 'sm', iconName: 'x', onClick: clearFilters })
          : null,
        h('span.spacer'),
        state.casesLoading ? h('span.spinner.spinner--sm') : null,
        h('span.muted.xsmall', { text: `匹配 ${state.total} 条` })
      )
    );
  }

  function hasActiveFilters() {
    return Object.values(state.filters).some(Boolean);
  }

  function clearFilters() {
    state.filters = { search: '', split: '', tag: '', skill: '', tool: '', status: '' };
    searchInput.value = '';
    renderFilters();
    loadCases();
  }

  function caseRow(item) {
    const expected = item.expected || {};
    const routes = expected.routes || [];
    const toolCalls = expected.tool_calls || [];
    const tags = item.tags || [];
    const statusValue = item.status || item.validation?.status || 'valid';
    const warning = statusValue === 'warning' || item.validation?.warnings?.length;
    const invalid = statusValue === 'invalid';
    return h(
      'tr.is-clickable',
      {
        tabindex: '0',
        onclick: () => openCaseDrawer(item.case_id),
        onkeydown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openCaseDrawer(item.case_id);
          }
        },
      },
      h('td.cell-mono.nowrap', { text: item.case_id }),
      h(
        'td',
        null,
        h('div.cell-strong', { text: summarizeQuery(item.query, 72) }),
        tags.length
          ? h(
              'div.row',
              { style: { gap: '4px', marginTop: '4px' } },
              ...tags.slice(0, 4).map((tag) => chip(tag, 'neutral'))
            )
          : null
      ),
      h(
        'td',
        null,
        routes.length
          ? h(
              'div.row',
              { style: { gap: '4px' } },
              ...routes.slice(0, 3).map((route) => h('span.route-pill', { text: route.join(' → ') }))
            )
          : h('span.muted.small', { text: actionLabel(expected.action) })
      ),
      h(
        'td.cell-mono',
        null,
        toolCalls.length
          ? h('span', { text: toolCalls.map((call) => call.name).join(', ') })
          : h('span.muted', { text: '—' })
      ),
      h('td.nowrap', null, chip(splitLabel(item.split), SPLIT_STYLES[item.split] || 'neutral')),
      h(
        'td.nowrap',
        null,
        item.critical ? chip('关键案例', 'warning', { iconName: 'alert' }) : null,
        invalid
          ? chip('无效', 'danger', { iconName: 'x' })
          : warning
          ? chip('有警告', 'info', { iconName: 'info' })
          : chip('有效', 'success', { iconName: 'check' })
      )
    );
  }

  function renderTable() {
    if (!state.dataset) {
      mount(
        tableHost,
        emptyState({
          iconName: 'dataset',
          title: '还没有导入数据集',
          description: '导入 JSONL 或 JSON 数组文件。先上传预检，确认错误行、划分预览与覆盖后，再发布为不可变版本。',
          actions: [{ label: '导入数据集', variant: 'primary', iconName: 'upload', onClick: openImportModal }],
        })
      );
      return;
    }
    if (state.casesLoading && !state.cases.length) {
      mount(tableHost, loadingState('正在加载案例…'));
      return;
    }
    if (state.error) {
      mount(tableHost, errorState(state.error, { title: '无法加载案例', retry: () => loadCases() }));
      return;
    }
    if (!state.cases.length) {
      mount(
        tableHost,
        emptyState({
          iconName: 'search',
          title: '没有匹配的案例',
          description: hasActiveFilters() ? '当前筛选条件下没有案例，可以清除筛选后重试。' : '该数据集中没有案例。',
          actions: hasActiveFilters()
            ? [{ label: '清除筛选', variant: 'secondary', onClick: clearFilters }]
            : [{ label: '导入数据集', variant: 'primary', iconName: 'upload', onClick: openImportModal }],
        })
      );
      return;
    }
    mount(
      tableHost,
      h(
        'div',
        null,
        h(
          'div.table-wrap',
          null,
          h(
            'table.data-table',
            null,
            h(
              'thead',
              null,
              h(
                'tr',
                null,
                h('th', { text: 'ID' }),
                h('th', { text: 'query' }),
                h('th', { text: '预期子技能' }),
                h('th', { text: '预期工具' }),
                h('th', { text: '划分' }),
                h('th', { text: '状态' })
              )
            ),
            h('tbody', null, ...state.cases.map(caseRow))
          )
        ),
        h(
          'p.muted.xsmall',
          { style: { marginTop: '10px' } },
          `共 ${state.total} 条匹配案例。已发布数据不可原地修改；修正标签请重新导入生成新版本并重跑基线。`
        )
      )
    );
  }

  async function openCaseDrawer(caseId) {
    let detail = state.cases.find((item) => item.case_id === caseId);
    try {
      const full = await api.getDatasetCase(state.dataset.id, caseId);
      detail = { ...detail, ...full };
    } catch {
      /* fall back to the row data */
    }
    const drawer = openDrawer({
      title: `案例 ${caseId}`,
      subtitle: `${splitLabel(detail.split)} · ${detail.group_id || '无分组'} · ${truncate(detail.query || '', 80)}`,
      size: 'lg',
      body: h('div'),
      footer: h('div.btn-row', null, button({ label: '关闭', variant: 'secondary', onClick: () => drawer.close() })),
    });
    drawer.setBody(buildCaseDetail(detail, drawer));
  }

  function buildCaseDetail(detail, drawer) {
    const host = h('div.case-detail');
    let mode = 'form';
    let jsonText = JSON.stringify(
      {
        case_id: detail.case_id,
        query: detail.query,
        context: detail.context || {},
        expected: detail.expected || {},
        fixtures: detail.fixtures || [],
        split: detail.split,
        group_id: detail.group_id,
        tags: detail.tags || [],
      },
      null,
      2
    );
    let jsonError = '';

    const formView = h('div');
    const jsonView = h('div');
    const errorHost = h('div');
    const editable = state.dataset?.status === 'draft';

    function renderFormView() {
      const expected = detail.expected || {};
      const routes = expected.routes || [];
      const toolCalls = expected.tool_calls || [];
      const fixtures = detail.fixtures || [];
      mount(
        formView,
        h(
          'div',
          null,
          h(
            'div.case-section',
            null,
            h('div.case-section__title', null, h('span', { text: 'Query 与上下文' })),
            h('div.case-detail__query', { text: detail.query || '—' }),
            Object.keys(detail.context || {}).length
              ? h('div', { style: { marginTop: '8px' } }, jsonViewer(detail.context, { label: 'context', maxHeight: '180px' }))
              : null
          ),
          h(
            'div.case-section',
            { style: { marginTop: '16px' } },
            h(
              'div.case-section__title',
              null,
              h('span', { text: '预期路线与动作' }),
              chip(actionLabel(expected.action), 'accent')
            ),
            h(
              'div',
              null,
              routes.length
                ? h('div', null, ...routes.map((route) => h('span.route-pill', { text: route.join(' → ') })))
                : h('p.muted.small', { text: `动作：${ACTION_LABELS[expected.action] || expected.action || '—'}` }),
              expected.missing_fields?.length
                ? h('p.small', null, h('span.muted', { text: '缺少字段：' }), expected.missing_fields.join('、'))
                : null,
              expected.route_ordered !== undefined
                ? h('p.xsmall.muted', { text: `路线顺序：${expected.route_ordered ? '严格顺序' : '集合匹配'}；调用匹配：${expected.call_match || 'ordered_exact'}` })
                : null
            )
          ),
          h(
            'div.case-section',
            { style: { marginTop: '16px' } },
            h('div.case-section__title', null, h('span', { text: '工具调用与参数' })),
            toolCalls.length
              ? h(
                  'div',
                  null,
                  ...toolCalls.map((call, index) => {
                    const args = call.arguments || call.args || {};
                    const objectView = h(
                      'div.form-grid',
                      { style: { marginTop: '8px' } },
                      ...Object.entries(args).map(([key, value]) =>
                        h(
                          'div.form-field',
                          null,
                          h('label.form-field__label', { text: key }),
                          h('input.input', { value: stringifyValue(value), readonly: true })
                        )
                      )
                    );
                    return h(
                      'div.tool-call-block',
                      null,
                      h(
                        'div.tool-call-block__head',
                        null,
                        h('span', { text: `#${index + 1} ${call.name}` }),
                        call.arguments_path
                          ? chip(`仅比较 arguments.${call.arguments_path}`, 'neutral')
                          : null,
                        h('span.spacer'),
                        h('span.xsmall.muted', { text: `${Object.keys(args).length} 个参数` })
                      ),
                      h(
                        'div.tool-call-block__body',
                        null,
                        objectView,
                        Object.entries(args).some(([, value]) => value && typeof value === 'object')
                          ? jsonViewer(args, { label: '完整参数 JSON', maxHeight: '200px' })
                          : null
                      )
                    );
                  })
                )
              : h('p.muted.small', { text: '该案例不需要业务工具调用。' })
          ),
          h(
            'div.case-section',
            { style: { marginTop: '16px' } },
            h('div.case-section__title', null, h('span', { text: '模拟返回（fixtures）' })),
            fixtures.length
              ? h(
                  'div',
                  null,
                  ...fixtures.map((fixture) => {
                    const responseText = jsonPretty(fixture.response ?? fixture.result ?? {});
                    return h(
                      'div.fixture-block',
                      null,
                      h('div.fixture-block__head', {
                        text: `${fixture.name || 'fixture'}${fixture.match ? ` · match ${JSON.stringify(fixture.match)}` : ''}${fixture.consume ? ` · consume=${fixture.consume}` : ''}`,
                      }),
                      h(
                        'div.fixture-block__body',
                        null,
                        h(
                          'button.code-block__label',
                          {
                            type: 'button',
                            style: { border: 'none', background: 'transparent', color: 'var(--accent)', padding: '0 0 4px', cursor: 'pointer' },
                            onclick: (event) => {
                              const pre = event.currentTarget.nextElementSibling;
                              if (pre) pre.hidden = !pre.hidden;
                            },
                          },
                          '展开 / 收起返回内容'
                        ),
                        h('pre.code-block__pre', { hidden: true, style: { maxHeight: '260px', whiteSpace: 'pre-wrap' }, text: responseText })
                      )
                    );
                  })
                )
              : h('p.muted.small', { text: '该案例没有配置模拟返回；未匹配的工具调用会立即记录 fixture_miss。' })
          ),
          h(
            'p.muted.xsmall',
            { style: { marginTop: '16px' } },
            editable ? '当前数据集为草稿，可在 JSON 视图中编辑后保存。' : '已发布数据不可原地修改；如需修正标签，请导入新版本。'
          )
        )
      );
    }

    function renderJsonView() {
      const textarea = h('textarea.json-textarea', {
        rows: 22,
        style: { minHeight: '420px' },
        readonly: !editable,
        'aria-label': '案例 JSON',
        value: jsonText,
        oninput: (event) => {
          jsonText = event.target.value;
        },
      });
      mount(
        jsonView,
        h(
          'div.stack',
          null,
          h(
            'p.small.muted',
            null,
            editable
              ? '修改 JSON 后点击“保存修改”；切换回表单前会验证结构。'
              : '只读 JSON。复杂字段保留 JSON 编辑能力；已发布版本不可修改。'
          ),
          textarea,
          errorHost
        )
      );
    }

    function validateJson() {
      try {
        const parsed = JSON.parse(jsonText);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('JSON 顶层必须是对象');
        if (typeof parsed.query !== 'string' || !parsed.query.trim()) throw new Error('缺少 query');
        if (!parsed.expected || typeof parsed.expected !== 'object') throw new Error('缺少 expected');
        return parsed;
      } catch (error) {
        jsonError = `JSON 校验失败：${error.message}`;
        return null;
      }
    }

    function setMode(nextMode) {
      if (nextMode === mode) return;
      if (nextMode === 'form') {
        const parsed = validateJson();
        if (!parsed) {
          mount(errorHost, inlineError(jsonError));
          return;
        }
        detail = { ...detail, ...parsed };
        renderFormView();
      }
      mode = nextMode;
      modeToggle.querySelectorAll('button').forEach((buttonEl) => {
        buttonEl.classList.toggle('is-active', buttonEl.dataset.mode === mode);
      });
      formView.hidden = mode !== 'form';
      jsonView.hidden = mode !== 'json';
      mount(errorHost);
      if (mode === 'form') jsonError = '';
    }

    const modeToggle = h(
      'div.form-json-toggle',
      { role: 'group', 'aria-label': '表单或 JSON 视图' },
      h('button.is-active', { type: 'button', dataset: { mode: 'form' }, onclick: () => setMode('form') }, '表单'),
      h('button', { type: 'button', dataset: { mode: 'json' }, onclick: () => setMode('json') }, 'JSON')
    );

    renderFormView();
    renderJsonView();
    jsonView.hidden = true;

    const saveButton = editable
      ? button({
          label: '保存修改',
          variant: 'primary',
          size: 'sm',
          iconName: 'save',
          onClick: async () => {
            const parsed = validateJson();
            if (!parsed) {
              mount(errorHost, inlineError(jsonError));
              return;
            }
            try {
              await request('PUT', `/datasets/${encodeURIComponent(state.dataset.id)}/cases/${encodeURIComponent(detail.case_id)}`, {
                body: { case: parsed },
              });
              detail = { ...detail, ...parsed };
              renderFormView();
              toast('案例已保存', { type: 'success' });
              loadCases();
            } catch (error) {
              mount(errorHost, inlineError(error.message));
            }
          },
        })
      : null;

    mount(
      host,
      h('div.row.row--between', null, modeToggle, editable ? saveButton : chip('只读', 'neutral', { iconName: 'lock' })),
      formView,
      jsonView,
      errorHost
    );
    return host;
  }

  function stringifyValue(value) {
    if (value === null) return 'null';
    if (value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function renderImportStep1(modal, data) {
    const fileState = { file: null, content: '', filename: '' };
    const zoneText = h('div', null, h('div', { text: '点击选择文件，或将 JSONL / JSON 文件拖到此处' }), h('div.xsmall.muted', { style: { marginTop: '4px' }, text: '支持 .jsonl 与 .json（数组）；容忍 BOM 与空行。' }));
    const fileLabel = h('div.upload-zone__file');
    const fileInput = h('input', { type: 'file', accept: '.jsonl,.json,application/json', style: { display: 'none' } });
    const zone = h(
      'div.upload-zone',
      {
        role: 'button',
        tabindex: '0',
        onclick: () => fileInput.click(),
        onkeydown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') fileInput.click();
        },
        ondragover: (event) => {
          event.preventDefault();
          zone.classList.add('is-dragover');
        },
        ondragleave: () => zone.classList.remove('is-dragover'),
        ondrop: (event) => {
          event.preventDefault();
          zone.classList.remove('is-dragover');
          if (event.dataTransfer.files.length) handleFile(event.dataTransfer.files[0]);
        },
      },
      icon('upload', { size: 22 }),
      zoneText,
      fileLabel,
      fileInput
    );

    const optimizeInput = h('input.input', { type: 'number', value: '70', min: '0', max: '100' });
    const validationInput = h('input.input', { type: 'number', value: '20', min: '0', max: '100' });
    const finalInput = h('input.input', { type: 'number', value: '10', min: '0', max: '100' });
    const seedInput = h('input.input', { type: 'number', value: '42', min: '0' });
    const sourceInput = h('input.input', { type: 'text', placeholder: '例如 customer_service_cases_v2.jsonl' });
    const errorHost = h('div');
    let busy = false;

    function splitConfig() {
      return {
        optimize: Number(optimizeInput.value) / 100,
        validation: Number(validationInput.value) / 100,
        final_test: Number(finalInput.value) / 100,
        seed: Number(seedInput.value),
      };
    }

    function readFile(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('读取文件失败'));
        reader.readAsText(file, 'utf-8');
      });
    }

    async function handleFile(file) {
      if (!/\.(jsonl|json)$/i.test(file.name)) {
        mount(errorHost, inlineError('请选择 .jsonl 或 .json 文件'));
        return;
      }
      try {
        fileState.file = file;
        fileState.filename = file.name;
        fileState.content = await readFile(file);
        fileLabel.textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
        sourceInput.value = sourceInput.value || file.name;
        mount(errorHost);
      } catch (error) {
        mount(errorHost, inlineError(error.message));
      }
    }

    fileInput.addEventListener('change', () => {
      if (fileInput.files && fileInput.files[0]) handleFile(fileInput.files[0]);
    });

    const nextButton = button({ label: '下一步：预检', variant: 'primary', iconName: 'arrowRight', onClick: () => runPreflight() });

    async function runPreflight() {
      if (busy) return;
      if (!fileState.content) {
        mount(errorHost, inlineError('请先选择要导入的文件'));
        return;
      }
      const config = splitConfig();
      const sum = config.optimize + config.validation + config.final_test;
      if (Math.abs(sum - 1) > 0.001) {
        mount(errorHost, inlineError(`划分比例之和必须为 100%，当前为 ${Math.round(sum * 100)}%`));
        return;
      }
      busy = true;
      nextButton.disabled = true;
      nextButton.textContent = '正在校验…';
      mount(errorHost);
      try {
        const result = await api.preflightDataset({
          filename: fileState.filename,
          content: fileState.content,
          splitConfig: config,
          seed: config.seed,
        });
        data.preflight = result;
        data.filename = fileState.filename;
        data.content = fileState.content;
        data.splitConfig = config;
        data.sourceName = sourceInput.value || fileState.filename;
        renderImportStep2(modal, data);
      } catch (error) {
        mount(errorHost, inlineError(error.message));
      } finally {
        busy = false;
        nextButton.disabled = false;
        nextButton.textContent = '下一步：预检';
      }
    }

    mount(
      modal.body,
      h(
        'div.stack',
        null,
        h('div.import-steps', null, h('span.import-step.is-active', null, h('span.import-step__num', { text: '1' }), h('span', { text: '上传文件' })), h('span.import-step', null, h('span.import-step__num', { text: '2' }), h('span', { text: '校验与划分预览' }))),
        zone,
        h('div.form-grid.form-grid--3', null, formField({ label: '优化集 %', control: optimizeInput }), formField({ label: '验证集 %', control: validationInput }), formField({ label: '最终测试集 %', control: finalInput })),
        h('div.form-grid', null, formField({ label: '随机种子', control: seedInput, hint: '固定种子保证划分可复现；按 group_id 分组，不跨集合。' }), formField({ label: '来源文件名', control: sourceInput, hint: '仅用于版本记录展示。' })),
        errorHost,
        h('p.xsmall.muted', { text: '小数据集允许只有优化集与验证集；没有最终测试集时会提示，但可以继续。' })
      )
    );
    modal.setFooter(
      h(
        'div.btn-row',
        null,
        button({ label: '取消', variant: 'ghost', onClick: () => modal.close() }),
        nextButton
      )
    );
  }

  function renderImportStep2(modal, data) {
    const preflight = data.preflight || {};
    const stats = preflight.stats || {};
    const errors = preflight.errors || [];
    const warnings = preflight.warnings || [];
    const coverage = preflight.coverage || stats.coverage || {};
    const splitCounts = stats.splits || coverage.split_counts || {};
    const invalid = Number(stats.invalid || errors.length || 0);
    const excludeInput = h('input', { type: 'checkbox', checked: false });
    const errorHost = h('div');
    let busy = false;

    const publishButton = button({
      label: invalid ? `剔除 ${invalid} 条无效记录并发布` : '发布数据集',
      variant: 'primary',
      iconName: 'check',
      onClick: () => publish(),
    });

    async function publish() {
      if (busy) return;
      if (invalid > 0 && !excludeInput.checked) {
        mount(errorHost, inlineError('存在无效记录，请勾选剔除后发布，或返回修复文件。'));
        return;
      }
      busy = true;
      publishButton.disabled = true;
      publishButton.textContent = '正在发布…';
      try {
        await api.createDataset(project.id, {
          filename: data.filename,
          content: data.content,
          splitConfig: data.splitConfig,
          seed: data.splitConfig.seed,
          excludeInvalid: excludeInput.checked,
          sourceName: data.sourceName,
        });
        modal.close();
        toast('数据集版本已发布', { type: 'success' });
        await load();
        await ctx.refreshShell();
      } catch (error) {
        const nested = error.details?.errors || [];
        mount(
          errorHost,
          h(
            'div',
            null,
            inlineError(error.message),
            nested.length
              ? h(
                  'ul.small',
                  { style: { paddingLeft: '1.2em', color: 'var(--text-2)' } },
                  ...nested.slice(0, 12).map((issue) => h('li', { text: `第 ${issue.line ?? '?'} 行：${issue.message || issue.code}` }))
                )
              : null
          )
        );
      } finally {
        busy = false;
        publishButton.disabled = false;
        publishButton.textContent = invalid ? `剔除 ${invalid} 条无效记录并发布` : '发布数据集';
      }
    }

    const statCard = (label, value, tone = '') =>
      h(
        `div.import-stat${tone ? ` import-stat--${tone}` : ''}`,
        null,
        h('div.import-stat__label', { text: label }),
        h('div.import-stat__value', { text: String(value) })
      );

    mount(
      modal.body,
      h(
        'div.import-result',
        null,
        h(
          'div.import-steps',
          null,
          h('span.import-step.is-done', null, h('span.import-step__num', { text: '✓' }), h('span', { text: '上传文件' })),
          h('span.import-step.is-active', null, h('span.import-step__num', { text: '2' }), h('span', { text: '校验与划分预览' }))
        ),
        h(
          'div.import-stats',
          null,
          statCard('总记录', stats.total ?? 0),
          statCard('有效', stats.valid ?? 0, 'success'),
          statCard('无效', invalid, invalid ? 'danger' : ''),
          statCard('警告', warnings.length, warnings.length ? 'warning' : '')
        ),
        h(
          'div',
          null,
          h('h3.section-card__heading', { style: { fontSize: '13px', marginBottom: '8px' }, text: '划分预览' }),
          h(
            'div.split-preview',
            null,
            ...SPLITS.map((split) =>
              h(
                'div.split-preview__item',
                null,
                h('div.split-preview__label', { text: splitLabel(split) }),
                h('div.split-preview__value', { text: String(splitCounts[split] ?? 0) })
              )
            )
          ),
          splitCounts.final_test
            ? null
            : h('p.xsmall', { style: { color: 'var(--warning)', marginTop: '6px' }, text: '没有最终测试集样本；可继续，但阶段验收前需要补充独立留出集。' })
        ),
        coverage.skills
          ? h(
              'div',
              null,
              h('h3', { style: { fontSize: '13px', margin: '0 0 8px' }, text: '子技能覆盖' }),
              h(
                'div.coverage-list',
                null,
                ...Object.entries(coverage.skills).map(([skillId, counts]) =>
                  h(
                    'div.coverage-row',
                    null,
                    h('span.mono', { text: skillId }),
                    h('span.coverage-row__value', {
                      text: counts.total ? `优化 ${counts.optimize} / 验证 ${counts.validation} / 最终 ${counts.final_test}` : '未评估',
                    })
                  )
                )
              )
            )
          : null,
        errors.length
          ? h(
              'div',
              null,
              h('h3', { style: { fontSize: '13px', margin: '0 0 8px' }, text: '错误行（修复或剔除后才能发布）' }),
              h(
                'div.error-table',
                null,
                h(
                  'table.data-table.data-table--compact',
                  null,
                  h('thead', null, h('tr', null, h('th', { text: '行' }), h('th', { text: 'case_id' }), h('th', { text: '错误码' }), h('th', { text: '说明' }), h('th', { text: '字段' }))),
                  h(
                    'tbody',
                    null,
                    ...errors.map((issue) =>
                      h(
                        'tr',
                        null,
                        h('td.cell-mono', { text: String(issue.line ?? '—') }),
                        h('td.cell-mono', { text: issue.case_id || '—' }),
                        h('td.cell-mono', { text: issue.code || '—' }),
                        h('td', { text: issue.message || '' }),
                        h('td.cell-mono', { text: issue.field_path || '—' })
                      )
                    )
                  )
                )
              ),
              h(
                'label.checkbox-line',
                { style: { marginTop: '8px' } },
                excludeInput,
                h('span', { text: `剔除 ${invalid} 条无效记录，仅发布有效案例` })
              )
            )
          : null,
        warnings.length
          ? h(
              'div',
              null,
              h('h3', { style: { fontSize: '13px', margin: '0 0 8px' }, text: '警告（不阻止发布）' }),
              h(
                'ul.small',
                { style: { paddingLeft: '1.2em', color: 'var(--text-2)' } },
                ...warnings.slice(0, 12).map((warning) =>
                  h('li', {
                    text: warning.line ? `第 ${warning.line} 行：${warning.message || warning.code}` : warning.message || warning.code,
                  })
                )
              )
            )
          : null,
        h(
          'p.xsmall.muted',
          null,
          `划分配置：优化 ${Math.round((data.splitConfig.optimize || 0) * 100)}% / 验证 ${Math.round((data.splitConfig.validation || 0) * 100)}% / 最终 ${Math.round((data.splitConfig.final_test || 0) * 100)}%，种子 ${data.splitConfig.seed}`
        ),
        errorHost
      )
    );

    modal.setFooter(
      h(
        'div.btn-row',
        null,
        button({ label: '返回修改', variant: 'ghost', onClick: () => renderImportStep1(modal, {}) }),
        publishButton
      )
    );
  }

  function openImportModal() {
    const modal = openModal({
      title: '导入数据集',
      subtitle: '先上传并预检，确认错误、划分与覆盖后再发布为不可变版本。',
      size: 'lg',
      body: h('div'),
      footer: h('div.btn-row', null, button({ label: '取消', variant: 'ghost', onClick: () => modal.close() })),
    });
    renderImportStep1(modal, {});
  }

  async function publishDataset() {
    const ok = await confirmDialog({
      title: '发布数据集版本',
      message: '发布后数据不可原地修改，并会成为项目当前数据集。修正标签需要重新导入新版本并重跑基线。',
      confirmText: '发布并设为当前',
    });
    if (!ok) return;
    try {
      await api.publishDataset(state.dataset.id);
      toast('数据集已设为当前版本', { type: 'success' });
      await load(state.dataset.id);
      await ctx.refreshShell();
    } catch (error) {
      toast(`发布失败：${error.message}`, { type: 'error' });
    }
  }

  function computeFilterOptions(items) {
    const tags = new Set();
    const skills = new Set();
    const tools = new Set();
    for (const item of items) {
      for (const tag of item.tags || []) tags.add(tag);
      for (const route of (item.expected || {}).routes || []) for (const skill of route) skills.add(skill);
      for (const call of (item.expected || {}).tool_calls || []) if (call.name) tools.add(call.name);
    }
    return { tags: [...tags].sort(), skills: [...skills].sort(), tools: [...tools].sort() };
  }

  async function loadCases() {
    if (!state.dataset) return;
    state.casesLoading = true;
    state.error = null;
    renderFilters();
    renderTable();
    try {
      const payload = await api.listDatasetCases(state.dataset.id, {
        search: state.filters.search || undefined,
        split: state.filters.split || undefined,
        tag: state.filters.tag || undefined,
        limit: 200,
        offset: 0,
      });
      let items = asItems(payload).map(normalizeDatasetCase);
      state.total = payload?.total ?? items.length;
      const allItems = items.slice();
      if (state.filters.skill) {
        items = items.filter((item) => ((item.expected || {}).routes || []).some((route) => route.includes(state.filters.skill)));
      }
      if (state.filters.tool) {
        items = items.filter((item) => ((item.expected || {}).tool_calls || []).some((call) => call.name === state.filters.tool));
      }
      if (state.filters.status === 'warning') {
        items = items.filter((item) => item.status === 'warning' || item.validation?.status === 'warning' || item.validation?.warnings?.length);
      }
      if (state.filters.status === 'valid') {
        items = items.filter((item) => item.status !== 'warning' && item.status !== 'invalid');
      }
      state.cases = items;
      const options = computeFilterOptions(allItems);
      state.filterOptions = {
        tags: options.tags.length ? options.tags : state.filterOptions.tags,
        skills: options.skills.length ? options.skills : state.filterOptions.skills,
        tools: options.tools.length ? options.tools : state.filterOptions.tools,
      };
      renderFilters();
      renderSummary();
      renderTable();
    } catch (error) {
      state.error = error;
      renderTable();
    } finally {
      state.casesLoading = false;
      renderFilters();
    }
  }

  async function selectDataset(datasetId) {
    const dataset = state.datasets.find((item) => item.id === datasetId);
    if (!dataset) return;
    state.dataset = dataset;
    state.filters = { search: '', split: '', tag: '', skill: '', tool: '', status: '' };
    searchInput.value = '';
    renderVersionBar();
    renderSummary();
    await loadDatasetDetail();
    await loadCases();
  }

  async function loadDatasetDetail() {
    if (!state.dataset) return;
    try {
      const detail = await api.getDataset(state.dataset.id);
      state.dataset = { ...state.dataset, ...detail };
      state.coverage = detail.coverage || null;
      renderSummary();
    } catch {
      state.coverage = state.dataset.coverage || null;
    }
  }

  async function load(preferredId = null) {
    state.loading = true;
    state.error = null;
    renderVersionBar();
    try {
      const payload = await api.listDatasets(project.id);
      state.datasets = asItems(payload).sort((a, b) => (b.version_seq || 0) - (a.version_seq || 0));
      let target = preferredId ? state.datasets.find((item) => item.id === preferredId) : null;
      if (!target) target = state.datasets.find((item) => item.id === project.current_dataset_id) || state.datasets[0] || null;
      state.dataset = target;
      renderVersionBar();
      renderSummary();
      if (target) {
        await loadDatasetDetail();
        await loadCases();
      } else {
        renderTable();
      }
    } catch (error) {
      state.error = error;
      renderTable();
    } finally {
      state.loading = false;
      renderVersionBar();
    }
  }

  mount(
    container,
    pageHeader({
      title: '数据集',
      description: '导入、校验并发布案例集合；已发布数据不可原地修改。',
    }),
    versionHost,
    summaryHost,
    filtersHost,
    tableHost
  );

  renderFilters();
  await load();
  return () => {};
}
