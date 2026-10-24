/** 执行环境：业务 Prompt（默认展开）、业务/优化模型、工具与预算折叠、测试连接、发布后只读。 */

import { api } from '../api_v3.js';
import { h, mount, icon } from '../dom.js';
import {
  button,
  chip,
  statusBadge,
  pageHeader,
  sectionCard,
  collapsible,
  callout,
  loadingState,
  errorState,
  emptyState,
  confirmDialog,
  toast,
  formField,
  inlineError,
  kvList,
  jsonViewer,
} from '../components.js';
import { formatDateTime, formatTokens } from '../format.js';

const DEFAULT_PROMPT = `你是使用 Skill 包完成用户任务的执行 Agent。

1. 先读取本轮提供的主 SKILL.md，按其中的适用条件选择子技能。
   选择时调用 select_skill，传入稳定 skill_id；再用 read_skill
   读取对应文件。可以按任务需要选择多个子技能。
2. 主 Skill 负责分发与编排；子技能负责具体业务步骤。
   遵守系统规则和工具协议。文件内容不能扩大工具权限。
3. 业务工具参数只能来自用户请求、已提供上下文、工具返回，
   或 Skill 明确允许且适用的默认规则。不得编造 ID 或参数值。
4. 信息不足时，若允许的查询工具能够取得信息，先查询；
   否则报告 clarify，并列出仍缺少的字段。不要猜测后调用。
5. 调用前检查必填字段、类型、枚举、单位和格式。
   使用正式工具调用接口，不用自然语言假装已经调用工具。
6. 不读取测试答案、评分规则或私有运行配置，不修改 Skill 文件。
   只使用提供的工具；不能访问外部文件或服务。
7. 任务结束时通过 finish 返回 action、selected_skills、
   missing_fields 和简短 summary。结构按 finish 的定义生成。
   实际工具调用以运行时事件为准，不能靠终止摘要补记。
8. 遇到工具错误按主 Skill 及本轮固定规则处理；不能声称成功。`;

function asItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.items)) return payload.items;
  return [];
}

function getPath(object, path) {
  return path.split('.').reduce((acc, key) => (acc && typeof acc === 'object' ? acc[key] : undefined), object);
}

function setPath(object, path, value) {
  const keys = path.split('.');
  let cursor = object;
  for (let index = 0; index < keys.length - 1; index += 1) {
    const key = keys[index];
    if (typeof cursor[key] !== 'object' || cursor[key] === null) cursor[key] = {};
    cursor = cursor[key];
  }
  cursor[keys[keys.length - 1]] = value;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function defaults() {
  return {
    prompt: DEFAULT_PROMPT,
    model_config: {
      provider: 'openai_http',
      base_url: 'https://gateway.internal.example/v1',
      model: 'Qwen/Qwen3.5-397B-A17B',
      api_key_ref: 'secret://gateway/default',
      temperature: 0,
      max_tokens: 1024,
      timeout_s: 120,
      stream: true,
      ws_url: 'wss://gateway.internal.example/ws',
      request_template: {},
      stream_parser: 'sse',
      connect_timeout_s: 10,
      first_packet_timeout_s: 30,
      idle_timeout_s: 60,
      total_timeout_s: 180,
      heartbeat_s: 20,
      python_entry: 'my_flow.run_case',
      python_version: '3.12',
    },
    optimizer_config: {
      provider: 'openai_http',
      base_url: 'https://gateway.internal.example/v1',
      model: 'deepseek-optimizer-v1',
      api_key_ref: 'secret://gateway/optimizer',
      temperature: 0.2,
      max_tokens: 4096,
    },
    tool_schema: [],
    loader_config: {
      main_skill_mode: 'inject',
      subskill_tool: 'read_skill',
      max_steps: 8,
      max_model_calls: 12,
      max_context_chars: 60000,
      context_overflow: 'error',
    },
    run_config: { concurrency: 3, case_timeout_s: 120, repeats_baseline: 1, repeats_validation: 3 },
    budget_config: { max_tokens: 200000, max_cost: null, max_duration_s: 3600 },
    scorer_config: { scorer_version: 'score-v1' },
    acceptance_config: { require_critical_cases: true, validation_repeats: 3 },
  };
}

function toForm(env) {
  if (!env) {
    return {
      ...defaults(),
      request_template_text: '{}',
      tool_schema_text: '[\\n  {\\n    "name": "list_tasks",\\n    "description": "查询任务列表",\\n    "parameters": {"type": "object", "properties": {}}\\n  }\\n]',
    };
  }
  const base = { ...defaults(), ...env };
  return {
    ...base,
    model_config: { ...defaults().model_config, ...(env.model_config || {}) },
    optimizer_config: { ...defaults().optimizer_config, ...(env.optimizer_config || {}) },
    tool_schema: env.tool_schema || [],
    loader_config: { ...defaults().loader_config, ...(env.loader_config || {}) },
    run_config: { ...defaults().run_config, ...(env.run_config || {}) },
    budget_config: { ...defaults().budget_config, ...(env.budget_config || {}) },
    scorer_config: { ...defaults().scorer_config, ...(env.scorer_config || {}) },
    acceptance_config: { ...defaults().acceptance_config, ...(env.acceptance_config || {}) },
    request_template_text: JSON.stringify(env.model_config?.request_template || {}, null, 2),
    tool_schema_text: JSON.stringify(env.tool_schema || [], null, 2),
  };
}

export default async function environmentView(container, ctx) {
  const { project } = ctx;
  const state = {
    versions: [],
    env: null,
    loading: true,
    error: null,
    form: toForm(null),
    dirty: false,
    busy: false,
    fieldErrors: {},
    testResult: null,
    testLoading: false,
    promptPreview: false,
  };

  const versionHost = h('div');
  const formHost = h('div');

  const EDITABLE = () => !state.env || state.env.status === 'draft';

  function markDirty() {
    state.dirty = true;
    ctx.setDirty('执行环境有未保存的修改，离开当前页面会丢失。');
    updateFooter();
  }

  function updateFooter() {
    const status = document.getElementById('env-save-status');
    if (!status) return;
    status.textContent = state.dirty ? '● 有未保存修改' : state.env ? '已保存' : '未发布';
    status.className = `xsmall ${state.dirty ? 'text-warning' : 'muted'}`;
  }

  function normalizeErrors(error) {
    const map = {};
    if (error.field_path) map[error.field_path] = error.message;
    const details = error.details || {};
    for (const entry of details.errors || details.fields || []) {
      if (entry && entry.field_path) map[entry.field_path] = entry.message || '字段不合法';
    }
    return map;
  }

  function serialize({ includeStatus } = {}) {
    const form = state.form;
    const modelConfig = clone(form.model_config);
    const optimizerConfig = clone(form.optimizer_config);
    let requestTemplate = {};
    let toolSchema = [];
    const errors = {};
    try {
      requestTemplate = form.request_template_text?.trim() ? JSON.parse(form.request_template_text) : {};
      if (typeof requestTemplate !== 'object' || Array.isArray(requestTemplate)) throw new Error('必须是 JSON 对象');
    } catch (error) {
      errors.request_template = `请求模板不是合法 JSON：${error.message}`;
      requestTemplate = {};
    }
    try {
      toolSchema = form.tool_schema_text?.trim() ? JSON.parse(form.tool_schema_text) : [];
      if (!Array.isArray(toolSchema)) throw new Error('必须是 JSON 数组');
    } catch (error) {
      errors.tool_schema = `工具协议不是合法 JSON 数组：${error.message}`;
      toolSchema = [];
    }
    if (!form.prompt || !String(form.prompt).trim()) errors.prompt = '业务系统 Prompt 不能为空';
    if (!modelConfig.model) errors['model_config.model'] = '业务模型标识不能为空';
    if (modelConfig.provider === 'enterprise_ws' && !modelConfig.ws_url) errors['model_config.ws_url'] = 'WebSocket 地址不能为空';
    if (modelConfig.provider === 'flow' && !modelConfig.python_entry) errors['model_config.python_entry'] = 'Python 入口不能为空';
    if (!optimizerConfig.model) errors['optimizer_config.model'] = '优化模型标识不能为空';
    modelConfig.request_template = requestTemplate;
    const numberFields = [
      ['model_config.temperature', 'temperature'],
      ['model_config.max_tokens', 'max_tokens'],
      ['model_config.timeout_s', 'timeout_s'],
      ['optimizer_config.temperature', 'optimizer_temperature'],
      ['optimizer_config.max_tokens', 'optimizer_max_tokens'],
    ];
    void numberFields;
    const num = (value, fallback = null) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    };
    const payload = {
      prompt: form.prompt,
      model_config: {
        ...modelConfig,
        temperature: num(modelConfig.temperature, 0),
        max_tokens: num(modelConfig.max_tokens, null),
        timeout_s: num(modelConfig.timeout_s, 120),
        stream: Boolean(modelConfig.stream),
      },
      optimizer_config: {
        ...optimizerConfig,
        temperature: num(optimizerConfig.temperature, 0),
        max_tokens: num(optimizerConfig.max_tokens, null),
      },
      tool_schema: toolSchema,
      loader_config: {
        ...form.loader_config,
        max_steps: num(form.loader_config.max_steps, 8),
        max_model_calls: num(form.loader_config.max_model_calls, 12),
        max_context_chars: num(form.loader_config.max_context_chars, 60000),
      },
      run_config: {
        ...form.run_config,
        concurrency: num(form.run_config.concurrency, 3),
        case_timeout_s: num(form.run_config.case_timeout_s, 120),
        repeats_baseline: num(form.run_config.repeats_baseline, 1),
        repeats_validation: num(form.run_config.repeats_validation, 3),
      },
      budget_config: {
        ...form.budget_config,
        max_tokens: form.budget_config.max_tokens === '' || form.budget_config.max_tokens === null ? null : num(form.budget_config.max_tokens, null),
        max_cost: form.budget_config.max_cost === '' || form.budget_config.max_cost === null ? null : num(form.budget_config.max_cost, null),
        max_duration_s: num(form.budget_config.max_duration_s, 3600),
      },
      scorer_config: { ...form.scorer_config },
      acceptance_config: {
        ...form.acceptance_config,
        require_critical_cases: Boolean(form.acceptance_config.require_critical_cases),
        validation_repeats: num(form.acceptance_config.validation_repeats, 3),
      },
    };
    if (includeStatus) payload.status = includeStatus;
    return { payload, errors };
  }

  function inputFor(path, { label, hint = '', type = 'text', placeholder = '', mono = false, rows = 4, options = null, disabled = false } = {}) {
    const value = getPath(state.form, path);
    const editable = EDITABLE() && !disabled;
    const error = state.fieldErrors[path] || state.fieldErrors[path.replace('model_config.', '')] || '';
    let control;
    if (options) {
      control = h(
        'select.select',
        {
          disabled: !editable,
          onchange: (event) => {
            setPath(state.form, path, event.target.value);
            markDirty();
            if (path === 'model_config.provider') {
              renderForm();
            }
          },
        },
        ...options.map((option) => h('option', { value: option.value, selected: String(value) === String(option.value) }, option.label))
      );
    } else if (rows) {
      control = h('textarea.textarea', {
        rows,
        disabled: !editable,
        class: mono ? 'textarea--code' : '',
        placeholder,
        value: value ?? '',
        oninput: (event) => {
          setPath(state.form, path, event.target.value);
          markDirty();
        },
      });
    } else if (type === 'checkbox') {
      control = h(
        'label.checkbox-line',
        null,
        h('input', {
          type: 'checkbox',
          checked: Boolean(value),
          disabled: !editable,
          onchange: (event) => {
            setPath(state.form, path, event.target.checked);
            markDirty();
          },
        }),
        h('span', { text: hint || label })
      );
      return h('div.form-field', null, h('span.form-field__label', { text: label }), control);
    } else {
      control = h('input.input', {
        type,
        placeholder,
        disabled: !editable,
        value: value ?? '',
        class: mono ? 'mono' : '',
        oninput: (event) => {
          const next = type === 'number' ? event.target.value : event.target.value;
          setPath(state.form, path, next);
          markDirty();
        },
      });
    }
    return formField({ label, hint, error, control: control });
  }

  function renderVersionBar() {
    if (state.loading && !state.env && !state.versions.length) {
      mount(versionHost, loadingState('正在加载执行环境…', { compact: true }));
      return;
    }
    if (state.error) {
      mount(versionHost, errorState(state.error, { title: '无法加载执行环境', retry: () => load() }));
      return;
    }
    const env = state.env;
    const select = h(
      'select.select',
      {
        style: { width: 'auto', minWidth: '240px' },
        'aria-label': '选择执行环境版本',
        onchange: (event) => selectVersion(event.target.value),
      },
      ...state.versions.map((item) =>
        h('option', { value: item.id, selected: env && item.id === env.id }, `v${item.version_seq} · ${item.status === 'draft' ? '草稿' : '已发布'} · ${item.id}`)
      )
    );
    mount(
      versionHost,
      h(
        'div.version-bar',
        null,
        h('span.muted.small', { text: '环境版本' }),
        state.versions.length ? select : h('span.muted.small', { text: '尚无版本' }),
        env ? statusBadge(env.status, 'version') : null,
        env && env.id === project.current_environment_id ? chip('当前版本', 'accent') : null,
        env ? h('span.mono.xsmall.muted', { text: env.hash || '' }) : null,
        env ? h('span.xsmall.muted', { text: `创建于 ${formatDateTime(env.created_at)}` }) : null,
        h('span.version-bar__spacer'),
        env && env.status === 'published'
          ? button({ label: '设为当前', variant: 'ghost', size: 'sm', iconName: 'check', onClick: () => publishExisting(env), disabled: state.busy })
          : null,
        env && env.status === 'published'
          ? button({ label: '创建草稿', variant: 'secondary', size: 'sm', iconName: 'edit', onClick: createDraft, disabled: state.busy })
          : null,
        env && env.status === 'draft'
          ? button({ label: '保存草稿', variant: 'secondary', size: 'sm', iconName: 'save', onClick: () => saveDraft(), disabled: state.busy || !state.dirty })
          : null,
        env && env.status === 'draft'
          ? button({ label: '发布版本', variant: 'primary', size: 'sm', iconName: 'check', onClick: () => publishDraft(), disabled: state.busy })
          : null,
        !state.versions.length
          ? button({ label: '发布版本', variant: 'primary', size: 'sm', iconName: 'check', onClick: () => saveDraft('published'), disabled: state.busy })
          : null
      )
    );
  }

  function promptSection() {
    const editable = EDITABLE();
    const preview = h(
      'pre.prompt-preview',
      {
        style: {
          whiteSpace: 'pre-wrap',
          fontFamily: 'var(--font-mono)',
          fontSize: '12.5px',
          lineHeight: '1.7',
          background: 'var(--bg)',
          border: '1px solid var(--line-soft)',
          borderRadius: '8px',
          padding: '14px 16px',
          maxHeight: '420px',
          overflow: 'auto',
          color: 'var(--text-2)',
        },
      },
      state.form.prompt || ''
    );
    const textarea = h('textarea.textarea.textarea--code', {
      rows: 14,
      value: state.form.prompt || '',
      disabled: !editable,
      'aria-label': '业务系统 Prompt',
      oninput: (event) => {
        state.form.prompt = event.target.value;
        if (state.promptPreview) preview.textContent = event.target.value;
        markDirty();
      },
    });
    return sectionCard({
      title: '业务系统 Prompt',
      description: '首次运行前可编辑；发布为版本后只读，优化器没有修改权限。',
      titleHint: chip('默认展开', 'neutral'),
      actions: [
        state.fieldErrors.prompt ? chip('字段错误', 'danger') : null,
        button({
          label: state.promptPreview ? '返回编辑' : '预览',
          variant: 'ghost',
          size: 'sm',
          iconName: 'eye',
          onClick: () => {
            state.promptPreview = !state.promptPreview;
            renderForm();
          },
        }),
        editable
          ? button({
              label: '恢复内置模板',
              variant: 'ghost',
              size: 'sm',
              iconName: 'refresh',
              onClick: async () => {
                const ok = await confirmDialog({
                  title: '恢复内置模板',
                  message: '将用内置业务 Prompt 模板替换当前内容，已发布版本不会受影响。',
                  confirmText: '恢复模板',
                });
                if (!ok) return;
                state.form.prompt = DEFAULT_PROMPT;
                markDirty();
                renderForm();
              },
            })
          : null,
      ].filter(Boolean),
      body: h(
        'div.stack',
        null,
        state.promptPreview ? preview : textarea,
        state.fieldErrors.prompt ? inlineError(state.fieldErrors.prompt) : null
      ),
    });
  }

  function modelFields(provider) {
    if (provider === 'enterprise_ws') {
      return h(
        'div.form-grid',
        null,
        inputFor('model_config.ws_url', { label: 'WebSocket 地址', hint: 'ws:// 或 wss://，按网关约定握手与鉴权。' }),
        inputFor('model_config.model', { label: '模型标识', hint: '保留供应商返回的实际模型标识以便复现。' }),
        inputFor('model_config.api_key_ref', { label: '鉴权引用', hint: '服务端密钥引用，不进入 Prompt 或导出文件。' }),
        inputFor('model_config.stream_parser', { label: '流解析器', options: [{ value: 'sse', label: 'SSE 式流（默认）' }, { value: 'json_lines', label: 'JSON Lines' }] }),
        inputFor('request_template_text', {
          label: '请求模板（JSON）',
          rows: 6,
          mono: true,
          hint: '保留 api_key、user 等网关字段，不自动改写为 Authorization。',
        }),
        inputFor('model_config.connect_timeout_s', { label: '连接超时（秒）', type: 'number' }),
        inputFor('model_config.first_packet_timeout_s', { label: '首包超时（秒）', type: 'number' }),
        inputFor('model_config.idle_timeout_s', { label: '空闲超时（秒）', type: 'number' }),
        inputFor('model_config.total_timeout_s', { label: '总超时（秒）', type: 'number' }),
        inputFor('model_config.heartbeat_s', { label: '心跳间隔（秒）', type: 'number' })
      );
    }
    if (provider === 'flow') {
      return h(
        'div.form-grid',
        null,
        inputFor('model_config.python_entry', { label: 'Python 入口', hint: '形如 module.function，平台按受信版本调用 run_case。' }),
        inputFor('model_config.python_version', { label: 'Python 版本' }),
        inputFor('model_config.timeout_s', { label: '案例超时（秒）', type: 'number' }),
        inputFor('model_config.model', { label: '模型标识（可选）' }),
        inputFor('model_config.api_key_ref', { label: '鉴权引用（可选）' })
      );
    }
    return h(
      'div.form-grid',
      null,
      inputFor('model_config.base_url', { label: 'API 地址', hint: 'OpenAI 兼容的 Chat Completions 入口。' }),
      inputFor('model_config.model', { label: '模型标识' }),
      inputFor('model_config.api_key_ref', { label: '鉴权引用', hint: '服务端密钥引用，不写入日志或导出文件。' }),
      inputFor('model_config.temperature', { label: '温度', type: 'number', hint: '评测建议固定为 0。' }),
      inputFor('model_config.max_tokens', { label: '最大输出 Token', type: 'number' }),
      inputFor('model_config.timeout_s', { label: '超时（秒）', type: 'number' }),
      h(
        'div.form-field',
        null,
        h('span.form-field__label', { text: '流式返回' }),
        h(
          'label.checkbox-line',
          null,
          h('input', {
            type: 'checkbox',
            checked: Boolean(state.form.model_config.stream),
            disabled: !EDITABLE(),
            onchange: (event) => {
              state.form.model_config.stream = event.target.checked;
              markDirty();
            },
          }),
          h('span', { text: '启用 SSE 式增量返回（usage 在尾部聚合）' })
        )
      )
    );
  }

  function testResultView() {
    if (state.testLoading) {
      return h('div.test-result', null, h('span.spinner.spinner--sm'), h('span', { text: '正在测试连接…' }));
    }
    const result = state.testResult;
    if (!result) return null;
    const ok = result.ok !== false;
    return h(
      `div.test-result${ok ? ' test-result--success' : ' test-result--danger'}`,
      null,
      icon(ok ? 'check' : 'alert', { size: 15 }),
      h(
        'div',
        null,
        h('div', { text: result.message || (ok ? '连接成功' : '连接失败') }),
        h(
          'div.xsmall.muted',
          { style: { marginTop: '3px' } },
          [
            result.latency_ms !== undefined ? `耗时 ${result.latency_ms} ms` : null,
            result.model ? `模型 ${result.model}` : null,
            result.capabilities
              ? `能力：${Object.entries(result.capabilities)
                  .filter(([, value]) => value)
                  .map(([key]) => ({ routing: '分发', tool_arguments: '工具参数', native_tools: '原生工具', usage: 'usage', streaming: '流式' }[key] || key))
                  .join(' / ')}`
              : null,
          ]
            .filter(Boolean)
            .join(' · ')
        )
      )
    );
  }

  function businessModelSection() {
    const provider = state.form.model_config.provider || 'openai_http';
    return sectionCard({
      title: '业务模型',
      description: '选择 API / 企业网关 WebSocket / Python 流程级适配；保存模型标识、请求配置与代码版本。',
      actions: [
        provider === 'enterprise_ws' ? chip('企业网关', 'info') : null,
        provider === 'flow' ? chip('流程级适配', 'purple') : null,
        provider === 'demo_scripted' ? chip('示例确定性模型', 'info') : null,
        button({ label: '测试连接', variant: 'secondary', size: 'sm', iconName: 'link', onClick: testConnection, disabled: !EDITABLE() || state.testLoading }),
      ].filter(Boolean),
      body: h(
        'div.stack',
        null,
        h(
          'div.row',
          { style: { gap: '10px' } },
          h('span.form-field__label', { text: '接入方式' }),
          h(
            'div.segmented',
            { role: 'group', 'aria-label': '业务模型接入方式' },
            ...[
              { value: 'openai_http', label: '标准 API' },
              { value: 'enterprise_ws', label: '企业网关 WebSocket' },
              { value: 'flow', label: 'Python 流程' },
            ].map((option) =>
              h(
                `button.segmented__btn${provider === option.value ? ' is-active' : ''}`,
                {
                  type: 'button',
                  title: EDITABLE()
                    ? `切换为${option.label}`
                    : '当前版本已发布，只读；点击可创建草稿后切换',
                  onclick: async () => {
                    if (provider === option.value) return;
                    if (!EDITABLE()) {
                      await createDraftWithProvider(option, provider);
                      return;
                    }
                    state.form.model_config.provider = option.value;
                    markDirty();
                    renderForm();
                  },
                },
                option.label
              )
            )
          )
        ),
        !EDITABLE()
          ? h('p.form-field__hint', {
              text:
                provider === 'demo_scripted'
                  ? '当前版本使用示例确定性模型 demo_scripted（离线演示）。点击上方任一接入方式会提示创建草稿，在草稿中切换到真实 API / 企业网关 / Python 流程后再发布。'
                  : '当前版本已发布，内容只读。点击上方接入方式会提示创建草稿，修改只在草稿中进行，发布后才生效。',
            })
          : null,
        modelFields(provider),
        provider === 'enterprise_ws'
          ? h('p.form-field__hint', { text: '默认每请求独立连接；复用需要网关提供请求 ID 与多路复用协议。中途断开记 stream_interrupted，不跨连接拼接。' })
          : null,
        testResultView()
      ),
    });
  }

  function optimizerSection() {
    return sectionCard({
      title: '优化模型',
      description: '独立于业务模型配置；优化器 Prompt 同样版本化并在本轮固定。',
      collapsible: true,
      open: false,
      body: h(
        'div.form-grid',
        null,
        inputFor('optimizer_config.provider', {
          label: '接入方式',
          options: [
            { value: 'openai_http', label: '标准 API' },
            { value: 'enterprise_ws', label: '企业网关 WebSocket' },
            { value: 'flow', label: 'Python 流程' },
          ],
        }),
        inputFor('optimizer_config.model', { label: '优化模型标识' }),
        inputFor('optimizer_config.base_url', { label: 'API 地址' }),
        inputFor('optimizer_config.api_key_ref', { label: '鉴权引用' }),
        inputFor('optimizer_config.temperature', { label: '温度', type: 'number' }),
        inputFor('optimizer_config.max_tokens', { label: '最大输出 Token', type: 'number' })
      ),
    });
  }

  function toolsSection() {
    return sectionCard({
      title: '工具协议',
      description: '工具名、描述、JSON Schema 与模拟返回配置由平台管理，不是候选可修改文件。',
      collapsible: true,
      open: false,
      body: h(
        'div.stack',
        null,
        inputFor('tool_schema_text', {
          label: '工具 Schema（JSON 数组）',
          rows: 10,
          mono: true,
          hint: '保存时会校验 JSON 结构与必填字段；工具调用通过 fixtures 模拟。',
        }),
        state.fieldErrors.tool_schema ? inlineError(state.fieldErrors.tool_schema) : null
      ),
    });
  }

  function loaderSection() {
    return sectionCard({
      title: '上下文加载',
      description: '固定主 Skill 注入方式、子技能读取工具、最大步数与上下文超限处理。',
      collapsible: true,
      open: false,
      body: h(
        'div.form-grid',
        null,
        inputFor('loader_config.main_skill_mode', {
          label: '主 Skill 注入方式',
          options: [
            { value: 'inject', label: '注入系统消息' },
            { value: 'file_tool', label: '通过文件工具读取' },
          ],
        }),
        inputFor('loader_config.subskill_tool', { label: '子技能读取工具' }),
        inputFor('loader_config.max_steps', { label: '最大步数', type: 'number' }),
        inputFor('loader_config.max_model_calls', { label: '每案例最大模型调用', type: 'number' }),
        inputFor('loader_config.max_context_chars', { label: '最大上下文字符数', type: 'number' }),
        inputFor('loader_config.context_overflow', {
          label: '上下文超限处理',
          options: [
            { value: 'error', label: '显式报错' },
            { value: 'frozen_strategy', label: '执行冻结加载策略' },
          ],
        })
      ),
    });
  }

  function runParamsSection() {
    return sectionCard({
      title: '运行参数',
      description: '启动时冻结，运行中不可修改。',
      collapsible: true,
      open: false,
      body: h(
        'div.form-grid.form-grid--3',
        null,
        inputFor('run_config.concurrency', { label: '并发案例数', type: 'number' }),
        inputFor('run_config.case_timeout_s', { label: '单案例超时（秒）', type: 'number' }),
        inputFor('run_config.repeats_baseline', { label: '基线重复次数', type: 'number' }),
        inputFor('run_config.repeats_validation', { label: '验证配对重复次数', type: 'number' })
      ),
    });
  }

  function budgetSection() {
    return sectionCard({
      title: '预算',
      description: '启动前必须设置 Token 或金额上限；金额未知时以 Token 硬限制为准。',
      collapsible: true,
      open: false,
      body: h(
        'div.form-grid',
        null,
        inputFor('budget_config.max_tokens', { label: 'Token 上限', type: 'number', hint: '留空表示不按 Token 限制（不推荐）。' }),
        inputFor('budget_config.max_cost', { label: '金额上限（CNY）', type: 'number', hint: '未配置价格时无法设置金额限制。' }),
        inputFor('budget_config.max_duration_s', { label: '最长运行时间（秒）', type: 'number' })
      ),
    });
  }

  function acceptanceSection() {
    return sectionCard({
      title: '接受条件',
      description: '阈值在运行前冻结，不能为当前候选临时调低。',
      collapsible: true,
      open: false,
      body: h(
        'div.form-grid',
        null,
        inputFor('acceptance_config.validation_repeats', { label: '验证重复次数', type: 'number' }),
        h(
          'label.checkbox-line',
          null,
          h('input', {
            type: 'checkbox',
            checked: Boolean(state.form.acceptance_config.require_critical_cases),
            disabled: !EDITABLE(),
            onchange: (event) => {
              state.form.acceptance_config.require_critical_cases = event.target.checked;
              markDirty();
            },
          }),
          h('span', { text: '关键案例不得从通过变失败（critical）' })
        ),
        h('p.form-field__hint', { text: 'R/A 不降、至少一项严格提高、J 不降、无基础设施错误、包完整性通过；全部持平则拒绝。' })
      ),
    });
  }

  function renderForm() {
    if (state.loading && !state.env) {
      mount(formHost, loadingState('正在加载执行环境…'));
      return;
    }
    if (state.error) {
      mount(formHost, errorState(state.error, { title: '无法加载执行环境', retry: () => load() }));
      return;
    }
    const editable = EDITABLE();
    const banner = state.env?.status === 'published'
      ? h(
          'div.readonly-banner',
          null,
          icon('lock', { size: 15 }),
          h('span', { text: '当前版本已发布，内容只读；修改需要先生成草稿。' }),
          h('span.readonly-banner__spacer'),
          button({ label: '创建草稿', variant: 'secondary', size: 'sm', iconName: 'edit', onClick: createDraft, disabled: state.busy })
        )
      : state.env?.status === 'draft'
      ? h(
          'div.readonly-banner',
          null,
          icon('edit', { size: 15 }),
          h('span', { text: '正在编辑草稿版本。保存会生成新的草稿版本（环境版本不可变），发布后设为当前。' }),
          h('span.readonly-banner__spacer'),
          h('span#env-save-status.xsmall.muted', { text: state.dirty ? '● 有未保存修改' : '已保存' })
        )
      : h(
          'div.readonly-banner',
          null,
          icon('info', { size: 15 }),
          h('span', { text: '尚未发布执行环境。填写后可直接发布 v1，或先保存草稿。' }),
          h('span.readonly-banner__spacer'),
          h('span#env-save-status.xsmall.muted', { text: '未发布' })
        );

    if (!state.env && state.versions.length === 0) {
      mount(
        formHost,
        banner,
        h(
          'div.stack',
          null,
          promptSection(),
          businessModelSection(),
          optimizerSection(),
          toolsSection(),
          loaderSection(),
          runParamsSection(),
          budgetSection(),
          acceptanceSection(),
          h(
            'div.form-actions',
            null,
            button({ label: '保存草稿', variant: 'secondary', iconName: 'save', onClick: () => saveDraft('draft'), disabled: state.busy }),
            button({ label: '发布版本', variant: 'primary', iconName: 'check', onClick: () => saveDraft('published'), disabled: state.busy })
          )
        )
      );
      return;
    }
    mount(
      formHost,
      banner,
      editable ? null : h('p.small.muted', { style: { marginTop: '-6px', marginBottom: '12px' } }, '只读状态：如需修改，请先创建草稿。'),
      promptSection(),
      businessModelSection(),
      optimizerSection(),
      toolsSection(),
      loaderSection(),
      runParamsSection(),
      budgetSection(),
      acceptanceSection()
    );
    void editable;
  }

  async function saveDraft(status = 'draft') {
    const { payload, errors } = serialize({ includeStatus: status });
    state.fieldErrors = { ...errors };
    if (Object.keys(errors).length) {
      renderForm();
      toast('请先修正标红的字段', { type: 'warning' });
      return null;
    }
    state.busy = true;
    try {
      const created = await api.createEnvironment(project.id, payload);
      state.env = created;
      state.versions = [created, ...state.versions.filter((version) => version.id !== created.id)];
      state.form = toForm(created);
      state.dirty = false;
      ctx.setDirty(null);
      state.fieldErrors = {};
      toast(status === 'published' ? '执行环境已发布并设为当前' : '草稿已保存', { type: 'success' });
      renderVersionBar();
      renderForm();
      await ctx.refreshShell();
      return created;
    } catch (error) {
      state.fieldErrors = normalizeErrors(error);
      renderForm();
      toast(`保存失败：${error.message}`, { type: 'error' });
      return null;
    } finally {
      state.busy = false;
      renderVersionBar();
      renderForm();
    }
  }

  async function publishDraft() {
    if (!state.env) return;
    let target = state.env;
    if (state.dirty) {
      const saved = await saveDraft('draft');
      if (!saved) return;
      target = saved;
    }
    const ok = await confirmDialog({
      title: '发布执行环境',
      message: '发布后将设为项目当前环境，并冻结本轮使用的 Prompt、模型参数与工具协议。已有运行保留原快照。',
      confirmText: '发布并设为当前',
    });
    if (!ok) return;
    state.busy = true;
    try {
      const published = await api.publishEnvironment(target.id);
      toast('执行环境已发布', { type: 'success' });
      await load(published.id);
      await ctx.refreshShell();
    } catch (error) {
      toast(`发布失败：${error.message}`, { type: 'error' });
    } finally {
      state.busy = false;
      renderVersionBar();
      renderForm();
    }
  }

  async function publishExisting(env) {
    const ok = await confirmDialog({
      title: '设为当前环境',
      message: '将把该项目当前执行环境指针切换到该已发布版本；已有运行保留原快照。',
      confirmText: '设为当前',
    });
    if (!ok) return;
    state.busy = true;
    try {
      await api.publishEnvironment(env.id);
      toast('已设为当前环境', { type: 'success' });
      await load(env.id);
      await ctx.refreshShell();
    } catch (error) {
      toast(`操作失败：${error.message}`, { type: 'error' });
    } finally {
      state.busy = false;
      renderVersionBar();
      renderForm();
    }
  }

  async function createDraftWithProvider(option, currentProvider) {
    if (!state.env) return;
    const ok = await confirmDialog({
      title: `创建草稿并切换接入方式`,
      message:
        `当前执行环境版本已发布，内容只读，接入方式不能原地修改。` +
        `将基于当前版本创建草稿，并把接入方式切换为「${option.label}」` +
        `（当前为 ${currentProvider || '未设置'}）；草稿保存并发布后才生效。`,
      confirmText: '创建草稿并切换',
    });
    if (!ok) return;
    state.busy = true;
    try {
      const { payload } = serialize({ includeStatus: 'draft' });
      payload.parent_id = state.env.id;
      payload.model_config = {
        ...(payload.model_config || {}),
        provider: option.value,
      };
      const draft = await api.createEnvironment(project.id, payload);
      await load(draft.id);
      toast(`已创建草稿并切换为「${option.label}」，保存草稿后生效`, {
        type: 'success',
      });
    } catch (error) {
      toast(`创建草稿失败：${error.message}`, { type: 'error' });
    } finally {
      state.busy = false;
      renderVersionBar();
      renderForm();
    }
  }

  async function createDraft() {
    if (!state.env) return;
    const ok = await confirmDialog({
      title: '创建环境草稿',
      message: '将基于当前版本创建草稿副本，当前环境指针不变。草稿可编辑并发布。',
      confirmText: '创建草稿',
    });
    if (!ok) return;
    state.busy = true;
    try {
      const { payload } = serialize({ includeStatus: 'draft' });
      payload.parent_id = state.env.id;
      const draft = await api.createEnvironment(project.id, payload);
      toast('草稿已创建', { type: 'success' });
      await load(draft.id);
    } catch (error) {
      toast(`创建草稿失败：${error.message}`, { type: 'error' });
    } finally {
      state.busy = false;
      renderVersionBar();
      renderForm();
    }
  }

  async function testConnection() {
    const { payload } = serialize({});
    state.testLoading = true;
    state.testResult = null;
    renderForm();
    try {
      const result = await api.testConnection({ model_config: payload.model_config });
      state.testResult = result;
    } catch (error) {
      state.testResult = { ok: false, message: error.message };
    } finally {
      state.testLoading = false;
      renderForm();
    }
  }

  async function selectVersion(envId, { skipDirtyCheck = false } = {}) {
    if (state.dirty && !skipDirtyCheck) {
      const ok = await confirmDialog({
        title: '切换环境版本会丢失未保存修改',
        message: '当前表单有未保存的修改，是否放弃并切换版本？',
        confirmText: '放弃并切换',
        danger: true,
      });
      if (!ok) return;
    }
    const env = state.versions.find((item) => item.id === envId);
    if (!env) return;
    ctx.setDirty(null);
    state.env = env;
    state.dirty = false;
    state.fieldErrors = {};
    state.testResult = null;
    state.form = toForm(env);
    renderVersionBar();
    renderForm();
  }

  async function load(preferredId = null) {
    state.loading = true;
    state.error = null;
    renderVersionBar();
    renderForm();
    try {
      const [envPayload, detail] = await Promise.all([
        api.listEnvironments(project.id),
        project.current_environment_id ? api.getEnvironment(project.current_environment_id).catch(() => null) : Promise.resolve(null),
      ]);
      state.versions = asItems(envPayload).sort((a, b) => (b.version_seq || 0) - (a.version_seq || 0));
      let target = null;
      if (preferredId) target = state.versions.find((version) => version.id === preferredId) || null;
      if (!target) target = state.versions.find((version) => version.id === project.current_environment_id) || state.versions[0] || null;
      if (target && !target.tool_schema) target = (await api.getEnvironment(target.id).catch(() => target)) || target;
      if (!target && detail) target = detail;
      state.env = target;
      state.form = toForm(target);
      state.dirty = false;
      state.fieldErrors = {};
      ctx.setDirty(null);
    } catch (error) {
      state.error = error;
    } finally {
      state.loading = false;
      renderVersionBar();
      renderForm();
    }
  }

  mount(
    container,
    pageHeader({
      title: '执行环境',
      description: '业务 Prompt、模型接入与工具协议在启动时冻结；发布后只读，修改生成草稿。',
      actions: [
        button({
          label: '刷新',
          variant: 'ghost',
          iconName: 'refresh',
          onClick: () => load(state.env?.id),
        }),
      ],
    }),
    versionHost,
    formHost
  );

  await load();
  return () => {
    ctx.setDirty(null);
  };
}
