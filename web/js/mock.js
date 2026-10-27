/**
 * Development-only fake backend (enabled with ``?mock=1``).
 *
 * It implements the same routes the real FastAPI service exposes so layout,
 * interactions and the run-log UX can be exercised before the backend is
 * ready. It is never loaded unless the URL contains ``mock=1``.
 */

import { ApiError, installMockTransport } from './api_v3.js';

const NOW = Date.now();
const isoAgo = (ms) => new Date(NOW - ms).toISOString();
const ISO = (offsetMs = 0) => new Date(NOW + offsetMs).toISOString();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const clone = (value) => JSON.parse(JSON.stringify(value));

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

const MANIFEST = {
  version: 1,
  main: 'SKILL.md',
  subskills: [
    { skill_id: 'task_query', name: '任务查询', path: 'references/task_query.md', description: '查询项目或成员的任务列表、任务详情与状态' },
    { skill_id: 'task_update', name: '任务更新', path: 'references/task_update.md', description: '修改任务状态、负责人与截止时间' },
    { skill_id: 'member_lookup', name: '成员查询', path: 'references/member_lookup.md', description: '按姓名、角色或部门查询成员信息' },
    { skill_id: 'project_lookup', name: '项目查询', path: 'references/project_lookup.md', description: '查询项目基本信息与成员列表' },
  ],
};

const BASE_FILES = {
  'SKILL.md': `# 客户服务任务助手

## 适用场景
当用户需要查询或更新项目任务、成员信息时使用本 Skill。

## 子技能索引
- task_query：任务查询。当用户提到任务列表、进行中的任务、任务详情时使用。
- task_update：任务更新。当用户要求修改状态、负责人、截止时间时使用。
- member_lookup：成员查询。当用户按姓名或角色查找成员时使用。
- project_lookup：项目查询。当用户询问项目信息时使用。

## 编排规则
1. 先判断用户意图对应的子技能，再读取对应参考文件。
2. 业务工具参数必须来自用户请求或已加载的上下文，不得猜测。
3. 一次请求可能涉及多个子技能，按依赖顺序执行。

## 状态映射
- 进行中 → in_progress
- 待办 → todo
- 已完成 → done
`,
  'manifest.json': JSON.stringify(MANIFEST, null, 2),
  'references/task_query.md': `# 任务查询

## 触发条件
用户询问项目/成员的任务、任务状态、进行中的任务。

## 流程
1. 从请求中提取 project_id、status、assignee。
2. 调用 list_tasks；需要详细信息时再调用 get_task。
3. 状态关键词映射到枚举值：进行中 → in_progress。
`,
  'references/task_update.md': `# 任务更新

## 触发条件
用户要求把任务标记为完成、进行中或转交给其他成员。

## 流程
1. 确认 task_id 与目标状态。
2. 调用 update_task_status。
3. 返回更新后的状态摘要。
`,
  'references/member_lookup.md': `# 成员查询

## 触发条件
用户按姓名、角色或部门查找成员。

## 流程
1. 提取 keyword 或 role。
2. 调用 search_members。
`,
  'references/project_lookup.md': `# 项目查询

## 触发条件
用户询问项目概述、项目成员。

## 流程
1. 提取 project_id。
2. 调用 get_project。
`,
};

const CANDIDATE_FILES = clone(BASE_FILES);
CANDIDATE_FILES['SKILL.md'] = `# 客户服务任务助手

## 适用场景
当用户需要查询或更新项目任务、成员信息时使用本 Skill。

## 子技能索引
- task_query：任务查询。当用户提到任务列表、进行中的任务、状态映射、任务详情时使用。
- task_update：任务更新。当用户要求修改状态、负责人、截止时间时使用。
- member_lookup：成员查询。当用户按姓名或角色查找成员时使用。
- project_lookup：项目查询。当用户询问项目信息时使用。

## 编排规则
1. 先判断用户意图对应的子技能，再读取对应参考文件。
2. 业务工具参数必须来自用户请求或已加载的上下文，不得猜测。
3. 一次请求可能涉及多个子技能，按依赖顺序执行。
4. 查询类请求优先使用只读工具，不要先请求澄清再查询。

## 状态映射
- 进行中 → in_progress
- 处理中 → in_progress
- 待办 → todo
- 已完成 → done
- 已关闭 → done
`;
CANDIDATE_FILES['references/task_query.md'] = `# 任务查询

## 触发条件
用户询问项目/成员的任务、任务状态、进行中的任务、待办任务。

## 流程
1. 从请求中提取 project_id、status、assignee。
2. 状态关键词先做映射：进行中/处理中 → in_progress；待办 → todo；已完成/已关闭 → done。
3. 调用 list_tasks；需要详细信息时再调用 get_task。
4. 若缺少 project_id，先尝试从上下文解析，仍无法确定时再澄清。
`;

function fileList(files) {
  return Object.entries(files).map(([path, content]) => ({
    path,
    size: new TextEncoder().encode(content).length,
    sha256: `mock-${path.length}-${content.length}`,
  }));
}

function makeVersion({ id, parentId = null, status = 'published', files, message, createdAt, sourceRunId = null }) {
  return {
    id,
    project_id: 'project_demo',
    parent_id: parentId,
    status,
    manifest: MANIFEST,
    files: fileList(files),
    content_hash: `sha256:mock-${id}`,
    source_run_id: sourceRunId,
    message,
    created_at: createdAt,
  };
}

const skillVersionFiles = new Map();
const skillVersions = [
  makeVersion({ id: 'skillv_1', files: BASE_FILES, message: '导入初始 Skill 包（4 个子技能）', createdAt: isoAgo(9 * 86400_000) }),
  makeVersion({
    id: 'skillv_2',
    parentId: 'skillv_1',
    files: BASE_FILES,
    message: '按基线报告修正状态映射说明',
    createdAt: isoAgo(3 * 3600_000),
    sourceRunId: 'run_base_1',
  }),
  makeVersion({
    id: 'skillv_3',
    parentId: 'skillv_2',
    files: CANDIDATE_FILES,
    message: '候选补丁：补充进行中状态映射与只读查询优先',
    createdAt: isoAgo(26 * 60_000),
    sourceRunId: 'run_accept_1',
  }),
];
for (const version of skillVersions) {
  skillVersionFiles.set(version.id, clone(version.id === 'skillv_3' ? CANDIDATE_FILES : BASE_FILES));
}

/* ------------------------------------------------------------------ */
/* datasets / cases                                                    */
/* ------------------------------------------------------------------ */

const CASE_DEFS = [
  { case_id: 'case_001', query: '查询项目 P123 中进行中的任务', group_id: 'task_query_01', split: 'optimize', tags: ['任务查询', '状态映射'], skill: 'task_query', tool: 'list_tasks', args: { project_id: 'P123', status: 'in_progress' } },
  { case_id: 'case_002', query: '项目 P123 里还有哪些任务没有完成？', group_id: 'task_query_02', split: 'optimize', tags: ['任务查询'], skill: 'task_query', tool: 'list_tasks', args: { project_id: 'P123', status: 'in_progress' }, failMode: 'status_active' },
  { case_id: 'case_003', query: '把任务 T-2048 标记为已完成', group_id: 'task_update_01', split: 'optimize', tags: ['任务更新'], skill: 'task_update', tool: 'update_task_status', args: { task_id: 'T-2048', status: 'done' } },
  { case_id: 'case_004', query: '查看项目 P123 的成员列表', group_id: 'project_lookup_01', split: 'optimize', tags: ['项目查询', '成员'], skill: 'project_lookup', tool: 'get_project', args: { project_id: 'P123' }, failMode: 'wrong_route' },
  { case_id: 'case_005', query: '搜索成员「张伟」的联系方式', group_id: 'member_lookup_01', split: 'optimize', tags: ['成员查询'], skill: 'member_lookup', tool: 'search_members', args: { keyword: '张伟' } },
  { case_id: 'case_006', query: '任务 T-3102 现在是什么状态？', group_id: 'task_query_03', split: 'optimize', tags: ['任务查询', '详情'], skill: 'task_query', tool: 'get_task', args: { task_id: 'T-3102' }, failMode: 'missing_tool' },
  { case_id: 'case_007', query: '将任务 T-1188 转交给李娜', group_id: 'task_update_02', split: 'optimize', tags: ['任务更新', '关键'], skill: 'task_update', tool: 'update_task_status', args: { task_id: 'T-1188', status: 'in_progress' }, critical: true, failMode: 'regression_critical' },
  { case_id: 'case_008', query: '项目 P900 中有哪些进行中的任务？', group_id: 'task_query_04', split: 'optimize', tags: ['任务查询'], skill: 'task_query', tool: 'list_tasks', args: { project_id: 'P900', status: 'in_progress' }, failMode: 'extra_arg' },
  { case_id: 'case_009', query: '查询项目 P123 已完成的任务', group_id: 'task_query_05', split: 'optimize', tags: ['任务查询'], skill: 'task_query', tool: 'list_tasks', args: { project_id: 'P123', status: 'done' } },
  { case_id: 'case_010', query: '任务 T-2049 的负责人是谁？', group_id: 'task_query_06', split: 'optimize', tags: ['任务查询'], skill: 'task_query', tool: 'get_task', args: { task_id: 'T-2049' }, failMode: 'missing_arg' },
  { case_id: 'case_011', query: '把 T-3300 标记为进行中', group_id: 'task_update_03', split: 'optimize', tags: ['任务更新', '状态映射'], skill: 'task_update', tool: 'update_task_status', args: { task_id: 'T-3300', status: 'in_progress' } },
  { case_id: 'case_012', query: '项目 P123 的任务负责人都是谁？', group_id: 'project_lookup_02', split: 'optimize', tags: ['项目查询', '成员'], skill: 'project_lookup', tool: 'get_project', args: { project_id: 'P123' } },
  { case_id: 'case_013', query: '按角色查询所有产品经理', group_id: 'member_lookup_02', split: 'validation', tags: ['成员查询', '角色'], skill: 'member_lookup', tool: 'search_members', args: { keyword: '产品经理', role: 'pm' }, failMode: 'regression_validation' },
  { case_id: 'case_014', query: '项目 P555 的任务列表', group_id: 'task_query_07', split: 'validation', tags: ['任务查询'], skill: 'task_query', tool: 'list_tasks', args: { project_id: 'P555' } },
  { case_id: 'case_015', query: '任务 T-777 需要转交，但我没说是谁', group_id: 'task_update_04', split: 'validation', tags: ['澄清'], action: 'clarify', skill: 'task_update', missing_fields: ['assignee'] },
  { case_id: 'case_016', query: '把项目 P123 的已完成任务全部关闭', group_id: 'task_update_05', split: 'final_test', tags: ['任务更新'], skill: 'task_update', tool: 'update_task_status', args: { task_id: 'T-9999', status: 'done' } },
  { case_id: 'case_017', query: '查询项目 P321 中待办的任务', group_id: 'task_query_08', split: 'optimize', tags: ['任务查询'], skill: 'task_query', tool: 'list_tasks', args: { project_id: 'P321', status: 'todo' } },
  { case_id: 'case_018', query: '成员「王芳」属于哪个部门？', group_id: 'member_lookup_03', split: 'optimize', tags: ['成员查询'], skill: 'member_lookup', tool: 'search_members', args: { keyword: '王芳' } },
];

function expectedFor(def) {
  const action = def.action || 'tool_call';
  if (action === 'clarify') {
    return { action, routes: [['task_update']], call_match: 'ordered_exact', tool_calls: [], missing_fields: def.missing_fields || [] };
  }
  return {
    action,
    routes: [[def.skill]],
    route_ordered: false,
    call_match: 'ordered_exact',
    tool_calls: [{ name: def.tool, arguments: def.args }],
    missing_fields: [],
  };
}

function contextsFor(def) {
  if (def.case_id === 'case_001') return { timezone: 'Asia/Shanghai', now: '2026-10-24T10:00:00+08:00' };
  if (def.split === 'validation') return { timezone: 'Asia/Shanghai' };
  return {};
}

function fixturesFor(def) {
  if (!def.tool) return [];
  const response =
    def.tool === 'list_tasks'
      ? { tasks: [{ task_id: 'T-2048', status: 'in_progress' }, { task_id: 'T-3102', status: 'done' }], total: 2 }
      : def.tool === 'get_task'
      ? { task_id: def.args.task_id, status: 'in_progress', assignee: '李娜' }
      : def.tool === 'update_task_status'
      ? { ok: true, task_id: def.args.task_id, status: def.args.status }
      : def.tool === 'search_members'
      ? { members: [{ name: '张伟', role: 'pm', department: '产品部' }] }
      : { project_id: def.args.project_id, name: '示例项目', members: 12 };
  return [{ name: def.tool, match: def.args, response }];
}

const DATASET_CASES = CASE_DEFS.map((def) => ({
  case_id: def.case_id,
  query: def.query,
  context: contextsFor(def),
  expected: expectedFor(def),
  fixtures: fixturesFor(def),
  split: def.split,
  group_id: def.group_id,
  tags: def.tags,
  critical: Boolean(def.critical),
}));

function coverageFor(cases) {
  const skills = {};
  for (const sub of MANIFEST.subskills) skills[sub.skill_id] = { optimize: 0, validation: 0, final_test: 0, total: 0 };
  const tools = {};
  const actions = { tool_call: 0, clarify: 0, no_action: 0 };
  const splitCounts = {};
  let multiRoute = 0;
  for (const item of cases) {
    const expected = item.expected || {};
    splitCounts[item.split] = (splitCounts[item.split] || 0) + 1;
    actions[expected.action || 'tool_call'] = (actions[expected.action || 'tool_call'] || 0) + 1;
    const seen = new Set();
    for (const route of expected.routes || []) {
      for (const skill of route) {
        if (skills[skill] && !seen.has(skill)) {
          seen.add(skill);
          skills[skill][item.split] += 1;
          skills[skill].total += 1;
        }
      }
      if (route.length > 1) multiRoute += 1;
    }
    for (const call of expected.tool_calls || []) {
      if (call.name in tools) tools[call.name] += 1;
    }
  }
  return {
    total: cases.length,
    split_counts: splitCounts,
    skills,
    tools,
    multi_route_cases: multiRoute,
    actions,
    clarify_cases: actions.clarify || 0,
    no_action_cases: actions.no_action || 0,
  };
}

const datasetCasesByVersion = new Map();
const datasets = [
  {
    id: 'ds_1',
    project_id: 'project_demo',
    parent_id: null,
    version_seq: 1,
    status: 'published',
    hash: 'sha256:mock-ds-1',
    split_config: { optimize: 0.7, validation: 0.2, final_test: 0.1, seed: 42 },
    source_name: 'customer_service_cases_v1.jsonl',
    case_count: DATASET_CASES.length,
    import_report: { total: DATASET_CASES.length, valid: DATASET_CASES.length, invalid: 0 },
    created_at: isoAgo(8 * 86400_000),
    published_at: isoAgo(8 * 86400_000),
  },
  {
    id: 'ds_2',
    project_id: 'project_demo',
    parent_id: 'ds_1',
    version_seq: 2,
    status: 'published',
    hash: 'sha256:mock-ds-2',
    split_config: { optimize: 0.7, validation: 0.2, final_test: 0.1, seed: 42 },
    source_name: 'customer_service_cases_v2.jsonl',
    case_count: DATASET_CASES.length,
    import_report: { total: DATASET_CASES.length + 1, valid: DATASET_CASES.length, invalid: 1 },
    created_at: isoAgo(3 * 86400_000),
    published_at: isoAgo(3 * 86400_000),
  },
];
datasetCasesByVersion.set('ds_1', clone(DATASET_CASES));
datasetCasesByVersion.set('ds_2', clone(DATASET_CASES));

/* ------------------------------------------------------------------ */
/* environments                                                        */
/* ------------------------------------------------------------------ */

function baseEnvironment(id, versionSeq, overrides = {}) {
  return {
    id,
    project_id: 'project_demo',
    parent_id: versionSeq > 1 ? 'env_1' : null,
    version_seq: versionSeq,
    status: 'published',
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
      request_template: {},
    },
    optimizer_config: {
      provider: 'openai_http',
      base_url: 'https://gateway.internal.example/v1',
      model: 'deepseek-optimizer-v1',
      api_key_ref: 'secret://gateway/optimizer',
      temperature: 0.2,
      max_tokens: 4096,
    },
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
    adapter_hash: 'sha256:mock-adapter-v2',
    hash: `sha256:mock-env-${versionSeq}`,
    reproducibility_note: '模型版本由供应商返回标识记录；温度固定为 0。',
    created_at: isoAgo(9 * 86400_000 - versionSeq * 3600_000),
    published_at: isoAgo(9 * 86400_000 - versionSeq * 3600_000),
    ...overrides,
  };
}

const environments = [
  baseEnvironment('env_1', 1, { status: 'published' }),
  baseEnvironment('env_2', 2, { status: 'published', prompt: `${DEFAULT_PROMPT}\n\n（v2：补充只读查询优先规则）` }),
];

/* ------------------------------------------------------------------ */
/* runs + events                                                       */
/* ------------------------------------------------------------------ */

const runEvents = new Map();
const eventListeners = new Map();
const runSequences = new Map();
const runs = [];

function metricBlock(value, evaluated, passed) {
  if (value === null) return { value: null, status: 'not_evaluated', evaluated: 0, passed: 0, total_cases: 0 };
  return { value, status: 'ok', evaluated, passed, total_cases: evaluated };
}

function buildMetrics({ baseline, candidate = null }) {
  const build = (data) =>
    data
      ? {
          routing: metricBlock(data.routing, data.evaluated, data.routingPassed),
          tool_args: metricBlock(data.tool_args, data.evaluated, data.toolArgsPassed),
          joint: metricBlock(data.joint, data.total, data.jointPassed),
        }
      : null;
  const b = build(baseline);
  const c = build(candidate);
  const delta = b && c
    ? {
        routing: c.routing.value - b.routing.value,
        tool_args: c.tool_args.value - b.tool_args.value,
        joint: c.joint.value - b.joint.value,
      }
    : null;
  return { baseline: b, candidate: c, delta };
}

const ACCEPT_METRIC_DATA = {
  baseline: { routing: 0.72, evaluated: 12, routingPassed: 9, tool_args: 0.66, toolArgsPassed: 8, joint: 0.58, total: 18, jointPassed: 10 },
  candidate: { routing: 0.86, evaluated: 12, routingPassed: 11, tool_args: 0.81, toolArgsPassed: 10, joint: 0.75, total: 18, jointPassed: 13 },
};
const REJECT_METRIC_DATA = {
  baseline: { routing: 0.8, evaluated: 12, routingPassed: 10, tool_args: 0.75, toolArgsPassed: 9, joint: 0.7, total: 18, jointPassed: 12 },
  candidate: { routing: 0.74, evaluated: 12, routingPassed: 9, tool_args: 0.78, toolArgsPassed: 10, joint: 0.71, total: 18, jointPassed: 12 },
};

const EXECUTION = {
  planned: 18,
  completed: 18,
  valid: 18,
  infra_errors: 0,
  duration_ms: 212_000,
  tokens: 18_432,
  cost: null,
  fixture_misses: 2,
  capability_failures: 3,
};

const SNAPSHOT = {
  skill_version: { id: 'skillv_2', label: 'v2', status: 'published' },
  environment: { id: 'env_2', version_seq: 2 },
  dataset: { id: 'ds_1', version_seq: 1, case_count: 18 },
  scorer_version: 'score-v1',
  fingerprints: { skill: 'sha256:mock-skill-2', environment: 'sha256:mock-env-2', dataset: 'sha256:mock-ds-1', scorer: 'score-v1' },
};

const ACCEPTANCE_ACCEPT = {
  decision: 'accept',
  rules: [
    { rule: 'routing_not_decreased', ok: true, detail: 'R 0.72 → 0.86（+14.0pp）' },
    { rule: 'tool_args_not_decreased', ok: true, detail: 'A 0.66 → 0.81（+15.0pp）' },
    { rule: 'at_least_one_improved', ok: true, detail: 'R 与 A 均严格提高' },
    { rule: 'joint_not_decreased', ok: true, detail: 'J 0.58 → 0.75' },
    { rule: 'critical_cases_no_regression', ok: true, detail: '关键案例无回归' },
    { rule: 'no_infra_errors', ok: true, detail: '无基础设施错误' },
    { rule: 'package_integrity', ok: true, detail: '候选包静态检查通过' },
    { rule: 'validation_complete', ok: true, detail: '验证集 18/18 案例完成，重复 3 次' },
  ],
  reason: '候选在 R、A、J 上均提高，关键案例无回归，无基础设施错误，符合接受规则。',
};

const CANDIDATE_PATCH = {
  summary: '补充状态映射与只读查询优先规则',
  edits: [
    {
      path: 'SKILL.md',
      op: 'replace',
      search: '进行中 → in_progress',
      replace: '进行中 → in_progress\n处理中 → in_progress\n已关闭 → done',
      reason: 'case_002、case_011 将“进行中/处理中”映射为 active 或缺失',
    },
    {
      path: 'references/task_query.md',
      op: 'replace',
      search: '从请求中提取 project_id、status、assignee。',
      replace: '从请求中提取 project_id、status、assignee；状态关键词先做映射。',
      reason: 'case_001、case_008 漏做状态映射',
    },
  ],
};

const DIAGNOSIS = {
  category: 'arguments',
  summary: '主要错误集中在状态枚举映射与查询工具参数缺失。',
  related_cases: ['case_002', 'case_006', 'case_007', 'case_008', 'case_010'],
  evidence: [
    { case_id: 'case_002', event: 'score_completed', detail: '$.status 预期 in_progress，实际 active' },
    { case_id: 'case_010', event: 'tool_call_started', detail: 'get_task 缺少必填参数 task_id' },
  ],
};

const HYPOTHESES = [
  { summary: '状态关键词未映射到业务枚举，导致 3 条任务查询案例 A 失败', confidence: 'high', evidence: ['case_002', 'case_008', 'case_011'] },
  { summary: '查询类请求缺少“先查询再澄清”的编排规则，导致 2 条案例路由正确但未调用工具', confidence: 'medium', evidence: ['case_006', 'case_010'] },
];

const CANDIDATE = {
  id: 'candidate_accept_1',
  skill_version_id: 'skillv_3',
  diagnosis: DIAGNOSIS,
  hypotheses: HYPOTHESES,
  changed_files: ['SKILL.md', 'references/task_query.md'],
  patch: CANDIDATE_PATCH,
  expected_effect: 'R 提升约 10–15pp，A 提升约 10–15pp，关键查询类案例不再漏参。',
  risk_cases: [
    { case_id: 'case_013', reason: '验证集中按角色查询的组合参数，补丁未覆盖' },
    { case_id: 'case_015', reason: '澄清路径未被修改，需重点观察' },
  ],
  checks: { ok: true, checks: [{ name: 'path_whitelist', ok: true, message: '仅修改 Markdown 文件' }, { name: 'references', ok: true, message: '引用完整' }], errors: [] },
};

function makeRuns() {
  return [
    {
      id: 'run_accept_1',
      project_id: 'project_demo',
      mode: 'optimize',
      status: 'completed',
      stage: 'decision',
      stage_progress: { stage: 'decision', completed: 1, total: 1, current_case_ids: [], started_at: isoAgo(4 * 60_000), last_activity_at: isoAgo(3 * 60_000), message: '评测结论已生成' },
      baseline_skill_version_id: 'skillv_2',
      candidate_skill_version_id: 'skillv_3',
      environment_id: 'env_2',
      dataset_id: 'ds_1',
      parent_run_id: 'run_base_1',
      baseline_run_id: 'run_base_1',
      baseline_cache_hit: false,
      decision: 'accept',
      decision_reason: ACCEPTANCE_ACCEPT.reason,
      acceptance: ACCEPTANCE_ACCEPT,
      metrics: { ...buildMetrics(ACCEPT_METRIC_DATA), diagnostics: { categories: { value_error: 3, missing_arg: 2, wrong_route: 1 }, fields: { '$.status': 3 }, tools: { get_task: 2 }, skills: { task_query: 4 } } },
      case_stats: { case_results: 36, scored: 36, errors: 0, planned: 18, completed: 18, valid: 18 },
      snapshot: clone(SNAPSHOT),
      candidate: clone(CANDIDATE),
      budget: { max_tokens: 200000, max_cost: null, max_duration_s: 3600, tokens_used: 18432, tokens_remaining: 181568 },
      cost: { input_tokens: 12_100, output_tokens: 6_332, total_tokens: 18_432, amount: null, currency: 'CNY', duration_ms: 212_000 },
      execution: clone(EXECUTION),
      created_at: isoAgo(12 * 60_000),
      queued_at: isoAgo(12 * 60_000),
      started_at: isoAgo(11 * 60_000),
      finished_at: isoAgo(3 * 60_000),
      rules_version: 'accept-v1',
    },
    {
      id: 'run_base_1',
      project_id: 'project_demo',
      mode: 'baseline',
      status: 'completed',
      stage: 'baseline_eval',
      stage_progress: { stage: 'baseline_eval', completed: 18, total: 18, current_case_ids: [], started_at: isoAgo(2 * 86400_000), last_activity_at: isoAgo(2 * 86400_000 - 180_000), message: '基线评测完成' },
      baseline_skill_version_id: 'skillv_2',
      candidate_skill_version_id: null,
      environment_id: 'env_2',
      dataset_id: 'ds_1',
      parent_run_id: null,
      baseline_run_id: null,
      baseline_cache_hit: false,
      decision: null,
      decision_reason: '',
      metrics: buildMetrics({ baseline: ACCEPT_METRIC_DATA.baseline }),
      case_stats: { case_results: 18, scored: 18, errors: 0, planned: 18, completed: 18, valid: 18 },
      snapshot: clone(SNAPSHOT),
      candidate: null,
      budget: { max_tokens: 200000, max_cost: null, max_duration_s: 3600, tokens_used: 9_120, tokens_remaining: 190880 },
      cost: { input_tokens: 6_000, output_tokens: 3_120, total_tokens: 9_120, amount: null, currency: 'CNY', duration_ms: 180_000 },
      execution: { ...clone(EXECUTION), planned: 18, completed: 18 },
      created_at: isoAgo(2 * 86400_000 + 30_000),
      queued_at: isoAgo(2 * 86400_000 + 30_000),
      started_at: isoAgo(2 * 86400_000),
      finished_at: isoAgo(2 * 86400_000 - 180_000),
      rules_version: 'score-v1',
    },
    {
      id: 'run_reject_1',
      project_id: 'project_demo',
      mode: 'optimize',
      status: 'completed',
      stage: 'decision',
      stage_progress: { stage: 'decision', completed: 1, total: 1, current_case_ids: [], started_at: isoAgo(2 * 86400_000), last_activity_at: isoAgo(2 * 86400_000 - 120_000), message: '评测结论：未达标' },
      baseline_skill_version_id: 'skillv_2',
      candidate_skill_version_id: 'skillv_reject_1',
      environment_id: 'env_2',
      dataset_id: 'ds_1',
      parent_run_id: 'run_base_1',
      baseline_run_id: 'run_base_1',
      decision: 'reject',
      decision_reason: '工具入参准确率 A 提高，但分发准确率 R 下降 6 个百分点，未满足“R 不下降”规则；关键案例 case_007 从通过变为失败。',
      acceptance: {
        decision: 'reject',
        rules: [
          { rule: 'routing_not_decreased', ok: false, detail: 'R 0.80 → 0.74（-6.0pp）' },
          { rule: 'tool_args_not_decreased', ok: true, detail: 'A 0.75 → 0.78' },
          { rule: 'at_least_one_improved', ok: true, detail: 'A 严格提高' },
          { rule: 'joint_not_decreased', ok: true, detail: 'J 0.70 → 0.71' },
          { rule: 'critical_cases_no_regression', ok: false, detail: '关键案例 case_007 从通过变为失败' },
          { rule: 'no_infra_errors', ok: true, detail: '无基础设施错误' },
          { rule: 'package_integrity', ok: true, detail: '候选包静态检查通过' },
          { rule: 'validation_complete', ok: true, detail: '验证集完成 3 次配对' },
        ],
        reason: 'R 下降且关键案例回归，按 accept-v1 拒绝。',
      },
      metrics: { ...buildMetrics(REJECT_METRIC_DATA), diagnostics: { categories: { wrong_route: 3, missing_arg: 2, value_error: 1 }, fields: { '$.status': 1 }, tools: { list_tasks: 2 }, skills: { task_update: 3 } } },
      case_stats: { case_results: 32, scored: 32, errors: 0, planned: 18, completed: 18, valid: 18 },
      snapshot: clone(SNAPSHOT),
      candidate: {
        ...clone(CANDIDATE),
        id: 'candidate_reject_1',
        skill_version_id: 'skillv_reject_1',
        patch: { summary: '重写子技能索引关键词', edits: [{ path: 'SKILL.md', op: 'replace', search: '子技能索引', replace: '子技能索引（重排）', reason: '尝试改善路由命中' }] },
        expected_effect: 'R 提升',
      },
      budget: { max_tokens: 200000, tokens_used: 16_800, tokens_remaining: 183200 },
      cost: { input_tokens: 11_000, output_tokens: 5_800, total_tokens: 16_800, amount: null, currency: 'CNY', duration_ms: 180_000 },
      execution: { ...clone(EXECUTION), infra_errors: 0, valid: 18 },
      created_at: isoAgo(2 * 86400_000 + 120_000),
      queued_at: isoAgo(2 * 86400_000 + 120_000),
      started_at: isoAgo(2 * 86400_000 + 60_000),
      finished_at: isoAgo(2 * 86400_000 - 120_000),
      rules_version: 'accept-v1',
    },
    {
      id: 'run_inconclusive_1',
      project_id: 'project_demo',
      mode: 'optimize',
      status: 'completed',
      stage: 'decision',
      stage_progress: { stage: 'decision', completed: 1, total: 1, current_case_ids: [], started_at: isoAgo(3 * 86400_000), last_activity_at: isoAgo(3 * 86400_000 - 60_000), message: '证据不足，本轮结束' },
      baseline_skill_version_id: 'skillv_2',
      candidate_skill_version_id: null,
      environment_id: 'env_2',
      dataset_id: 'ds_1',
      parent_run_id: 'run_base_1',
      baseline_run_id: 'run_base_1',
      decision: 'inconclusive',
      decision_reason: '优化器输出无法解析，启发式回退未生成修改；本轮没有进入复测，证据不足。',
      acceptance: {
        decision: 'inconclusive',
        rules: [
          { rule: 'routing_not_decreased', ok: false, detail: '候选未生成，无法比较' },
          { rule: 'tool_args_not_decreased', ok: false, detail: '候选未生成，无法比较' },
          { rule: 'at_least_one_improved', ok: false, detail: '无候选' },
          { rule: 'joint_not_decreased', ok: false, detail: '无候选' },
          { rule: 'critical_cases_no_regression', ok: true, detail: '未执行复测' },
          { rule: 'no_infra_errors', ok: true, detail: '无基础设施错误' },
          { rule: 'package_integrity', ok: false, detail: '未生成候选包' },
          { rule: 'validation_complete', ok: false, detail: '复测已跳过' },
        ],
        reason: '候选未生成或验证不完整，按证据不足处理。',
      },
      metrics: buildMetrics({ baseline: ACCEPT_METRIC_DATA.baseline }),
      case_stats: { case_results: 18, scored: 18, errors: 0, planned: 18, completed: 18, valid: 18 },
      snapshot: clone(SNAPSHOT),
      candidate: null,
      budget: { max_tokens: 200000, tokens_used: 7_600, tokens_remaining: 192400 },
      cost: { input_tokens: 5_100, output_tokens: 2_500, total_tokens: 7_600, amount: null, currency: 'CNY', duration_ms: 64_000 },
      execution: { ...clone(EXECUTION), planned: 18, completed: 18, valid: 17 },
      created_at: isoAgo(3 * 86400_000 + 90_000),
      queued_at: isoAgo(3 * 86400_000 + 90_000),
      started_at: isoAgo(3 * 86400_000 + 60_000),
      finished_at: isoAgo(3 * 86400_000 - 60_000),
      rules_version: 'accept-v1',
    },
    {
      id: 'run_running_1',
      project_id: 'project_demo',
      mode: 'optimize',
      status: 'running',
      stage: 'retest',
      stage_progress: {
        stage: 'retest',
        completed: 2,
        total: 8,
        current_case_ids: ['case_009', 'case_010', 'case_011'],
        started_at: isoAgo(2 * 60_000),
        last_activity_at: isoAgo(2_000),
        message: '候选复测进行中',
      },
      baseline_skill_version_id: 'skillv_2',
      candidate_skill_version_id: 'skillv_candidate_running',
      environment_id: 'env_2',
      dataset_id: 'ds_1',
      parent_run_id: 'run_base_1',
      baseline_run_id: 'run_base_1',
      decision: null,
      decision_reason: '',
      acceptance: null,
      metrics: {
        baseline: buildMetrics({ baseline: ACCEPT_METRIC_DATA.baseline }).baseline,
        candidate: {
          routing: metricBlock(0.83, 6, 5),
          tool_args: metricBlock(0.78, 6, 4),
          joint: metricBlock(0.72, 8, 6),
        },
        delta: null,
        temporary: true,
      },
      case_stats: { case_results: 20, scored: 14, errors: 1, planned: 18, completed: 8, valid: 7 },
      snapshot: clone(SNAPSHOT),
      candidate: { ...clone(CANDIDATE), id: 'candidate_running', skill_version_id: 'skillv_candidate_running' },
      budget: { max_tokens: 200000, tokens_used: 8_900, tokens_remaining: 191100 },
      cost: { input_tokens: 6_100, output_tokens: 2_800, total_tokens: 8_900, amount: null, currency: 'CNY', duration_ms: 92_000 },
      execution: { ...clone(EXECUTION), planned: 18, completed: 8, valid: 7 },
      created_at: isoAgo(4 * 60_000),
      queued_at: isoAgo(4 * 60_000),
      started_at: isoAgo(3 * 60_000),
      finished_at: null,
      rules_version: 'accept-v1',
    },
  ];
}

runs.push(...makeRuns());

/* ------------------------------------------------------------------ */
/* event generation                                                    */
/* ------------------------------------------------------------------ */

function nextSeq(runId) {
  const current = runSequences.get(runId) || 0;
  const next = current + 1;
  runSequences.set(runId, next);
  return next;
}

function pushEvent(runId, type, options = {}) {
  const event = {
    run_id: runId,
    seq: nextSeq(runId),
    ts: options.ts || new Date().toISOString(),
    stage: options.stage ?? null,
    case_id: options.case_id ?? null,
    split: options.split ?? null,
    variant: options.variant ?? null,
    repeat_index: options.repeat_index ?? null,
    attempt: options.attempt ?? null,
    call_id: options.call_id ?? null,
    type,
    level: options.level || 'info',
    payload: options.payload || {},
  };
  if (!runEvents.has(runId)) runEvents.set(runId, []);
  runEvents.get(runId).push(event);
  for (const listener of eventListeners.get(runId) || []) {
    try {
      listener(event);
    } catch {
      /* ignore */
    }
  }
  return event;
}

const baseTs = (offsetMs) => new Date(NOW - offsetMs).toISOString();
let clockCursor = 0;
function nextTs(totalMs, index, count) {
  return baseTs(totalMs - (totalMs * index) / Math.max(1, count));
}

function issuePayload(errorCode, title, message, { impact = '', nextAction = '', causeEventSeq = null, technicalDetail } = {}) {
  const issue = {
    error_code: errorCode,
    title,
    message,
    impact,
    next_action: nextAction,
    cause_event_seq: causeEventSeq,
  };
  if (technicalDetail) issue.technical_detail = technicalDetail;
  return issue;
}

function scorePayload(caseId, passed, extra = {}) {
  return {
    case_id: caseId,
    passed,
    status: passed ? 'pass' : 'fail',
    joint: passed,
    routing: { evaluable: true, passed: extra.routingPassed ?? passed, reason: extra.routingReason || '' },
    tool_args: { evaluable: true, passed: extra.toolArgsPassed ?? passed, reason: extra.toolArgsReason || '', call_match: 'ordered_exact' },
    duration_ms: 1200 + (caseId.length * 37) % 900,
    usage: { input_tokens: 380 + (caseId.length * 11) % 120, output_tokens: 90 + (caseId.length * 7) % 60 },
    diagnostics: extra.diagnostics || [],
  };
}

function seedRunEvents() {
  seedAcceptRun();
  seedBaselineRun();
  seedRejectRun();
  seedInconclusiveRun();
  seedRunningRun();
}

function loopCases(runId, caseIds, stage, startOffset, totalOffset, build) {
  const span = totalOffset - startOffset;
  const step = span / Math.max(1, caseIds.length * 4);
  let index = 0;
  for (const caseId of caseIds) {
    const base = startOffset + index * step * 4;
    build(caseId, (type, opts = {}) =>
      pushEvent(runId, type, {
        ts: nextTs(totalOffset, index * 4 + 0.1, caseIds.length * 4),
        stage,
        case_id: caseId,
        split: 'optimize',
        ...opts,
      })
    );
    index += 1;
    void base;
  }
}

function seedAcceptRun() {
  const runId = 'run_accept_1';
  const t = 12 * 60_000;
  const ev = (type, offset, opts = {}) => pushEvent(runId, type, { ts: baseTs(t - offset), ...opts });
  ev('run_created', 0, { payload: { mode: 'optimize', project_id: 'project_demo' } });
  ev('run_started', 4_000, { payload: { worker: 'worker-1', snapshot: SNAPSHOT } });
  ev('stage_started', 5_000, { stage: 'preflight', payload: { stage: 'preflight' } });
  ev('preflight_item', 7_000, { stage: 'preflight', payload: { name: 'Skill 包完整性', ok: true, detail: '5 个文件，哈希一致' } });
  ev('preflight_item', 8_000, { stage: 'preflight', payload: { name: '执行环境快照', ok: true, detail: 'env_2 · 哈希 sha256:mock-env-2' } });
  ev('preflight_item', 9_000, {
    stage: 'preflight',
    level: 'warning',
    payload: {
      name: '数据覆盖率',
      ok: false,
      detail: '子技能 project_lookup 在验证集中无样本',
      title: '数据集存在未覆盖子技能',
      issue: issuePayload('coverage_gap', '数据集存在未覆盖子技能', 'project_lookup 在验证集中没有样本，相关指标不会被评估。', {
        impact: '验证集的 R/A 覆盖不完整，未覆盖项显示“未评估”。',
        nextAction: '补充该子技能的验证样本，或确认本轮只用于诊断。',
      }),
    },
  });
  ev('preflight_item', 10_000, { stage: 'preflight', payload: { name: '预算', ok: true, detail: 'Token 上限 200k，预计使用 18k' } });
  ev('stage_completed', 11_000, { stage: 'preflight', payload: { ok: true } });

  ev('stage_started', 12_000, { stage: 'baseline_eval', payload: { stage: 'baseline_eval', planned: 18 } });
  const cases = ['case_001', 'case_002', 'case_003', 'case_004', 'case_005', 'case_006'];
  let offset = 14_000;
  for (const caseId of cases) {
    ev('case_started', offset, { stage: 'baseline_eval', case_id: caseId, split: 'optimize', payload: { case_id: caseId, phase: 'baseline' } });
    ev('skill_selected', offset + 1_200, { stage: 'baseline_eval', case_id: caseId, payload: { skill_id: caseId === 'case_004' ? 'task_query' : 'task_query', phase: 'baseline' } });
    ev('skill_read', offset + 1_800, { stage: 'baseline_eval', case_id: caseId, payload: { path: 'references/task_query.md', chars: 412 } });
    ev('model_call_started', offset + 3_400, { stage: 'baseline_eval', case_id: caseId, call_id: `call_${caseId}_1`, payload: { attempt: 1, model: 'Qwen/Qwen3.5-397B-A17B', messages_count: 6, phase: 'baseline' } });
    ev('model_call_finished', offset + 6_900, { stage: 'baseline_eval', case_id: caseId, call_id: `call_${caseId}_1`, payload: { finish_reason: 'tool_calls', usage: { input_tokens: 402, output_tokens: 96 }, duration_ms: 3_500 } });
    ev('tool_call_started', offset + 7_400, { stage: 'baseline_eval', case_id: caseId, call_id: `call_${caseId}_1`, payload: { tool: 'list_tasks', arguments: { project_id: 'P123', status: 'in_progress' } } });
    if (caseId === 'case_004') {
      ev('fixture_miss', offset + 8_000, {
        stage: 'baseline_eval',
        case_id: caseId,
        level: 'warning',
        payload: {
          tool: 'get_project',
          arguments: { project_id: 'P123' },
          title: '模拟返回缺失',
          issue: issuePayload('fixture_miss', '模拟返回缺失', '工具 get_project 的调用没有匹配的 fixture。', {
            impact: '该案例按能力失败计分，A/J 判定失败。',
            nextAction: '在数据集中为该工具参数组合补充模拟返回。',
          }),
        },
      });
    } else {
      ev('tool_result', offset + 8_000, { stage: 'baseline_eval', case_id: caseId, payload: { tool: 'list_tasks', matched_fixture: true, preview: { tasks: 2 } } });
    }
    const passed = !['case_002', 'case_004', 'case_006'].includes(caseId);
    const diagnostics = passed
      ? []
      : caseId === 'case_002'
      ? [{ category: 'value_error', field_path: '$.status', expected_value: 'in_progress', actual_value: 'active', tool: 'list_tasks', skill_id: 'task_query', case_id: caseId }]
      : caseId === 'case_006'
      ? [{ category: 'missing_tool', field_path: '$', tool: 'get_task', skill_id: 'task_query', case_id: caseId }]
      : [{ category: 'wrong_route', field_path: '$.routes', expected_value: [['project_lookup']], actual_value: [['task_query']], case_id: caseId }];
    ev('score_completed', offset + 9_000, {
      stage: 'baseline_eval',
      case_id: caseId,
      level: passed ? 'info' : 'warning',
      payload: { ...scorePayload(caseId, passed, { diagnostics }), phase: 'baseline' },
    });
    ev('case_completed', offset + 9_600, { stage: 'baseline_eval', case_id: caseId, payload: { passed, phase: 'baseline' } });
    offset += 12_000;
  }
  ev('progress_updated', 88_000, { stage: 'baseline_eval', payload: { completed: 18, total: 18 } });
  ev('stage_completed', 90_000, { stage: 'baseline_eval', payload: { ok: true, metrics: buildMetrics(ACCEPT_METRIC_DATA).baseline } });

  ev('stage_started', 92_000, { stage: 'error_analysis', payload: { stage: 'error_analysis' } });
  ev('progress_updated', 95_000, {
    stage: 'error_analysis',
    payload: { failed_cases: 3, categories: { value_error: 1, missing_tool: 1, wrong_route: 1 }, note: '仅向优化器提供优化集失败证据' },
  });
  ev('stage_completed', 97_000, { stage: 'error_analysis', payload: { ok: true } });

  ev('stage_started', 99_000, { stage: 'candidate_generation', payload: { stage: 'candidate_generation' } });
  ev('optimizer_input_built', 101_000, {
    stage: 'candidate_generation',
    payload: {
      failure_count: 3,
      provided_failures: 3,
      files: ['SKILL.md', 'references/task_query.md'],
      chars: 24_820,
      truncated: false,
      model: 'deepseek-optimizer-v1',
      strategy: 'model',
    },
  });
  ev('optimizer_call_started', 103_000, {
    stage: 'candidate_generation',
    call_id: 'call_optimizer_1',
    payload: { model: 'deepseek-optimizer-v1', temperature: 0.2, max_tokens: 4096 },
  });
  ev('optimizer_call_finished', 108_000, {
    stage: 'candidate_generation',
    call_id: 'call_optimizer_1',
    payload: {
      finish_reason: 'stop',
      usage: { input_tokens: 6_240, output_tokens: 980 },
      duration_ms: 5_000,
      artifacts: [{ id: 'artifact_opt_request_1', kind: 'model_request', size: 24_820, summary: '优化器请求（脱敏）' }],
    },
  });
  ev('optimizer_output_parsed', 109_000, {
    stage: 'candidate_generation',
    payload: {
      summary: CANDIDATE_PATCH.summary,
      hypotheses: HYPOTHESES.map((item) => item.summary),
      changed_files: ['SKILL.md', 'references/task_query.md'],
      artifacts: [{ id: 'artifact_opt_response_1', kind: 'model_response', size: 4_120, summary: '优化器原始返回（脱敏）' }],
    },
  });
  ev('patch_created', 110_000, {
    stage: 'candidate_generation',
    payload: CANDIDATE_PATCH,
  });
  ev('stage_completed', 112_000, { stage: 'candidate_generation', payload: { ok: true } });

  ev('stage_started', 114_000, { stage: 'static_check', payload: { stage: 'static_check' } });
  for (const [name, message] of [
    ['path_whitelist', '仅修改已有 Markdown 文件'],
    ['reference_integrity', '子技能引用完整'],
    ['no_test_hardcoding', '未写入测试 case_id 或标准答案'],
  ]) {
    ev('static_check_item', 116_000, { stage: 'static_check', payload: { name, ok: true, message } });
  }
  ev('static_check_passed', 118_000, { stage: 'static_check', payload: { checks: 3 } });
  ev('stage_completed', 119_000, { stage: 'static_check', payload: { ok: true } });

  ev('stage_started', 121_000, { stage: 'retest', payload: { stage: 'retest', repeats: 3 } });
  ev('retest_started', 122_000, { stage: 'retest', payload: { baseline: 'skillv_2', candidate: 'skillv_3', splits: ['optimize', 'validation'], repeats: 3 } });
  ev('case_started', 124_000, { stage: 'retest', case_id: 'case_001', split: 'validation', variant: 'candidate', repeat_index: 1, payload: { case_id: 'case_001', phase: 'candidate', repeat_index: 1 } });
  ev('tool_result', 127_000, { stage: 'retest', case_id: 'case_001', payload: { tool: 'list_tasks', matched_fixture: true, phase: 'candidate' } });
  ev('score_completed', 129_000, { stage: 'retest', case_id: 'case_001', repeat_index: 1, payload: scorePayload('case_001', true) });
  ev('case_completed', 130_000, { stage: 'retest', case_id: 'case_001', payload: { passed: true, phase: 'candidate' } });
  ev('retest_completed', 155_000, { stage: 'retest', payload: { baseline_runs: 3, candidate_runs: 3, improvements: 3, regressions: 1 } });
  ev('stage_completed', 157_000, { stage: 'retest', payload: { ok: true } });

  ev('stage_started', 159_000, { stage: 'decision', payload: { stage: 'decision' } });
  for (const rule of ACCEPTANCE_ACCEPT.rules) {
    ev('rule_evaluated', 161_000, {
      stage: 'decision',
      level: rule.ok ? 'info' : 'warning',
      payload: { rule: rule.rule, ok: rule.ok, detail: rule.detail, title: `规则判定：${rule.rule}` },
    });
  }
  ev('regression_detected', 164_000, {
    stage: 'decision',
    case_id: 'case_013',
    level: 'warning',
    payload: {
      case_id: 'case_013',
      change_type: 'regressed',
      title: '检测到非关键回归',
      issue: issuePayload('case_regression', '检测到非关键回归', 'case_013 在候选上从通过变为失败（组合参数 role 未被识别）。', {
        impact: '非关键案例回归，未触发拒绝规则，但会降低 J。',
        nextAction: '在下一轮运行中复核成员查询参数映射。',
      }),
    },
  });
  ev('decision_made', 166_000, {
    stage: 'decision',
    payload: { decision: 'accept', reason: ACCEPTANCE_ACCEPT.reason, rules_version: 'accept-v1' },
  });
  ev('stage_completed', 167_000, { stage: 'decision', payload: { ok: true, decision: 'accept' } });
  ev('run_completed', 168_000, { payload: { decision: 'accept', duration_ms: 212_000, tokens: 18_432 } });
}

function seedBaselineRun() {
  const runId = 'run_base_1';
  const t = 2 * 86400_000;
  const ev = (type, offset, opts = {}) => pushEvent(runId, type, { ts: baseTs(t - offset), ...opts });
  ev('run_created', 0, { payload: { mode: 'baseline' } });
  ev('run_started', 2_000, { payload: { worker: 'worker-1' } });
  ev('stage_started', 3_000, { stage: 'preflight' });
  ev('stage_completed', 8_000, { stage: 'preflight', payload: { ok: true } });
  ev('stage_started', 9_000, { stage: 'baseline_eval', payload: { planned: 18 } });
  let offset = 12_000;
  for (const caseId of ['case_001', 'case_002', 'case_003', 'case_004', 'case_005']) {
    ev('case_started', offset, { stage: 'baseline_eval', case_id: caseId, split: 'optimize', payload: { phase: 'baseline' } });
    ev('model_call_started', offset + 2_000, { stage: 'baseline_eval', case_id: caseId, call_id: `call_${caseId}_base`, payload: { model: 'Qwen/Qwen3.5-397B-A17B' } });
    ev('model_call_finished', offset + 5_000, { stage: 'baseline_eval', case_id: caseId, call_id: `call_${caseId}_base`, payload: { finish_reason: 'tool_calls' } });
    const passed = !['case_002', 'case_004'].includes(caseId);
    ev('score_completed', offset + 6_000, {
      stage: 'baseline_eval',
      case_id: caseId,
      level: passed ? 'info' : 'warning',
      payload: scorePayload(caseId, passed, {
        diagnostics: passed ? [] : [{ category: 'value_error', field_path: '$.status', expected_value: 'in_progress', actual_value: 'active', tool: 'list_tasks', case_id: caseId }],
      }),
    });
    ev('case_completed', offset + 6_800, { stage: 'baseline_eval', case_id: caseId, payload: { passed } });
    offset += 15_000;
  }
  ev('stage_completed', 175_000, { stage: 'baseline_eval', payload: { ok: true, metrics: buildMetrics(ACCEPT_METRIC_DATA).baseline } });
  ev('run_completed', 180_000, { payload: { duration_ms: 180_000 } });
}

function seedRejectRun() {
  const runId = 'run_reject_1';
  const t = 2 * 86400_000 + 60_000;
  const ev = (type, offset, opts = {}) => pushEvent(runId, type, { ts: baseTs(t - offset), ...opts });
  ev('run_created', 0, { payload: { mode: 'optimize' } });
  ev('run_started', 2_000, { payload: { worker: 'worker-1' } });
  ev('stage_started', 3_000, { stage: 'preflight' });
  ev('preflight_item', 5_000, { stage: 'preflight', payload: { name: '文件检查', ok: true, detail: '5 个文件' } });
  ev('stage_completed', 6_000, { stage: 'preflight', payload: { ok: true } });
  ev('stage_started', 7_000, { stage: 'baseline_eval', payload: { planned: 18 } });
  ev('stage_completed', 60_000, { stage: 'baseline_eval', payload: { ok: true } });
  ev('stage_started', 62_000, { stage: 'error_analysis' });
  ev('stage_completed', 64_000, { stage: 'error_analysis', payload: { ok: true, failed_cases: 4 } });
  ev('stage_started', 65_000, { stage: 'candidate_generation' });
  ev('optimizer_input_built', 66_000, { stage: 'candidate_generation', payload: { failure_count: 4, provided_failures: 4, files: ['SKILL.md'], chars: 18_200, truncated: false, model: 'deepseek-optimizer-v1' } });
  ev('optimizer_call_started', 67_000, { stage: 'candidate_generation', call_id: 'call_opt_reject', payload: { model: 'deepseek-optimizer-v1' } });
  ev('optimizer_call_finished', 70_000, { stage: 'candidate_generation', call_id: 'call_opt_reject', payload: { finish_reason: 'stop' } });
  ev('patch_created', 71_000, { stage: 'candidate_generation', payload: { summary: '重写子技能索引关键词', edits: 1 } });
  ev('stage_completed', 72_000, { stage: 'candidate_generation', payload: { ok: true } });
  ev('stage_started', 73_000, { stage: 'retest' });
  ev('retest_started', 74_000, { stage: 'retest', payload: { repeats: 3 } });
  ev('retest_completed', 100_000, { stage: 'retest', payload: { improvements: 1, regressions: 3 } });
  ev('stage_started', 101_000, { stage: 'decision' });
  for (const rule of runs.find((run) => run.id === 'run_reject_1').acceptance.rules) {
    ev('rule_evaluated', 103_000, { stage: 'decision', level: rule.ok ? 'info' : 'warning', payload: { rule: rule.rule, ok: rule.ok, detail: rule.detail } });
  }
  for (const caseId of ['case_007', 'case_009', 'case_015']) {
    ev('regression_detected', 105_000, {
      stage: 'decision',
      case_id: caseId,
      level: 'warning',
      payload: {
        case_id: caseId,
        change_type: 'regressed',
        title: '候选引入回归',
        issue: issuePayload(
          caseId === 'case_007' ? 'critical_case_regression' : 'case_regression',
          caseId === 'case_007' ? '关键案例从通过变为失败' : '检测到案例回归',
          caseId === 'case_007'
            ? 'case_007 是标记 critical 的任务转交案例，候选修改后分发到了 task_query。'
            : `case ${caseId} 在候选上的评分低于基线。`,
          {
            impact: caseId === 'case_007' ? '触发接受规则“关键案例不得回归”，本轮候选不能应用。' : '降低 R/J，不单独触发拒绝。',
            nextAction: caseId === 'case_007' ? '查看回归案例并调整补丁，避免修改 task_update 的触发关键词。' : '在下一轮运行中复核。',
            causeEventSeq: null,
            technicalDetail: { baseline: 'pass', candidate: 'fail', diagnostics: [{ field_path: '$.routes', expected: 'task_update', actual: 'task_query' }] },
          }
        ),
      },
    });
  }
  ev('decision_made', 107_000, { stage: 'decision', payload: { decision: 'reject', reason: runs.find((run) => run.id === 'run_reject_1').decision_reason, rules_version: 'accept-v1' } });
  ev('run_completed', 108_000, { payload: { decision: 'reject', duration_ms: 180_000 } });
}

function seedInconclusiveRun() {
  const runId = 'run_inconclusive_1';
  const t = 3 * 86400_000 + 60_000;
  const ev = (type, offset, opts = {}) => pushEvent(runId, type, { ts: baseTs(t - offset), ...opts });
  ev('run_created', 0, { payload: { mode: 'optimize' } });
  ev('run_started', 2_000, { payload: { worker: 'worker-2' } });
  ev('stage_started', 3_000, { stage: 'preflight' });
  ev('stage_completed', 7_000, { stage: 'preflight', payload: { ok: true } });
  ev('stage_started', 8_000, { stage: 'baseline_eval', payload: { planned: 18 } });
  for (const [index, caseId] of ['case_001', 'case_002', 'case_003'].entries()) {
    ev('case_started', 10_000 + index * 8_000, { stage: 'baseline_eval', case_id: caseId, split: 'optimize', payload: { phase: 'baseline' } });
    ev('score_completed', 14_000 + index * 8_000, {
      stage: 'baseline_eval',
      case_id: caseId,
      level: index === 1 ? 'warning' : 'info',
      payload: scorePayload(caseId, index !== 1),
    });
    ev('case_completed', 15_000 + index * 8_000, { stage: 'baseline_eval', case_id: caseId, payload: { passed: index !== 1 } });
  }
  ev('stage_completed', 38_000, { stage: 'baseline_eval', payload: { ok: true } });
  ev('stage_started', 40_000, { stage: 'error_analysis' });
  ev('stage_completed', 42_000, { stage: 'error_analysis', payload: { ok: true } });
  ev('stage_started', 44_000, { stage: 'candidate_generation' });
  ev('optimizer_input_built', 45_000, {
    stage: 'candidate_generation',
    payload: { failure_count: 3, provided_failures: 3, files: ['SKILL.md', 'references/task_query.md'], chars: 8_420, truncated: false, model: 'deepseek-optimizer-v1', strategy: 'model' },
  });
  ev('optimizer_call_started', 46_000, { stage: 'candidate_generation', call_id: 'call_opt_inconclusive', payload: { model: 'deepseek-optimizer-v1', max_tokens: 4096 } });
  const parseFailed = ev('optimizer_parse_failed', 49_000, {
    stage: 'candidate_generation',
    call_id: 'call_opt_inconclusive',
    level: 'error',
    payload: {
      title: '优化器返回内容无法解析',
      message: 'JSON 格式错误：第 12 行缺少逗号，输出被截断在 “edit” 字段附近。',
      raw_preview: '{"summary": "补充状态映射", "edits": [{"path": "SKILL.md", "op": "replace", "search": "进行中 → in_progress" "replace": ...',
      artifacts: [{ id: 'artifact_opt_invalid_1', kind: 'model_response', size: 3_050, summary: '优化器原始返回（脱敏）' }],
      issue: issuePayload('optimizer_parse_failed', '优化器返回内容无法解析', 'JSON 格式错误：第 12 行缺少逗号，输出在 “edit” 字段附近结束。', {
        impact: '优化器结果不可用，系统将尝试启发式回退，本轮可能没有候选。',
        nextAction: '查看原始返回与解析错误位置，必要时调整优化器输出格式或模型。',
        technicalDetail: {
          parser: 'optimizer.parse_output',
          error: 'JSONDecodeError: Expecting \',\' delimiter: line 12 column 34 (char 902)',
          response_artifact: 'artifact_opt_invalid_1',
          excerpt: '{"summary": "补充状态映射", "edits": [{"path": "SKILL.md", "op": "replace", "search": "进行中 → in_progress" "replace": ...',
        },
      }),
    },
  });
  ev('fallback_started', 50_000, { stage: 'candidate_generation', payload: { strategy: 'heuristic', reason: 'optimizer_parse_failed' } });
  ev('fallback_no_change', 51_000, {
    stage: 'candidate_generation',
    level: 'error',
    payload: {
      title: '回退未生成修改',
      message: '启发式回退没有找到可安全应用的通用修改。',
      issue: issuePayload('fallback_no_change', '启发式回退未生成修改', '回退策略扫描了失败证据，但没有找到满足白名单与唯一命中要求的修改。', {
        impact: '本轮没有候选，静态检查与复测已跳过。',
        nextAction: '检查优化器输出格式，或在下一轮提供更清晰的失败证据。',
        causeEventSeq: parseFailed.seq,
        technicalDetail: { scanned_edits: 0, rejected: [], policy: 'markdown-only' },
      }),
    },
  });
  ev('stage_completed', 52_000, { stage: 'candidate_generation', payload: { ok: false, reason: 'no_patch' } });
  ev('stage_started', 53_000, { stage: 'decision' });
  ev('rule_evaluated', 54_000, { stage: 'decision', level: 'warning', payload: { rule: 'at_least_one_improved', ok: false, detail: '候选未生成，无法比较' } });
  ev('decision_made', 55_000, { stage: 'decision', payload: { decision: 'inconclusive', reason: '优化器输出无法解析，回退未生成修改，本轮没有进入复测，证据不足。' } });
  ev('run_completed', 56_000, { payload: { decision: 'inconclusive', duration_ms: 64_000 } });
}

function seedRunningRun() {
  const runId = 'run_running_1';
  const t = 4 * 60_000;
  const ev = (type, offset, opts = {}) => pushEvent(runId, type, { ts: baseTs(t - offset), ...opts });
  ev('run_created', 0, { payload: { mode: 'optimize', parent_run_id: 'run_base_1' } });
  ev('run_started', 2_000, { payload: { worker: 'worker-1', snapshot: SNAPSHOT } });
  ev('stage_started', 3_000, { stage: 'preflight' });
  ev('preflight_item', 4_000, { stage: 'preflight', payload: { name: '文件检查', ok: true, detail: '5 个文件，哈希一致' } });
  ev('preflight_item', 5_000, { stage: 'preflight', payload: { name: '环境快照', ok: true, detail: 'env_2' } });
  ev('stage_completed', 6_000, { stage: 'preflight', payload: { ok: true } });
  ev('stage_started', 7_000, { stage: 'baseline_eval', payload: { planned: 18 } });
  for (let i = 0; i < 6; i += 1) {
    const caseId = `case_00${i + 1}`;
    ev('case_started', 9_000 + i * 6_000, { stage: 'baseline_eval', case_id: caseId, split: 'optimize', payload: { phase: 'baseline' } });
    ev('skill_selected', 10_000 + i * 6_000, { stage: 'baseline_eval', case_id: caseId, payload: { skill_id: 'task_query', phase: 'baseline' } });
    ev('model_call_started', 11_000 + i * 6_000, { stage: 'baseline_eval', case_id: caseId, call_id: `call_base_${caseId}`, payload: { model: 'Qwen/Qwen3.5-397B-A17B', attempt: 1 } });
    ev('model_call_finished', 13_500 + i * 6_000, { stage: 'baseline_eval', case_id: caseId, call_id: `call_base_${caseId}`, payload: { finish_reason: 'tool_calls', usage: { input_tokens: 410, output_tokens: 88 } } });
    const passed = i !== 1;
    ev('score_completed', 14_000 + i * 6_000, {
      stage: 'baseline_eval',
      case_id: caseId,
      level: passed ? 'info' : 'warning',
      payload: scorePayload(caseId, passed, {
        diagnostics: passed ? [] : [{ category: 'value_error', field_path: '$.status', expected_value: 'in_progress', actual_value: 'active', tool: 'list_tasks', case_id: caseId }],
      }),
    });
    ev('case_completed', 14_500 + i * 6_000, { stage: 'baseline_eval', case_id: caseId, payload: { passed } });
  }
  ev('stage_completed', 50_000, { stage: 'baseline_eval', payload: { ok: true, metrics: buildMetrics(ACCEPT_METRIC_DATA).baseline } });
  ev('stage_started', 52_000, { stage: 'error_analysis' });
  ev('progress_updated', 54_000, { stage: 'error_analysis', payload: { failed_cases: 4, categories: { value_error: 2, wrong_route: 1, missing_tool: 1 } } });
  ev('stage_completed', 55_000, { stage: 'error_analysis', payload: { ok: true } });
  ev('stage_started', 57_000, { stage: 'candidate_generation' });
  ev('optimizer_input_built', 58_000, { stage: 'candidate_generation', payload: { failure_count: 4, provided_failures: 4, files: ['SKILL.md', 'references/task_query.md'], chars: 21_300, truncated: false, model: 'deepseek-optimizer-v1', strategy: 'model' } });
  ev('optimizer_call_started', 59_000, { stage: 'candidate_generation', call_id: 'call_opt_running', payload: { model: 'deepseek-optimizer-v1' } });
  ev('optimizer_call_finished', 68_000, { stage: 'candidate_generation', call_id: 'call_opt_running', payload: { finish_reason: 'stop', usage: { input_tokens: 5_900, output_tokens: 1_020 } } });
  ev('patch_created', 69_000, { stage: 'candidate_generation', payload: CANDIDATE_PATCH });
  ev('stage_completed', 70_000, { stage: 'candidate_generation', payload: { ok: true } });
  ev('stage_started', 72_000, { stage: 'static_check' });
  ev('static_check_passed', 75_000, { stage: 'static_check', payload: { checks: 3 } });
  ev('stage_completed', 76_000, { stage: 'static_check', payload: { ok: true } });
  ev('stage_started', 78_000, { stage: 'retest', payload: { repeats: 3 } });
  ev('retest_started', 79_000, { stage: 'retest', payload: { baseline: 'skillv_2', candidate: 'skillv_candidate_running', repeats: 3 } });
  // three concurrent cases, two finished, three in flight
  const inflight = ['case_009', 'case_010', 'case_011'];
  for (const [index, caseId] of inflight.entries()) {
    ev('case_started', 90_000 + index * 1_500, { stage: 'retest', case_id: caseId, split: 'validation', variant: 'candidate', repeat_index: 1, payload: { phase: 'candidate', repeat_index: 1 } });
    ev('skill_selected', 92_000 + index * 1_500, { stage: 'retest', case_id: caseId, payload: { skill_id: 'task_query', phase: 'candidate' } });
    ev('skill_read', 93_000 + index * 1_500, { stage: 'retest', case_id: caseId, payload: { path: 'references/task_query.md' } });
    ev('model_call_started', 95_000 + index * 1_500, { stage: 'retest', case_id: caseId, call_id: `call_cand_${caseId}_1`, payload: { model: 'Qwen/Qwen3.5-397B-A17B', attempt: 1, phase: 'candidate' } });
  }
  ev('model_call_finished', 102_000, { stage: 'retest', case_id: 'case_009', call_id: 'call_cand_case_009_1', payload: { finish_reason: 'tool_calls', usage: { input_tokens: 418, output_tokens: 91 } } });
  ev('tool_call_started', 103_000, { stage: 'retest', case_id: 'case_009', call_id: 'call_cand_case_009_1', payload: { tool: 'list_tasks', arguments: { project_id: 'P123', status: 'done' } } });
  ev('tool_result', 104_000, { stage: 'retest', case_id: 'case_009', payload: { tool: 'list_tasks', matched_fixture: true } });
  ev('score_completed', 105_000, { stage: 'retest', case_id: 'case_009', payload: scorePayload('case_009', true) });
  ev('case_completed', 106_000, { stage: 'retest', case_id: 'case_009', payload: { passed: true, phase: 'candidate' } });
  ev('fixture_miss', 101_000, {
    stage: 'retest',
    case_id: 'case_010',
    level: 'warning',
    payload: {
      tool: 'get_task',
      arguments: { task_id: 'T-2049' },
      title: '模拟返回缺失（候选）',
      issue: issuePayload('fixture_miss', '候选复测缺少模拟返回', 'case_010 调用 get_task 时没有匹配的 fixture。', {
        impact: '该案例候选结果将按能力失败计分。',
        nextAction: '补充 get_task 的 fixture 后重跑复测。',
      }),
    },
  });
  ev('model_call_started', 104_500, { stage: 'retest', case_id: 'case_010', call_id: 'call_cand_case_010_2', attempt: 2, payload: { model: 'Qwen/Qwen3.5-397B-A17B', attempt: 2, note: '首次调用网关超时，已重试' } });
  ev('progress_updated', 108_000, { stage: 'retest', payload: { completed: 2, total: 8, active_case_ids: inflight } });
}

seedRunEvents();

/* ------------------------------------------------------------------ */
/* case results                                                        */
/* ------------------------------------------------------------------ */

const FAILURE_MODES = {
  case_002: { category: 'value_error', field_path: '$.status', expected_value: 'in_progress', actual_value: 'active' },
  case_004: { category: 'wrong_route', field_path: '$.routes', expected_value: 'project_lookup', actual_value: 'task_query' },
  case_006: { category: 'missing_tool', field_path: '$', expected_value: 'get_task', actual_value: null },
  case_007: { category: 'wrong_route', field_path: '$.routes', expected_value: 'task_update', actual_value: 'task_query' },
  case_008: { category: 'extra_arg', field_path: '$.status', expected_value: 'in_progress', actual_value: undefined },
  case_010: { category: 'missing_arg', field_path: '$.task_id', expected_value: 'T-2049', actual_value: undefined },
  case_013: { category: 'missing_arg', field_path: '$.role', expected_value: 'pm', actual_value: undefined },
  case_014: { category: 'value_error', field_path: '$.status', expected_value: 'in_progress', actual_value: 'todo' },
  case_015: { category: 'clarify_mismatch', field_path: '$.missing_fields', expected_value: ['assignee'], actual_value: [] },
};

function buildSide(def, passed, phase) {
  const expected = expectedFor(def);
  const mode = !passed ? FAILURE_MODES[def.case_id] || { category: 'value_error', field_path: '$.status', expected_value: 'in_progress', actual_value: 'active' } : null;
  let toolCalls = [];
  let selectedSkills = expected.routes[0] || [];
  if (mode && mode.category === 'wrong_route') {
    selectedSkills = ['task_query'];
  } else if (expected.action === 'clarify') {
    toolCalls = [];
  } else if (mode && mode.category === 'missing_tool') {
    toolCalls = [];
  } else if (def.tool) {
    const args = { ...def.args };
    if (mode) {
      if (mode.category === 'value_error' && mode.field_path === '$.status') args.status = mode.actual_value;
      if (mode.category === 'missing_arg') delete args[mode.field_path.replace('$.', '')];
      if (mode.category === 'extra_arg') args.extra = 'x';
    }
    toolCalls = [{ name: def.tool, arguments: args }];
  }
  const diagnostics = mode
    ? [
        {
          category: mode.category,
          skill_id: def.skill,
          tool: def.tool || null,
          field_path: mode.field_path,
          expected_value: mode.expected_value,
          actual_value: mode.actual_value,
          case_id: def.case_id,
        },
      ]
    : [];
  return {
    selected_skills: selectedSkills,
    action: expected.action,
    tool_calls: toolCalls,
    missing_fields: expected.action === 'clarify' ? (mode ? [] : expected.missing_fields) : [],
    passed,
    status: passed ? 'pass' : 'fail',
    error_code: passed ? null : mode && mode.category === 'missing_tool' ? 'format_error' : null,
    reason: passed ? '' : `${mode?.category || 'value_error'}：${mode?.field_path || ''} 预期 ${JSON.stringify(mode?.expected_value)}，实际 ${JSON.stringify(mode?.actual_value)}`,
    duration_ms: 900 + (def.case_id.length * 61) % 800,
    usage: { input_tokens: 380 + (def.case_id.length * 13) % 140, output_tokens: 80 + (def.case_id.length * 7) % 70 },
    diagnostics,
    phase,
    repeat_index: 1,
  };
}

const RUN_CASE_PATTERNS = {
  run_accept_1: {
    fixed: ['case_002', 'case_004', 'case_006', 'case_008', 'case_010'],
    regressed: ['case_013'],
    unchanged_fail: ['case_014'],
  },
  run_reject_1: {
    fixed: ['case_004'],
    regressed: ['case_007', 'case_009', 'case_015'],
    unchanged_fail: ['case_002', 'case_006', 'case_010', 'case_012', 'case_014'],
  },
  run_running_1: {
    fixed: ['case_002', 'case_004', 'case_006'],
    regressed: [],
    unchanged_fail: ['case_014'],
    candidateLimit: 8,
  },
};

function buildRunCaseResults(runId) {
  const pattern = RUN_CASE_PATTERNS[runId] || null;
  const items = [];
  for (const def of CASE_DEFS) {
    let baseline = null;
    let candidate = null;
    if (runId === 'run_base_1' || runId === 'run_inconclusive_1') {
      const baselinePass = !['case_002', 'case_004', 'case_006', 'case_008', 'case_010', 'case_014'].includes(def.case_id);
      baseline = buildSide(def, baselinePass, 'baseline');
    } else if (runId === 'run_accept_1' || runId === 'run_reject_1' || runId === 'run_running_1') {
      const fixed = pattern.fixed.includes(def.case_id);
      const regressed = pattern.regressed.includes(def.case_id);
      const failing = pattern.unchanged_fail.includes(def.case_id);
      const baselinePass = !fixed && !failing;
      const candidatePass = fixed || (!regressed && !failing && baselinePass);
      baseline = buildSide(def, baselinePass, 'baseline');
      const candidateCount = pattern.candidateLimit ? CASE_DEFS.slice(0, pattern.candidateLimit).map((item) => item.case_id) : null;
      if (!candidateCount || candidateCount.includes(def.case_id)) {
        candidate = buildSide(def, candidatePass, 'candidate');
      }
    }
    let changeType = 'pending';
    if (baseline && candidate) {
      if (baseline.passed && candidate.passed) changeType = 'unchanged_pass';
      else if (!baseline.passed && candidate.passed) changeType = 'fixed';
      else if (baseline.passed && !candidate.passed) changeType = 'regressed';
      else changeType = 'unchanged_fail';
    }
    items.push({
      case_id: def.case_id,
      query: def.query,
      split: def.split,
      group_id: def.group_id,
      tags: def.tags,
      critical: Boolean(def.critical),
      change_type: changeType,
      baseline,
      candidate,
      diagnostics: (candidate?.diagnostics?.length ? candidate.diagnostics : baseline?.diagnostics) || [],
    });
  }
  return items;
}

const runCaseCache = new Map();
function runCases(runId) {
  if (!runCaseCache.has(runId)) runCaseCache.set(runId, buildRunCaseResults(runId));
  return runCaseCache.get(runId);
}

/* ------------------------------------------------------------------ */
/* runtime helpers                                                     */
/* ------------------------------------------------------------------ */

function findProject(id) {
  const project = projects.find((item) => item.id === id);
  if (!project) throw new ApiError('项目不存在', { code: 'not_found', status: 404 });
  return project;
}

function findRun(id) {
  const run = runs.find((item) => item.id === id);
  if (!run) throw new ApiError('运行不存在', { code: 'not_found', status: 404 });
  return run;
}

function findDataset(id) {
  const dataset = datasets.find((item) => item.id === id);
  if (!dataset) throw new ApiError('数据集不存在', { code: 'not_found', status: 404 });
  return dataset;
}

function listResponse(items, limit = 50, offset = 0) {
  return { items: clone(items.slice(offset, offset + limit)), total: items.length, limit, offset };
}

const projects = [];

function seedProjects() {
  projects.push(
    {
      id: 'project_demo',
      name: '客户服务 Skill 单轮优化',
      description: '对客户服务 Skill 包做单轮评测与优化，重点改善子技能分发与工具入参准确率。',
      archived: 0,
      current_skill_version_id: 'skillv_2',
      current_environment_id: 'env_2',
      current_dataset_id: 'ds_1',
      created_at: isoAgo(9 * 86400_000),
      updated_at: isoAgo(3 * 60_000),
    },
    {
      id: 'project_empty',
      name: '内部知识库问答（待配置）',
      description: '新建项目，还没有导入 Skill 包与数据集。',
      archived: 0,
      current_skill_version_id: null,
      current_environment_id: null,
      current_dataset_id: null,
      created_at: isoAgo(2 * 86400_000),
      updated_at: isoAgo(2 * 86400_000),
    },
    {
      id: 'project_archived',
      name: '旧版工单 Skill（已归档）',
      description: '第一版工单处理 Skill，已归档保留历史记录。',
      archived: 1,
      current_skill_version_id: null,
      current_environment_id: null,
      current_dataset_id: null,
      created_at: isoAgo(30 * 86400_000),
      updated_at: isoAgo(20 * 86400_000),
    }
  );
}
seedProjects();

function completenessFor(project) {
  const skill = project.current_skill_version_id ? skillVersions.find((item) => item.id === project.current_skill_version_id) : null;
  const env = project.current_environment_id ? environments.find((item) => item.id === project.current_environment_id) : null;
  const dataset = project.current_dataset_id ? datasets.find((item) => item.id === project.current_dataset_id) : null;
  const baseline = runs.find((run) => run.project_id === project.id && run.mode === 'baseline' && run.status === 'completed');
  const items = {
    skill_package: skill
      ? { ok: true, label: 'Skill 包', detail: `当前版本 ${skill.id} · ${skill.files.length} 个文件`, route: `#/projects/${project.id}/skill` }
      : { ok: false, label: 'Skill 包', detail: '尚未导入 Skill 包', route: `#/projects/${project.id}/skill` },
    environment: env
      ? { ok: true, label: '执行环境', detail: `v${env.version_seq} · 已发布`, route: `#/projects/${project.id}/environment` }
      : { ok: false, label: '执行环境', detail: '尚未发布执行环境', route: `#/projects/${project.id}/environment` },
    dataset: dataset
      ? { ok: true, label: '数据集', detail: `v${dataset.version_seq} · ${dataset.case_count} 条案例`, route: `#/projects/${project.id}/dataset` }
      : { ok: false, label: '数据集', detail: '尚未导入数据集', route: `#/projects/${project.id}/dataset` },
    baseline: baseline
      ? { ok: true, label: '兼容基线', detail: `已完成 · ${baseline.id}`, route: `#/runs/${baseline.id}` }
      : { ok: false, label: '兼容基线', detail: '尚未运行基线评测', route: `#/projects/${project.id}/overview` },
  };
  const missing = Object.entries(items).filter(([, item]) => !item.ok).map(([key]) => key);
  return {
    ready: items.skill_package.ok && items.environment.ok && items.dataset.ok,
    missing,
    items,
  };
}

function baselineInfoFor(project) {
  const baseline = runs.find((run) => run.project_id === project.id && run.mode === 'baseline' && run.status === 'completed');
  if (!baseline) return { available: false, run_id: null, compatible: false, reason: '尚未运行基线评测' };
  return {
    available: true,
    run_id: baseline.id,
    compatible: true,
    reason: '执行环境、Skill 包、数据集与评分器均与当前版本一致',
    metrics: baseline.metrics,
    finished_at: baseline.finished_at,
  };
}

function overviewFor(project) {
  const completeness = completenessFor(project);
  const baseline = baselineInfoFor(project);
  const projectRuns = runs.filter((run) => run.project_id === project.id).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const active = projectRuns.find((run) => ['queued', 'running', 'cancel_requested'].includes(run.status));
  const metrics = baseline.available
    ? {
        routing: baseline.metrics.baseline.routing,
        tool_args: baseline.metrics.baseline.tool_args,
        joint: baseline.metrics.baseline.joint,
        dataset: { id: project.current_dataset_id, version_seq: 1, case_count: 18 },
        evaluated_at: baseline.finished_at,
        run_id: baseline.run_id,
        stale: false,
      }
    : null;
  let primary = { mode: 'baseline', label: '运行基线', enabled: true, reason: '' };
  if (!completeness.ready) {
    primary = { mode: null, label: '完成配置', enabled: true, reason: `缺少：${completeness.missing.map((key) => completeness.items[key].label).join('、')}` };
  } else if (active) {
    primary = { mode: active.mode, label: '已有运行正在进行', enabled: false, reason: '等待当前运行结束或先取消它。' };
  } else if (baseline.available && baseline.compatible) {
    primary = { mode: 'optimize', label: '单轮优化', enabled: true, reason: '基线可复用，配置一致' };
  }
  return {
    completeness,
    metrics,
    recent_runs: listResponse(projectRuns, 8, 0),
    baseline,
    primary_action: primary,
    active_run: active ? { id: active.id, status: active.status, stage: active.stage } : null,
    dataset: project.current_dataset_id ? datasets.find((item) => item.id === project.current_dataset_id) : null,
    environment: project.current_environment_id ? environments.find((item) => item.id === project.current_environment_id) : null,
    skill_version: project.current_skill_version_id ? skillVersions.find((item) => item.id === project.current_skill_version_id) : null,
  };
}

function projectDetail(project) {
  const cloneProject = clone(project);
  cloneProject.current_skill_version = project.current_skill_version_id
    ? clone(skillVersions.find((item) => item.id === project.current_skill_version_id) || null)
    : null;
  cloneProject.current_environment = project.current_environment_id
    ? clone(environments.find((item) => item.id === project.current_environment_id) || null)
    : null;
  cloneProject.current_dataset = project.current_dataset_id
    ? clone(datasets.find((item) => item.id === project.current_dataset_id) || null)
    : null;
  cloneProject.completeness = completenessFor(project);
  return cloneProject;
}

function caseDetail(runId, caseId) {
  const item = runCases(runId).find((entry) => entry.case_id === caseId);
  const def = CASE_DEFS.find((entry) => entry.case_id === caseId);
  if (!item || !def) throw new ApiError('案例不存在', { code: 'not_found', status: 404 });
  const events = (runEvents.get(runId) || []).filter((event) => event.case_id === caseId);
  return {
    ...clone(item),
    expected: expectedFor(def),
    context: contextsFor(def),
    fixtures: fixturesFor(def),
    dataset_id: 'ds_1',
    baseline_rate: null,
    events: clone(events.slice(-24)),
  };
}

function runDiffPayload(run) {
  const candidate = run.candidate;
  if (!candidate) return { changes: [], diagnosis: null, hypotheses: [], related_cases: [] };
  const changes = [
    {
      path: 'SKILL.md',
      status: 'modified',
      additions: 4,
      deletions: 2,
      diff: [
        '--- a/SKILL.md',
        '+++ b/SKILL.md',
        '@@ -8,7 +8,9 @@',
        ' ## 子技能索引',
        '- task_query：任务查询。当用户提到任务列表、进行中的任务、任务详情时使用。',
        '- task_update：任务更新。当用户要求修改状态、负责人、截止时间时使用。',
        '+ task_query：任务查询。当用户提到任务列表、进行中的任务、状态映射、任务详情时使用。',
        '+ task_update：任务更新。当用户要求修改状态、负责人、截止时间时使用。',
        ' - member_lookup：成员查询。当用户按姓名或角色查找成员时使用。',
        ' - project_lookup：项目查询。当用户询问项目信息时使用。',
        ' ',
        ' ## 编排规则',
        ' 1. 先判断用户意图对应的子技能，再读取对应参考文件。',
        ' 2. 业务工具参数必须来自用户请求或已加载的上下文，不得猜测。',
        ' 3. 一次请求可能涉及多个子技能，按依赖顺序执行。',
        '+4. 查询类请求优先使用只读工具，不要先请求澄清再查询；仅当所有允许的查询工具都无法获得所需字段时，才向用户请求澄清并列出 missing_fields。',
        ' ',
        ' ## 状态映射',
        ' - 进行中 → in_progress',
        '+ - 处理中 → in_progress',
        ' - 待办 → todo',
        ' - 已完成 → done',
        '+ - 已关闭 → done',
      ].join('\n'),
    },
    {
      path: 'references/task_query.md',
      status: 'modified',
      additions: 3,
      deletions: 1,
      diff: [
        '--- a/references/task_query.md',
        '+++ b/references/task_query.md',
        '@@ -5,6 +5,8 @@',
        ' ',
        ' ## 流程',
        '-1. 从请求中提取 project_id、status、assignee。',
        '-2. 调用 list_tasks；需要详细信息时再调用 get_task。',
        '+1. 从请求中提取 project_id、status、assignee。',
        '+2. 状态关键词先做映射：进行中/处理中 → in_progress；待办 → todo；已完成/已关闭 → done。',
        '+3. 调用 list_tasks；需要详细信息时再调用 get_task。',
        '+4. 若缺少 project_id，先尝试从上下文解析，仍无法确定时再澄清。',
      ].join('\n'),
    },
  ];
  return {
    changes,
    changed_files: candidate.changed_files,
    diagnosis: candidate.diagnosis,
    hypotheses: candidate.hypotheses.map((item) => (typeof item === 'string' ? { summary: item } : item)),
    expected_effect: candidate.expected_effect,
    risk_cases: candidate.risk_cases,
    related_cases: candidate.diagnosis?.related_cases || [],
    patch: candidate.patch,
    skill_version: 'skillv_2',
    candidate_skill_version: candidate.skill_version_id,
  };
}

function issuesFor(runId) {
  const events = runEvents.get(runId) || [];
  const items = events
    .filter((event) => ['warning', 'error'].includes(event.level) && event.payload && event.payload.issue)
    .map((event) => ({
      seq: event.seq,
      ts: event.ts,
      type: event.type,
      level: event.level,
      stage: event.stage,
      case_id: event.case_id,
      ...clone(event.payload.issue),
    }));
  return {
    items,
    counts: { error: items.filter((item) => item.level === 'error').length, warning: items.filter((item) => item.level === 'warning').length },
    total: items.length,
  };
}

/* ------------------------------------------------------------------ */
/* running simulation                                                  */
/* ------------------------------------------------------------------ */

const SIMULATION_STEPS = [
  { delay: 1800, type: 'model_call_finished', case_id: 'case_010', payload: { finish_reason: 'tool_calls', usage: { input_tokens: 402, output_tokens: 94 } } },
  { delay: 2600, type: 'tool_call_started', case_id: 'case_010', payload: { tool: 'get_task', arguments: { task_id: 'T-2049' } } },
  { delay: 3400, type: 'score_completed', case_id: 'case_010', level: 'warning', payload: { case_id: 'case_010', passed: false, status: 'fail', diagnostics: [{ category: 'missing_arg', field_path: '$.task_id', expected_value: 'T-2049', actual_value: undefined, tool: 'get_task', case_id: 'case_010' }] } },
  { delay: 4200, type: 'case_completed', case_id: 'case_010', payload: { passed: false, phase: 'candidate' } },
  { delay: 5200, type: 'model_call_finished', case_id: 'case_011', payload: { finish_reason: 'tool_calls' } },
  { delay: 6000, type: 'tool_result', case_id: 'case_011', payload: { tool: 'update_task_status', matched_fixture: true } },
  { delay: 6800, type: 'score_completed', case_id: 'case_011', payload: { case_id: 'case_011', passed: true, status: 'pass' } },
  { delay: 7600, type: 'case_completed', case_id: 'case_011', payload: { passed: true, phase: 'candidate' } },
  { delay: 8600, type: 'progress_updated', payload: { completed: 4, total: 8 } },
  { delay: 9600, type: 'retest_completed', payload: { improvements: 2, regressions: 0 } },
  { delay: 10400, type: 'stage_completed', stage: 'retest', payload: { ok: true } },
];

let simulationStarted = false;
function simulateRunningRun() {
  if (simulationStarted) return;
  simulationStarted = true;
  const run = runs.find((item) => item.id === 'run_running_1');
  if (!run) return;
  let elapsed = 0;
  for (const [index, step] of SIMULATION_STEPS.entries()) {
    elapsed += 700;
    setTimeout(() => {
      if (run.status !== 'running') return;
      pushEvent(run.id, step.type, {
        ts: new Date().toISOString(),
        stage: step.stage || 'retest',
        case_id: step.case_id,
        split: step.case_id ? 'validation' : null,
        variant: step.case_id ? 'candidate' : null,
        repeat_index: step.case_id ? 1 : null,
        call_id:
          step.type === 'model_call_finished'
            ? `call_cand_${step.case_id}_1`
            : step.type === 'tool_call_started' || step.type === 'tool_result'
            ? `call_cand_${step.case_id}_1`
            : null,
        level: step.level || 'info',
        payload: step.payload,
      });
      if (index === SIMULATION_STEPS.length - 1) {
        // decision + completion
        setTimeout(() => {
          if (run.status !== 'running') return;
          pushEvent(run.id, 'stage_started', { ts: new Date().toISOString(), stage: 'decision', payload: { stage: 'decision' } });
          for (const rule of ACCEPTANCE_ACCEPT.rules) {
            pushEvent(run.id, 'rule_evaluated', {
              ts: new Date().toISOString(),
              stage: 'decision',
              level: rule.ok ? 'info' : 'warning',
              payload: { rule: rule.rule, ok: rule.ok, detail: rule.detail },
            });
          }
          run.metrics = { ...buildMetrics(ACCEPT_METRIC_DATA), diagnostics: {} };
          run.acceptance = ACCEPTANCE_ACCEPT;
          run.decision = 'accept';
          run.decision_reason = ACCEPTANCE_ACCEPT.reason;
          run.status = 'completed';
          run.stage = 'decision';
          run.candidate_skill_version_id = 'skillv_3';
          run.finished_at = new Date().toISOString();
          run.stage_progress = { stage: 'decision', completed: 1, total: 1, current_case_ids: [], started_at: isoAgo(6 * 60_000), last_activity_at: new Date().toISOString(), message: '评测结论已生成' };
          run.case_stats = { case_results: 36, scored: 36, errors: 0, planned: 18, completed: 18, valid: 18 };
          pushEvent(run.id, 'decision_made', {
            ts: new Date().toISOString(),
            stage: 'decision',
            payload: { decision: 'accept', reason: ACCEPTANCE_ACCEPT.reason, rules_version: 'accept-v1' },
          });
          pushEvent(run.id, 'run_completed', {
            ts: new Date().toISOString(),
            payload: { decision: 'accept', duration_ms: 240_000, tokens: 22_100 },
          });
        }, 1200);
      }
    }, elapsed);
  }
}

/* ------------------------------------------------------------------ */
/* fake EventSource                                                    */
/* ------------------------------------------------------------------ */

function createMockEventSource(url) {
  const parsed = new URL(url, window.location.origin);
  const match = parsed.pathname.match(/^\/api\/v3\/runs\/([^/]+)\/events$/);
  const runId = match ? decodeURIComponent(match[1]) : '';
  const afterSeq = Number(parsed.searchParams.get('after_seq') || 0);
  const source = {
    readyState: 0,
    onopen: null,
    onerror: null,
    onmessage: null,
    lastEventId: '',
    close() {
      source.readyState = 2;
      if (listener && eventListeners.has(runId)) {
        eventListeners.get(runId).delete(listener);
      }
      clearTimeout(openTimer);
    },
  };
  let listener = null;
  const emit = (event) => {
    if (source.readyState === 2) return;
    source.lastEventId = String(event.seq);
    try {
      source.onmessage && source.onmessage({ data: JSON.stringify(event), lastEventId: String(event.seq), type: 'message' });
    } catch (error) {
      console.error('mock SSE handler failed', error);
    }
  };
  const openTimer = setTimeout(() => {
    if (source.readyState === 2) return;
    source.readyState = 1;
    try {
      source.onopen && source.onopen({});
    } catch {
      /* ignore */
    }
    let lastEmitted = afterSeq;
    let index = 0;
    const replay = () => {
      if (source.readyState === 2) return;
      const existing = (runEvents.get(runId) || []).filter((event) => event.seq > lastEmitted);
      const batchEnd = Math.min(existing.length, index + 6);
      for (; index < batchEnd; index += 1) {
        emit(existing[index]);
        lastEmitted = existing[index].seq;
      }
      if (index < existing.length) setTimeout(replay, 15);
      else {
        listener = (event) => emit(event);
        if (!eventListeners.has(runId)) eventListeners.set(runId, new Set());
        eventListeners.get(runId).add(listener);
        // close the tiny gap between the last replay batch and listener registration
        for (const event of runEvents.get(runId) || []) {
          if (event.seq > lastEmitted) {
            emit(event);
            lastEmitted = event.seq;
          }
        }
      }
    };
    replay();
  }, 40);
  return source;
}

/* ------------------------------------------------------------------ */
/* router                                                              */
/* ------------------------------------------------------------------ */

function ok(data, status = 200) {
  return { status, data };
}

function notFound(message = '资源不存在') {
  throw new ApiError(message, { code: 'not_found', status: 404 });
}

function asList(payload) {
  return Array.isArray(payload) ? payload : payload && Array.isArray(payload.items) ? payload.items : [];
}

async function handleDownload(url) {
  if (url.includes('/export')) {
    const versionId = url.split('/skill-versions/')[1].split('/')[0];
    const files = skillVersionFiles.get(decodeURIComponent(versionId)) || BASE_FILES;
    const text = JSON.stringify({ mock: true, note: 'mock 模式下为 JSON 占位，不是真实 ZIP', files }, null, 2);
    return new Blob([text], { type: 'application/json' });
  }
  if (url.includes('report.json')) {
    return new Blob([JSON.stringify({ note: 'mock report', metrics: ACCEPT_METRIC_DATA }, null, 2)], { type: 'application/json' });
  }
  if (url.includes('report.csv')) {
    return new Blob(['metric,baseline,candidate\nrouting,0.72,0.86\ntool_args,0.66,0.81\n'], { type: 'text/csv' });
  }
  return new Blob(['mock'], { type: 'text/plain' });
}

async function fetchImpl({ method, path, query = {}, body }) {
  await sleep(60 + Math.random() * 120);
  const route = `${method.toUpperCase()} ${path}`;

  if (route === 'GET /healthz') return ok({ status: 'ok', version: 'mock-1.0' });

  if (route === 'GET /projects') {
    const includeArchived = query.include_archived === true || query.include_archived === 'true';
    const items = projects.filter((project) => includeArchived || !project.archived);
    const enriched = items.map((project) => {
      const runsForProject = runs.filter((run) => run.project_id === project.id);
      const latest = runsForProject.sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
      return {
        ...clone(project),
        current_skill_version: project.current_skill_version_id ? clone(skillVersions.find((item) => item.id === project.current_skill_version_id)) : null,
        latest_run: latest ? { id: latest.id, status: latest.status, decision: latest.decision, mode: latest.mode, created_at: latest.created_at } : null,
        run_count: runsForProject.length,
      };
    });
    return ok(listResponse(enriched));
  }

  if (route === 'POST /projects') {
    const name = (body?.name || '').trim();
    if (!name) throw new ApiError('项目名称不能为空', { code: 'validation_error', status: 422, field_path: 'name' });
    const project = {
      id: `project_${Date.now()}`,
      name,
      description: body?.description || '',
      archived: 0,
      current_skill_version_id: null,
      current_environment_id: null,
      current_dataset_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    projects.unshift(project);
    return ok(projectDetail(project), 201);
  }

  let match = path.match(/^\/projects\/([^/]+)$/);
  if (match && method === 'GET') {
    return ok(projectDetail(findProject(decodeURIComponent(match[1]))));
  }
  if (match && method === 'PATCH') {
    const project = findProject(decodeURIComponent(match[1]));
    if (body && typeof body.name === 'string' && body.name.trim()) project.name = body.name.trim();
    if (body && typeof body.description === 'string') project.description = body.description;
    if (body && body.archived !== undefined) project.archived = body.archived ? 1 : 0;
    project.updated_at = new Date().toISOString();
    return ok(projectDetail(project));
  }

  match = path.match(/^\/projects\/([^/]+)\/overview$/);
  if (match && method === 'GET') {
    return ok(overviewFor(findProject(decodeURIComponent(match[1]))));
  }

  match = path.match(/^\/projects\/([^/]+)\/skill-versions$/);
  if (match && method === 'GET') {
    const project = findProject(decodeURIComponent(match[1]));
    return ok(listResponse(skillVersions.filter((version) => version.project_id === project.id)));
  }
  if (match && method === 'POST') {
    const project = findProject(decodeURIComponent(match[1]));
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      const version = makeVersion({
        id: `skillv_import_${Date.now()}`,
        parentId: project.current_skill_version_id,
        status: 'published',
        files: BASE_FILES,
        message: '导入 Skill 包 ZIP（mock）',
        createdAt: new Date().toISOString(),
      });
      version.project_id = project.id;
      skillVersions.unshift(version);
      skillVersionFiles.set(version.id, clone(BASE_FILES));
      project.current_skill_version_id = version.id;
      return ok(version, 201);
    }
    const incoming = body && Array.isArray(body.files) ? body.files : null;
    if (!incoming) throw new ApiError('缺少 files', { code: 'validation_error', status: 422, field_path: 'files' });
    const files = {};
    for (const file of incoming) {
      if (!file || !file.path) throw new ApiError('文件缺少 path', { code: 'validation_error', status: 422, field_path: 'files[].path' });
      files[file.path] = file.content ?? '';
    }
    if (!files['SKILL.md']) throw new ApiError('缺少主 Skill 文件 SKILL.md', { code: 'missing_main', status: 422, field_path: 'files' });
    const version = makeVersion({
      id: `skillv_${Date.now()}`,
      parentId: body.parent_id || project.current_skill_version_id,
      status: body.status === 'published' ? 'published' : 'draft',
      files,
      message: body.message || (body.status === 'published' ? '发布新版本' : '保存草稿'),
      createdAt: new Date().toISOString(),
      sourceRunId: body.source_run_id || null,
    });
    version.project_id = project.id;
    skillVersions.unshift(version);
    skillVersionFiles.set(version.id, files);
    if (body.set_current && version.status === 'published') project.current_skill_version_id = version.id;
    return ok(version, 201);
  }

  match = path.match(/^\/skill-versions\/([^/]+)$/);
  if (match && method === 'GET') {
    const version = skillVersions.find((item) => item.id === decodeURIComponent(match[1]));
    if (!version) return notFound('Skill 版本不存在');
    return ok(clone(version));
  }

  match = path.match(/^\/skill-versions\/([^/]+)\/activate$/);
  if (match && method === 'POST') {
    const version = skillVersions.find((item) => item.id === decodeURIComponent(match[1]));
    if (!version) return notFound('Skill 版本不存在');
    const project = projects.find((item) => item.id === version.project_id);
    if (!project) return notFound('项目不存在');
    if (body.expected_current_version !== undefined && project.current_skill_version_id !== body.expected_current_version) {
      throw new ApiError('项目当前 Skill 已变更', { code: 'version_conflict', status: 409 });
    }
    project.current_skill_version_id = version.id;
    return ok(clone(version));
  }

  match = path.match(/^\/skill-versions\/([^/]+)\/publish$/);
  if (match && method === 'POST') {
    const version = skillVersions.find((item) => item.id === decodeURIComponent(match[1]));
    if (!version) return notFound('Skill 版本不存在');
    version.status = 'published';
    const project = projects.find((item) => item.id === version.project_id);
    if (project) project.current_skill_version_id = version.id;
    return ok(clone(version));
  }

  match = path.match(/^\/skill-versions\/([^/]+)\/files\/(.+)$/);
  if (match) {
    const version = skillVersions.find((item) => item.id === decodeURIComponent(match[1]));
    if (!version) return notFound('Skill 版本不存在');
    const filePath = decodeURIComponent(match[2]);
    const files = skillVersionFiles.get(version.id) || {};
    if (method === 'GET') {
      if (!(filePath in files)) return notFound(`文件不存在：${filePath}`);
      const content = files[filePath];
      return ok({ path: filePath, content, size: new TextEncoder().encode(content).length, encoding: 'utf-8', sha256: `mock-${filePath.length}` });
    }
    if (method === 'PUT') {
      if (version.status !== 'draft') throw new ApiError('只有草稿版本允许编辑', { code: 'version_not_draft', status: 409 });
      files[filePath] = body?.content ?? '';
      skillVersionFiles.set(version.id, files);
      version.files = fileList(files);
      version.content_hash = `sha256:mock-${Date.now()}`;
      return ok({ path: filePath, content: files[filePath], saved: true });
    }
  }

  match = path.match(/^\/skill-versions\/([^/]+)\/diff$/);
  if (match && method === 'GET') {
    const targetId = decodeURIComponent(match[1]);
    const fromId = query.from ? decodeURIComponent(String(query.from)) : null;
    const changes = [];
    const before = fromId ? skillVersionFiles.get(fromId) || {} : {};
    const after = skillVersionFiles.get(targetId) || {};
    for (const filePath of new Set([...Object.keys(before), ...Object.keys(after)])) {
      if (before[filePath] === after[filePath]) continue;
      changes.push({
        path: filePath,
        status: !(filePath in before) ? 'added' : !(filePath in after) ? 'removed' : 'modified',
        additions: 0,
        deletions: 0,
        diff: `--- a/${filePath}\n+++ b/${filePath}\n@@ -1,1 +1,1 @@\n-${(before[filePath] || '').split('\n')[0]}\n+${(after[filePath] || '').split('\n')[0]}`,
      });
    }
    return ok({ changes });
  }

  match = path.match(/^\/projects\/([^/]+)\/environments$/);
  if (match && method === 'GET') {
    const project = findProject(decodeURIComponent(match[1]));
    return ok(listResponse(environments.filter((env) => env.project_id === project.id)));
  }
  if (match && method === 'POST') {
    const project = findProject(decodeURIComponent(match[1]));
    const payload = body || {};
    const missing = ['prompt', 'model_config'].filter((field) => !payload[field]);
    if (missing.length) throw new ApiError(`执行环境缺少字段: ${missing.join(', ')}`, { code: 'validation_error', status: 422, field_path: missing[0] });
    const versionSeq = Math.max(...environments.filter((env) => env.project_id === project.id).map((env) => env.version_seq), 0) + 1;
    const env = baseEnvironment(`env_${Date.now()}`, versionSeq, {
      status: payload.status === 'draft' ? 'draft' : 'published',
      prompt: payload.prompt,
      model_config: payload.model_config,
      optimizer_config: payload.optimizer_config || {},
      loader_config: payload.loader_config || {},
      run_config: payload.run_config || {},
      budget_config: payload.budget_config || {},
      scorer_config: payload.scorer_config || {},
      acceptance_config: payload.acceptance_config || {},
      adapter_hash: 'sha256:mock-adapter-new',
      // model_config is used above
      // eslint-disable-next-line no-undef
    });
    env.project_id = project.id;
    env.created_at = new Date().toISOString();
    env.published_at = env.status === 'published' ? env.created_at : null;
    environments.unshift(env);
    if (env.status === 'published') project.current_environment_id = env.id;
    return ok(clone(env), 201);
  }

  match = path.match(/^\/environments\/([^/]+)$/);
  if (match && method === 'GET') {
    const env = environments.find((item) => item.id === decodeURIComponent(match[1]));
    if (!env) return notFound('执行环境不存在');
    return ok(clone(env));
  }

  match = path.match(/^\/environments\/([^/]+)\/publish$/);
  if (match && method === 'POST') {
    const env = environments.find((item) => item.id === decodeURIComponent(match[1]));
    if (!env) return notFound('执行环境不存在');
    env.status = 'published';
    env.published_at = new Date().toISOString();
    const project = projects.find((item) => item.id === env.project_id);
    if (project) project.current_environment_id = env.id;
    return ok(clone(env));
  }

  if (route === 'POST /environments/test-connection') {
    const config = body?.model_config || body || {};
    const target = body?.target || 'business';
    const probeInput = typeof body?.input === 'string' && body.input ? body.input : 'ping';
    await sleep(450);
    if (String(config.api_key_ref || '').includes('missing') || String(config.base_url || '').includes('fail')) {
      return ok({ ok: false, performed: false, simulated: true, target, provider: config.provider || 'openai_http', message: '模拟连接检查失败：鉴权引用不可用。', latency_ms: 480, error_code: 'auth_failed', response_preview: null });
    }
    return ok({
      ok: true,
      performed: false,
      simulated: true,
      target,
      provider: config.provider || 'openai_http',
      message: '模拟连接检查完成；未发起真实模型请求。',
      latency_ms: 320,
      model: config.model || 'Qwen/Qwen3.5-397B-A17B',
      response_preview: `mock response preview: ${probeInput}`,
    });
  }

  match = path.match(/^\/projects\/([^/]+)\/datasets$/);
  if (match && method === 'GET') {
    const project = findProject(decodeURIComponent(match[1]));
    return ok(listResponse(datasets.filter((dataset) => dataset.project_id === project.id)));
  }
  if (match && method === 'POST') {
    const project = findProject(decodeURIComponent(match[1]));
    const parsed = parseDatasetContent(body?.content || '');
    if (parsed.errors.length && !body?.exclude_invalid) {
      throw new ApiError(`数据集中有 ${parsed.errors.length} 条无效记录，请修复或选择剔除后发布`, {
        code: 'invalid_cases',
        status: 422,
        details: { errors: parsed.errors },
      });
    }
    const cases = parsed.validCases;
    if (!cases.length) throw new ApiError('数据集不能为空', { code: 'validation_error', status: 422 });
    const versionSeq = Math.max(...datasets.filter((dataset) => dataset.project_id === project.id).map((dataset) => dataset.version_seq), 0) + 1;
    const dataset = {
      id: `ds_${Date.now()}`,
      project_id: project.id,
      parent_id: project.current_dataset_id,
      version_seq: versionSeq,
      status: 'published',
      hash: `sha256:mock-ds-${Date.now()}`,
      split_config: body?.split_config || { optimize: 0.7, validation: 0.2, final_test: 0.1, seed: 42 },
      source_name: body?.source_name || body?.filename || 'import.jsonl',
      case_count: cases.length,
      import_report: { total: parsed.total, valid: cases.length, invalid: parsed.errors.length, errors: parsed.errors },
      created_at: new Date().toISOString(),
      published_at: new Date().toISOString(),
    };
    datasets.unshift(dataset);
    datasetCasesByVersion.set(dataset.id, cases);
    project.current_dataset_id = dataset.id;
    return ok(clone(dataset), 201);
  }

  if (route === 'POST /datasets/preflight') {
    const parsed = parseDatasetContent(body?.content || '');
    const stats = {
      total: parsed.total,
      valid: parsed.validCases.length,
      invalid: parsed.errors.length,
      splits: parsed.splitCounts,
      coverage: parsed.coverage,
    };
    return ok({
      ok: parsed.errors.length === 0,
      filename: body?.filename || 'import.jsonl',
      stats,
      errors: parsed.errors,
      warnings: parsed.warnings,
      splits: parsed.splitAssignments,
      coverage: parsed.coverage,
      cases: parsed.validCases.map((item) => ({ case_id: item.case_id, query: item.query, split: item.split, group_id: item.group_id, tags: item.tags })),
    });
  }

  match = path.match(/^\/datasets\/([^/]+)\/draft$/);
  if (match && method === 'POST') {
    const source = findDataset(decodeURIComponent(match[1]));
    if (source.status !== 'published') throw new ApiError('只能从已发布数据集创建编辑草稿', { code: 'dataset_not_published', status: 409 });
    const project = projects.find((item) => item.id === source.project_id);
    const versionSeq = Math.max(...datasets.filter((item) => item.project_id === project.id).map((item) => item.version_seq), 0) + 1;
    const draft = { ...clone(source), id: `ds_${Date.now()}`, parent_id: source.id, version_seq: versionSeq, status: 'draft', hash: `sha256:mock-draft-${Date.now()}`, published_at: null };
    datasets.unshift(draft);
    datasetCasesByVersion.set(draft.id, clone(datasetCasesByVersion.get(source.id) || []));
    const cases = datasetCasesByVersion.get(draft.id) || [];
    return ok({ ...clone(draft), split_counts: countBy(cases, 'split'), coverage: coverageFor(cases) }, 201);
  }

  match = path.match(/^\/datasets\/([^/]+)\/cases\/([^/]+)$/);
  if (match && method === 'PUT') {
    const dataset = findDataset(decodeURIComponent(match[1]));
    if (dataset.status !== 'draft') throw new ApiError('已发布数据集不可编辑', { code: 'dataset_read_only', status: 409 });
    if (body?.expected_hash && body.expected_hash !== dataset.hash) throw new ApiError('数据集版本已变化', { code: 'conflict', status: 409 });
    const caseId = decodeURIComponent(match[2]);
    const nextCase = clone(body?.case || {});
    if (nextCase.case_id !== caseId) throw new ApiError('case_id 不允许改名', { code: 'validation_error', status: 422, field_path: 'case.case_id' });
    const cases = datasetCasesByVersion.get(dataset.id) || [];
    const index = cases.findIndex((item) => item.case_id === caseId);
    if (index < 0) return notFound('案例不存在');
    cases[index] = nextCase;
    dataset.hash = `sha256:mock-edit-${Date.now()}`;
    dataset.case_count = cases.length;
    return ok({ dataset: { ...clone(dataset), split_counts: countBy(cases, 'split'), coverage: coverageFor(cases) }, case: clone(nextCase) });
  }

  match = path.match(/^\/datasets\/([^/]+)\/publish$/);
  if (match && method === 'POST') {
    const dataset = findDataset(decodeURIComponent(match[1]));
    dataset.status = 'published';
    dataset.published_at = new Date().toISOString();
    const project = projects.find((item) => item.id === dataset.project_id);
    if (project) project.current_dataset_id = dataset.id;
    return ok(clone(dataset));
  }

  match = path.match(/^\/datasets\/([^/]+)$/);
  if (match && method === 'GET') {
    const dataset = findDataset(decodeURIComponent(match[1]));
    const cases = datasetCasesByVersion.get(dataset.id) || [];
    return ok({ ...clone(dataset), split_counts: countBy(cases, 'split'), coverage: coverageFor(cases) });
  }

  match = path.match(/^\/datasets\/([^/]+)\/cases$/);
  if (match && method === 'GET') {
    const dataset = findDataset(decodeURIComponent(match[1]));
    let cases = datasetCasesByVersion.get(dataset.id) || [];
    if (query.split) cases = cases.filter((item) => item.split === query.split);
    if (query.tag) cases = cases.filter((item) => (item.tags || []).includes(query.tag));
    if (query.search) {
      const needle = String(query.search).toLowerCase();
      cases = cases.filter((item) => item.case_id.toLowerCase().includes(needle) || item.query.toLowerCase().includes(needle));
    }
    const limit = Number(query.limit || 200);
    const offset = Number(query.offset || 0);
    return ok({
      ...listResponse(cases, limit, offset),
      split_counts: countBy(datasetCasesByVersion.get(dataset.id) || [], 'split'),
    });
  }

  match = path.match(/^\/datasets\/([^/]+)\/cases\/([^/]+)$/);
  if (match && method === 'GET') {
    const datasetId = decodeURIComponent(match[1]);
    const caseId = decodeURIComponent(match[2]);
    const dataset = findDataset(datasetId);
    const cases = datasetCasesByVersion.get(datasetId) || [];
    const item = cases.find((entry) => entry.case_id === caseId);
    if (!item) return notFound('案例不存在');
    return ok({ ...clone(item), dataset_id: dataset.id, validation: item.validation || {} });
  }

  match = path.match(/^\/projects\/([^/]+)\/versions$/);
  if (match && method === 'GET') {
    const project = findProject(decodeURIComponent(match[1]));
    const entries = [];
    for (const version of skillVersions.filter((item) => item.project_id === project.id)) {
      entries.push({
        type: 'skill',
        id: version.id,
        label: 'Skill 包',
        version_label: version.id.replace('skillv_', 'v'),
        status: version.status,
        parent_id: version.parent_id,
        source_run_id: version.source_run_id,
        created_at: version.created_at,
        message: version.message,
        is_current: project.current_skill_version_id === version.id,
        changed_files: [],
      });
    }
    for (const env of environments.filter((item) => item.project_id === project.id)) {
      entries.push({
        type: 'environment',
        id: env.id,
        label: '执行环境',
        version_label: `v${env.version_seq}`,
        status: env.status,
        parent_id: env.parent_id,
        source_run_id: null,
        created_at: env.created_at,
        message: env.reproducibility_note,
        is_current: project.current_environment_id === env.id,
        hash: env.hash,
      });
    }
    for (const dataset of datasets.filter((item) => item.project_id === project.id)) {
      entries.push({
        type: 'dataset',
        id: dataset.id,
        label: '数据集',
        version_label: `v${dataset.version_seq}`,
        status: dataset.status,
        parent_id: dataset.parent_id,
        source_run_id: null,
        created_at: dataset.created_at,
        message: `${dataset.case_count} 条案例 · ${dataset.source_name}`,
        is_current: project.current_dataset_id === dataset.id,
        case_count: dataset.case_count,
      });
    }
    entries.sort((a, b) => b.created_at.localeCompare(a.created_at));
    return ok(listResponse(entries));
  }

  if (route === 'GET /runs') {
    const projectId = query.project_id;
    let items = runs.filter((run) => !projectId || run.project_id === projectId);
    items = [...items].sort((a, b) => b.created_at.localeCompare(a.created_at));
    return ok(listResponse(items, Number(query.limit || 20), Number(query.offset || 0)));
  }

  if (route === 'POST /runs') {
    const project = findProject(body?.project_id);
    const mode = body?.mode || 'baseline';
    const idempotencyKey = arguments[0]?.headers?.['Idempotency-Key'] || null;
    void idempotencyKey;
    if (!project.current_skill_version_id) throw new ApiError('项目尚未发布 Skill 包', { code: 'config_error', status: 422, field_path: 'project_id' });
    if (!project.current_environment_id) throw new ApiError('项目尚未发布执行环境', { code: 'config_error', status: 422, field_path: 'project_id' });
    if (!project.current_dataset_id) throw new ApiError('项目尚未导入数据集', { code: 'config_error', status: 422, field_path: 'project_id' });
    const active = runs.find((run) => run.project_id === project.id && ['queued', 'running', 'cancel_requested'].includes(run.status));
    if (active) throw new ApiError('该项目已有运行正在进行', { code: 'run_in_progress', status: 409, details: { run_id: active.id } });
    const runId = `run_mock_${Date.now()}`;
    const baseline = runs.find((run) => run.project_id === project.id && run.mode === 'baseline' && run.status === 'completed');
    const run = {
      ...clone(runs.find((item) => item.id === 'run_accept_1')),
      id: runId,
      mode,
      status: 'queued',
      stage: 'preflight',
      stage_progress: { stage: 'preflight', completed: 0, total: 0, current_case_ids: [], started_at: null, last_activity_at: new Date().toISOString(), message: '排队中' },
      baseline_skill_version_id: project.current_skill_version_id,
      candidate_skill_version_id: null,
      environment_id: project.current_environment_id,
      dataset_id: project.current_dataset_id,
      parent_run_id: body?.parent_run_id || (mode === 'optimize' && baseline ? baseline.id : null),
      baseline_run_id: body?.reuse_baseline && baseline ? baseline.id : null,
      baseline_cache_hit: Boolean(body?.reuse_baseline && baseline),
      decision: null,
      decision_reason: '',
      acceptance: null,
      metrics: { baseline: null, candidate: null, delta: null, temporary: true },
      case_stats: { case_results: 0, scored: 0, errors: 0, planned: 18, completed: 0, valid: 0 },
      candidate: null,
      created_at: new Date().toISOString(),
      queued_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
    };
    runs.unshift(run);
    pushEvent(run.id, 'run_created', { stage: 'preflight', payload: { mode, project_id: project.id } });
    // start it after a short delay so the UI can show queued -> running
    setTimeout(() => {
      if (run.status !== 'queued') return;
      run.status = 'running';
      run.started_at = new Date().toISOString();
      run.stage = mode === 'baseline' ? 'baseline_eval' : 'preflight';
      run.stage_progress = { stage: run.stage, completed: 0, total: 18, current_case_ids: ['case_001', 'case_002', 'case_003'], started_at: new Date().toISOString(), last_activity_at: new Date().toISOString(), message: '执行中' };
      pushEvent(run.id, 'run_started', { stage: run.stage, payload: { worker: 'worker-mock' } });
      pushEvent(run.id, 'stage_started', { stage: run.stage, payload: { stage: run.stage, planned: 18 } });
      setTimeout(() => {
        run.status = 'completed';
        run.stage = 'decision';
        run.decision = 'inconclusive';
        run.decision_reason = 'mock 运行已完成：仅用于界面联调，未执行真实模型。';
        run.acceptance = { decision: 'inconclusive', rules: [{ rule: 'validation_complete', ok: false, detail: 'mock 运行未执行复测' }], reason: run.decision_reason };
        run.metrics = buildMetrics({ baseline: ACCEPT_METRIC_DATA.baseline });
        run.finished_at = new Date().toISOString();
        run.stage_progress = { stage: 'decision', completed: 1, total: 1, current_case_ids: [], started_at: run.started_at, last_activity_at: new Date().toISOString(), message: 'mock 运行结束' };
        pushEvent(run.id, 'stage_completed', { stage: 'baseline_eval', payload: { ok: true } });
        pushEvent(run.id, 'decision_made', { stage: 'decision', payload: { decision: 'inconclusive', reason: run.decision_reason } });
        pushEvent(run.id, 'run_completed', { payload: { decision: 'inconclusive' } });
      }, 4200);
    }, 700);
    return ok({ run_id: run.id, status: 'queued', reused_baseline_run_id: run.baseline_run_id }, 202);
  }

  match = path.match(/^\/runs\/([^/]+)$/);
  if (match && method === 'GET') {
    return ok(clone(findRun(decodeURIComponent(match[1]))));
  }

  match = path.match(/^\/runs\/([^/]+)\/cancel$/);
  if (match && method === 'POST') {
    const run = findRun(decodeURIComponent(match[1]));
    if (['completed', 'failed', 'cancelled', 'interrupted'].includes(run.status)) return ok(clone(run));
    run.status = 'cancelled';
    run.decision_reason = '用户取消（已产生的费用不会回退）';
    run.finished_at = new Date().toISOString();
    run.stage_progress = { ...(run.stage_progress || {}), current_case_ids: [], last_activity_at: new Date().toISOString(), message: '已取消' };
    pushEvent(run.id, 'run_cancelled', { stage: run.stage, payload: { reason: 'user_cancelled', note: '已产生的费用不会回退' } });
    return ok(clone(run));
  }

  match = path.match(/^\/runs\/([^/]+)\/apply$/);
  if (match && method === 'POST') {
    const run = findRun(decodeURIComponent(match[1]));
    if (run.decision !== 'accept') throw new ApiError('该运行未达标，不能应用候选', { code: 'not_applicable', status: 409 });
    const project = findProject(run.project_id);
    if (body?.expected_current_version && project.current_skill_version_id !== body.expected_current_version) {
      throw new ApiError('项目当前版本已变更，请重新比较后再应用', {
        code: 'conflict',
        status: 409,
        details: { current_version: project.current_skill_version_id, expected_current_version: body.expected_current_version },
      });
    }
    const candidateVersionId = body?.candidate_id ? 'skillv_3' : run.candidate_skill_version_id;
    project.current_skill_version_id = candidateVersionId;
    run.decision = 'accept';
    run.decision_reason = `${run.decision_reason}（已应用）`;
    return ok({ decision: 'accept', project: { current_skill_version_id: candidateVersionId }, applied_candidate: { candidate_id: run.candidate?.id, skill_version_id: candidateVersionId } });
  }

  match = path.match(/^\/runs\/([^/]+)\/cases$/);
  if (match && method === 'GET') {
    const runId = decodeURIComponent(match[1]);
    findRun(runId);
    let items = clone(runCases(runId));
    if (query.only_failed === true || query.only_failed === 'true') {
      items = items.filter((item) => ['regressed', 'unchanged_fail'].includes(item.change_type) || item.baseline?.passed === false || item.candidate?.passed === false);
    }
    if (query.search) {
      const needle = String(query.search).toLowerCase();
      items = items.filter((item) => item.case_id.toLowerCase().includes(needle) || item.query.toLowerCase().includes(needle));
    }
    if (query.phase) {
      if (query.phase === 'baseline') items = items.map((item) => ({ ...item, candidate: null, change_type: 'pending' }));
      if (query.phase === 'candidate') items = items.map((item) => ({ ...item, baseline: null, change_type: 'pending' }));
    }
    items.sort((a, b) => changeRank(a.change_type) - changeRank(b.change_type));
    return ok({ ...listResponse(items, Number(query.limit || 200), Number(query.offset || 0)), case_stats: { regressions: items.filter((item) => item.change_type === 'regressed').length, fixed: items.filter((item) => item.change_type === 'fixed').length } });
  }

  match = path.match(/^\/runs\/([^/]+)\/cases\/([^/]+)$/);
  if (match && method === 'GET') {
    return ok(caseDetail(decodeURIComponent(match[1]), decodeURIComponent(match[2])));
  }

  match = path.match(/^\/(runs|projects)\/([^/]+)\/debug-cases$/);
  if (match && method === 'POST') {
    // 试跑 in demo mode: replay the recorded side of a case (no model is called).
    const caseId = body?.case_id;
    const trialMeta = { skill_version: { id: 'sv_mock', status: body?.variant === 'candidate' ? 'candidate' : 'published' }, environment: { id: 'env_mock', version_seq: 1, status: 'published' }, trial: true, note: '演示数据：重放已有结果，没有调用模型。' };
    if (!caseId) {
      if (!String(body?.query || '').trim()) throw new ApiError('请选择一个案例，或输入要试跑的问题', { code: 'validation_failed', status: 422, field_path: 'query' });
      return ok({ ...trialMeta, case_id: null, query: body.query, scored: false, expected: null, events: [], result: { selected_skills: [], action: 'no_action', tool_calls: [], missing_fields: [], passed: null, status: null, diagnostics: [], judgments: [], usage: {}, duration_ms: 0 } });
    }
    const runId = match[1] === 'runs' ? decodeURIComponent(match[2]) : runs.find((run) => runCases(run.id).some((item) => item.case_id === caseId))?.id;
    if (!runId) return notFound('案例不存在');
    const detail = caseDetail(runId, caseId);
    const side = (body?.variant === 'candidate' ? detail.candidate : detail.baseline) || detail.baseline || detail.candidate;
    return ok({ ...trialMeta, case_id: caseId, query: detail.query, split: detail.split, scored: true, expected: detail.expected, events: detail.events || [], result: clone(side) });
  }

  match = path.match(/^\/runs\/([^/]+)\/diff$/);
  if (match && method === 'GET') {
    return ok(runDiffPayload(findRun(decodeURIComponent(match[1]))));
  }

  match = path.match(/^\/runs\/([^/]+)\/issues$/);
  if (match && method === 'GET') {
    const runId = decodeURIComponent(match[1]);
    findRun(runId);
    return ok(issuesFor(runId));
  }

  match = path.match(/^\/runs\/([^/]+)\/events\/(\d+)\/model-output$/);
  if (match && method === 'GET') {
    const runId = decodeURIComponent(match[1]);
    findRun(runId);
    const event = (runEvents.get(runId) || []).find(item => item.seq === Number(match[2]));
    if (!event) return notFound('运行记录不存在');
    const payload = event.payload || {};
    const saved = payload.response || payload.raw_output || payload.output_preview;
    return ok({
      source: saved ? 'captured' : 'unavailable', scope: 'model_call', case_results: [],
      response: saved ? (typeof saved === 'string' ? { content: saved, tool_calls: [] } : clone(saved)) : null,
      message: saved ? '演示事件中保存的返回示例。' : '此演示事件没有逐次返回内容；真实新运行会保存模型输出。',
    });
  }

  match = path.match(/^\/runs\/([^/]+)\/event-history$/);
  if (match && method === 'GET') {
    const runId = decodeURIComponent(match[1]);
    findRun(runId);
    let events = (runEvents.get(runId) || []).filter((event) => event.seq > Number(query.after_seq || 0));
    if (query.level) events = events.filter((event) => event.level === query.level);
    if (query.type) events = events.filter((event) => event.type === query.type);
    if (query.case_id) events = events.filter((event) => event.case_id === query.case_id);
    if (query.stage) events = events.filter((event) => event.stage === query.stage);
    const limit = Number(query.limit || 200);
    const page = events.slice(0, limit);
    return ok({ items: clone(page), total: events.length, limit, offset: 0 });
  }

  match = path.match(/^\/runs\/([^/]+)\/trace-artifacts\/([^/]+)$/);
  if (match && method === 'GET') {
    const runId = decodeURIComponent(match[1]);
    const artifactId = decodeURIComponent(match[2]);
    findRun(runId);
    if (artifactId === 'artifact_opt_invalid_1') {
      return ok({
        artifact: { id: artifactId, kind: 'model_response', size: 3050, content_type: 'application/json', summary: '优化器原始返回（脱敏）', created_at: new Date().toISOString() },
        content: {
          raw: '{"summary": "补充状态映射", "edits": [{"path": "SKILL.md", "op": "replace", "search": "进行中 → in_progress", "replace": "进行中 → in_progress\\n处理中 → in_progress", "reason": "case_002"}]',
          parse_error: { message: "Expecting ',' delimiter", line: 1, column: 156, char: 902 },
        },
      });
    }
    return ok({
      artifact: { id: artifactId, kind: 'model_request', size: 24_820, content_type: 'application/json', summary: '模型请求（脱敏）', created_at: new Date().toISOString() },
      content: {
        model: 'deepseek-optimizer-v1',
        messages: [
          { role: 'system', content: '[优化器系统 Prompt，已脱敏]\n你只能修改白名单内的 Markdown 文件……' },
          { role: 'user', content: '失败案例：case_002（$.status 预期 in_progress，实际 active）、case_006（漏调 get_task）……' },
        ],
        note: '这是按需加载的大内容示例，默认不在日志列表中渲染。',
      },
    });
  }

  throw new ApiError(`mock 未实现的路由：${method} ${path}`, { code: 'mock_route_missing', status: 500 });
}

function countBy(items, key) {
  const counts = {};
  for (const item of items) {
    const value = item[key] || 'unknown';
    counts[value] = (counts[value] || 0) + 1;
  }
  return counts;
}

function changeRank(changeType) {
  return { regressed: 0, unchanged_fail: 1, fixed: 2, unchanged_pass: 3, pending: 4, invalid: 5 }[changeType] ?? 6;
}

function parseDatasetContent(content) {
  const errors = [];
  const warnings = [];
  let items = [];
  let total = 0;
  const text = String(content || '').replace(/^\uFEFF/, '').trim();
  if (!text) {
    return { total: 0, validCases: [], errors: [{ line: 1, case_id: null, code: 'empty', message: '文件内容为空', field_path: null }], warnings: [], splits: [], splitCounts: {}, splitAssignments: [], coverage: coverageFor([]) };
  }
  if (text.startsWith('[')) {
    try {
      items = JSON.parse(text);
      if (!Array.isArray(items)) throw new Error('顶层不是数组');
      total = items.length;
    } catch (error) {
      return { total: 0, validCases: [], errors: [{ line: 1, case_id: null, code: 'parse_error', message: `JSON 解析失败：${error.message}`, field_path: null }], warnings: [], splits: [], splitCounts: {}, splitAssignments: [], coverage: coverageFor([]) };
    }
  } else {
    const lines = text.split('\n');
    total = lines.filter((line) => line.trim()).length;
    for (const [index, line] of lines.entries()) {
      if (!line.trim()) continue;
      try {
        items.push(JSON.parse(line));
      } catch (error) {
        errors.push({ line: index + 1, case_id: null, code: 'parse_error', message: `JSON 解析失败：${error.message}`, field_path: null });
      }
    }
  }
  const skillIds = new Set(MANIFEST.subskills.map((sub) => sub.skill_id));
  const validCases = [];
  const seenIds = new Set();
  const seenQueries = new Map();
  const splitSequences = { optimize: ['optimize', 'validation', 'final_test'], small: ['optimize', 'validation'] };
  const useSmall = total < 12;
  items.forEach((item, index) => {
    const line = index + 1;
    const caseId = item.case_id || item.id || `case_${String(index + 1).padStart(3, '0')}`;
    const caseErrors = [];
    if (!item.query || typeof item.query !== 'string') caseErrors.push({ code: 'missing_query', message: '缺少 query', field_path: 'query' });
    if (!item.expected || typeof item.expected !== 'object') caseErrors.push({ code: 'missing_expected', message: '缺少 expected', field_path: 'expected' });
    const expected = item.expected || {};
    const action = expected.action || 'tool_call';
    if (!['tool_call', 'clarify', 'no_action'].includes(action)) caseErrors.push({ code: 'bad_action', message: `非法 action：${action}`, field_path: 'expected.action' });
    for (const route of expected.routes || []) {
      for (const skillId of route) {
        if (!skillIds.has(skillId)) caseErrors.push({ code: 'unknown_skill_id', message: `未知 skill_id：${skillId}`, field_path: 'expected.routes' });
      }
    }
    for (const call of expected.tool_calls || []) {
      if (!call || typeof call.name !== 'string' || !call.name.trim() || !call.arguments || typeof call.arguments !== 'object' || Array.isArray(call.arguments)) caseErrors.push({ code: 'invalid_tool_call', message: '工具调用需要非空名称和参数对象', field_path: 'expected.tool_calls' });
    }
    if (seenIds.has(caseId)) caseErrors.push({ code: 'duplicate_case_id', message: `重复 case_id：${caseId}`, field_path: 'case_id' });
    if (caseErrors.length) {
      for (const error of caseErrors) errors.push({ line, case_id: caseId, ...error });
      return;
    }
    if (seenQueries.has(item.query)) {
      warnings.push({ line, case_id: caseId, code: 'duplicate_query', message: `与 ${seenQueries.get(item.query)} 的 query 重复，请确认上下文差异` });
    } else {
      seenQueries.set(item.query, caseId);
    }
    seenIds.add(caseId);
    const split = useSmall ? (index % 4 === 3 ? 'validation' : 'optimize') : ['optimize', 'optimize', 'validation', 'optimize', 'final_test'][index % 5];
    validCases.push({
      case_id: caseId,
      query: item.query,
      context: item.context || {},
      expected,
      fixtures: item.fixtures || [],
      split,
      group_id: item.group_id || `group_${caseId}`,
      tags: item.tags || [],
      critical: Boolean(item.critical),
    });
  });
  const splitCounts = countBy(validCases, 'split');
  const splitAssignments = validCases.map((item) => ({ group_id: item.group_id, split: item.split }));
  return { total, validCases, errors, warnings, splits: splitSequences, splitCounts, splitAssignments, coverage: coverageFor(validCases) };
}

/* ------------------------------------------------------------------ */
/* install                                                             */
/* ------------------------------------------------------------------ */

export function installMock() {
  installMockTransport({ fetchImpl, eventSource: createMockEventSource, download: handleDownload });
  simulateRunningRun();
  // eslint-disable-next-line no-console
  console.info('[mock] 已启用演示后端（?mock=1）。数据仅存在于内存中。');
}
