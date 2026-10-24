# 模块契约与接口规范（实现基线）

本文是并行开发的唯一接口基线。任何跨模块的数据结构、事件名、HTTP 路径、状态机
都以本文为准；修改契约需要 Lead 同意并同步所有模块。

## 1. 运行环境

- Python: 仓库内 `.venv/bin/python`（3.12，已安装 starlette / uvicorn / httpx /
  websockets / pytest）。不要新增第三方依赖，不要手写 `pip install`。
- 启动：`.venv/bin/python -m app.server`（默认 http://127.0.0.1:8765）。
- 测试：`.venv/bin/python -m pytest -q`（每个测试用独立临时 `SKILLOPT_DATA_DIR`）。
- 前端：原生 ES Modules + CSS，无构建步骤，由后端 `/` 静态托管 `web/`。
- 不访问外部网络；模型/网关适配器测试使用本地假服务或注入的假 adapter。

## 2. 数据结构（JSON，存 SQLite / 传 API）

### 2.1 Skill 包

包根目录必须包含 `SKILL.md` 与 `manifest.json`：

```json
{
  "version": 1,
  "main": "SKILL.md",
  "subskills": [
    {"skill_id": "task_query", "name": "任务查询", "path": "references/task_query.md",
     "description": "查询项目或成员的任务列表、任务详情与状态"}
  ]
}
```

`app/skill_package.py`（Lead 负责）提供：
- `import_zip(data: bytes) -> PackageImport`：安全检查、解析 manifest、返回
  `{"files": {rel: bytes}, "manifest": {...}, "file_hashes": [...], "content_hash": str,
    "errors": [{"code","path","message"}], "warnings": [...]}`。错误码：
  `missing_main` `missing_manifest` `bad_manifest` `duplicate_skill_id`
  `duplicate_path` `missing_reference` `dangling_reference` `path_traversal`
  `absolute_path` `symlink` `encoding_error` `unsafe_zip` `too_large`。
- `validate_manifest(manifest, files) -> list[error]`。
- `build_zip(files, manifest) -> bytes`。
- `apply_edits(files: dict[str, bytes], edits) -> (new_files, issues)`。

### 2.2 案例（Case）

```json
{
  "case_id": "case_001",
  "query": "查询项目 P123 中进行中的任务",
  "context": {"timezone": "Asia/Shanghai"},
  "expected": {
    "action": "tool_call",
    "routes": [["task_query"]],
    "route_ordered": false,
    "call_match": "ordered_exact",
    "tool_calls": [{"name": "list_tasks",
                    "arguments": {"project_id": "P123", "status": "in_progress"}}],
    "missing_fields": [],
    "variants": []
  },
  "fixtures": [
    {"name": "list_tasks",
     "match": {"project_id": "P123", "status": "in_progress"},
     "response": {"tasks": []}, "consume": "always"}
  ],
  "split": "optimize",
  "group_id": "task_query_01",
  "tags": ["任务查询", "状态映射"]
}
```

- `action` ∈ `tool_call` / `clarify` / `no_action`（缺失视为 `tool_call`）。
- `routes`: 允许的完整子技能路线列表，命中任意一条即通过；集合模式去重比较，
  `route_ordered=true` 时严格按顺序且额外调用算错。
- `tool_calls`: 完整业务调用序列（辅助查询工具也算业务工具；内部工具
  `select_skill` / `read_skill` / `finish` 不计分）。
- `call_match` ∈ `ordered_exact`（默认）/ `unordered`。
- `variants`: `[{"routes": ..., "tool_calls": ..., "call_match": ..., "route_ordered": ...}]`
  可选，用于多种有效调用序列，逐个完整匹配。
- `fixtures[].consume` ∈ `always`（默认）/ `once`（按调用次序消费）。
- `split` ∈ `optimize` / `validation` / `final_test`。
- `critical: true` 可出现在 case 顶层或 `expected` 内，表示关键案例（不得从通过变失败）。

### 2.3 执行结果（Actual）

`app/execution.py`（T2 负责）产出：

```json
{
  "selected_skills": ["task_query"],
  "route_events": [{"skill_id": "task_query", "seq": 3}],
  "action": "tool_call",
  "tool_calls": [{"name": "list_tasks", "arguments": {"project_id": "P123"}}],
  "missing_fields": [],
  "error": {"code": "fixture_miss", "message": "..."} ,
  "error_kind": "capability",
  "finish_reason": "stop",
  "usage": {"input_tokens": 100, "output_tokens": 20},
  "duration_ms": 1200,
  "result_source": "native_tool",
  "capabilities": {"routing": true, "tool_arguments": true, "native_tools": true,
                   "usage": true, "streaming": false},
  "events": [{"type": "...", "payload": {...}, "call_id": "...", ...}],
  "fixture_misses": [{"tool": "list_tasks", "arguments": {...}}]
}
```

- `error_kind` ∈ `capability` / `infrastructure` / `cancelled`，见
  `app/event_types.py::CASE_ERROR_CODES`。
- `result_source` ∈ `native_tool` / `text_parse` / `runtime_event`。
- 内部工具调用不得出现在 `tool_calls` 中。

### 2.4 评分结果（`app/evaluation.py`, T1）

`score_case(expected, actual, tool_schema, compare_rules) -> dict`：

```json
{
  "status": "pass" | "fail" | "infra" | "invalid",
  "passed": false,
  "error_kind": null,
  "routing": {"evaluable": true, "passed": false, "reason": "实际路线 task_search 不在允许路线中",
              "actual_routes": [["task_search"]], "allowed_routes": [["task_query"]]},
  "tool_args": {"evaluable": true, "passed": false, "reason": "...",
                "call_match": "ordered_exact"},
  "action_ok": true,
  "joint": false,
  "diagnostics": [
    {"category": "wrong_route|missing_route|extra_route|missing_tool|extra_tool|wrong_tool|"
                 "missing_arg|extra_arg|type_error|value_error|wrong_order|protocol_error|"
                 "fixture_miss|clarify_mismatch|unexpected_call|action_mismatch",
     "skill_id": "task_query", "tool": "list_tasks", "field_path": "$.status",
     "expected_value": "in_progress", "actual_value": "active", "case_id": "case_001"}
  ]
}
```

规则（score-v1，冻结）：
- `status=="infra"` 的结果不计入任何分母，单独统计并阻止自动晋级。
- capability 失败（format_error/refused/steps_exhausted/context_overflow/truncated/
  protocol_error/fixture_miss/parse_failed）计入分母并判 fail。
- R 分母：`expected.action=="tool_call"` 且 `routes` 非空的案例；分母为 0 时
  `{"value": null, "status": "not_evaluated"}`。
- A 分母：`expected.action=="tool_call"` 且 `expected.tool_calls` 非空的案例；
  分发错误不影响 A 的判定（A05：独立计分）。
- J 分母：全部有效案例；tool_call 需 action、路由（如有）、工具调用全部正确；
  clarify 需 action 正确且 missing_fields 集合一致（expected 未提供 missing_fields
  时只看 action）；no_action 需 action 正确且无业务工具调用。
- 参数比较默认 JSON 深度精确：对象键顺序无关、类型严格、缺失≠null、数组顺序敏
  感、额外参数判错；Schema 校验先于值比较。仅当 `expected.compare` 显式声明
  版本化规则时启用数值容差/时间规范化/数组无序/字段允许值。

`aggregate(results: list[（case, score)], planned: int, duration_ms, usage, cost,
fixture_misses) -> metrics`（同名函数在 evaluator 中）：

```json
{
  "routing": {"value": 0.8, "status": "ok", "evaluated": 20, "passed": 16,
              "total_cases": 25},
  "tool_args": {"value": 0.75, "status": "ok", "evaluated": 20, "passed": 15,
                "total_cases": 25},
  "joint": {"value": 0.68, "status": "ok", "evaluated": 25, "passed": 17,
            "total_cases": 25},
  "diagnostics": {"skills": {...}, "tools": {...}, "fields": {...},
                  "categories": {...}},
  "execution": {"planned": 25, "completed": 25, "valid": 23, "infra_errors": 2,
                "duration_ms": 0, "tokens": 0, "cost": null,
                "fixture_misses": 0, "capability_failures": 2}
}
```

### 2.5 数据集导入（`app/dataset.py`, T1）

- `parse_payload(raw: bytes|str, filename: str) -> (items: list[dict], format: str)`：
  支持 `.jsonl` / `.json`（数组），容忍 BOM 与空行；解析错误带行号。
- `validate_and_prepare(items, *, manifest, tool_schema, split_config, seed) -> dict`：
  返回
  ```json
  {"cases": [...], "errors": [{"line": 3, "case_id": "...", "code": "unknown_skill_id",
    "message": "...", "field_path": "expected.routes[0][0]"}],
   "warnings": [{"line": 7, "code": "duplicate_query", "message": "..."}],
   "stats": {"total": 30, "valid": 28, "invalid": 2, "splits": {...},
             "coverage": {...}}}
  ```
  校验：缺少 query、action 非法、routes 引用未知 skill_id、tool_calls 引用未知
  工具、重复 case_id、conflicting expected、clarify 缺 missing_fields、no_action
  带工具调用、非法 split、fixtures 结构错误。自动生成缺失 `case_id`（`ids.stable_case_id`）。
- `assign_splits(cases, config, seed)`：按 group_id 分组，默认 optimize 70% /
  validation 20% / final_test 10%，固定种子；小数据集允许仅 optimize+validation
  但返回 warning `no_final_test`。分组不跨集合。
- `coverage(cases, skill_ids, tools)`：见 `persistence.dataset_coverage` 的返回结构；
  未覆盖项前端显示“未评估”。
- 已发布数据不可原地修改；重新导入生成新版本。

### 2.6 模型适配层与执行循环（`app/adapters/*`, `app/execution.py`, T2）

统一模型接口：

```python
class ModelAdapter:                     # app/adapters/base.py
    name: str
    capabilities: dict                  # routing/tool_arguments/native_tools/usage/streaming
    def invoke(self, messages: list[dict], config: dict, tools: list[dict], *,
               emit: Callable | None = None) -> dict:
        return {"content": str, "tool_calls": [{"id": str, "name": str,
                "arguments": dict}], "finish_reason": str, "usage": dict | None,
                "model": str, "raw": dict}
```

- `app/adapters/registry.py::build_adapter(model_config) -> ModelAdapter`：
  `provider` ∈ `demo_scripted`（Lead 实现）/ `openai_http` / `enterprise_ws` /
  `flow`（流程级，调用用户 Python `run_case`）。
- `app/adapters/openai_http.py`：标准 Chat Completions HTTP，超时/重试分类
  （401→auth_failed 不重试；超时/429 最多重试 2 次并上报 retry 事件）。
- `app/adapters/enterprise_ws.py`：企业网关 WebSocket 适配器，按 §38/§40：
  保留 api_key/user 等字段，不改成 Authorization；WS 帧进入缓冲区后还原 SSE
  式事件，支持事件跨消息拆分、单消息多事件、`data:` 载荷聚合
  content 与 tool_calls（按 choice 与工具 index 拼接 params）、尾部 usage、
  `[DONE]`；空 delta/心跳不计结果；中途断开→`stream_interrupted`，不跨连接拼接。
  连接/首包/空闲/总超时与心跳可配置。
- `app/adapters/stream_events.py`：纯函数 `SSEEventAssembler.feed(frame: bytes|str) -> list[event]`
  与 `aggregate(events) -> {content, tool_calls, finish_reason, usage}`，供真实适配器
  与测试共用（A12 必须用本地假 WS 服务端覆盖拆分/合并帧、空 delta、尾部 usage、
  结束标记、中断与分片工具参数）。
- `app/obs_tools.py`：内部工具协议 `select_skill(skill_id)` / `read_skill(path)` /
  `finish(action, selected_skills, missing_fields, summary)`；记录路线事件与读取
  事件；finish 摘要与事件不一致→`protocol_error`；纯文本协议由 `app/adapters/text_parse.py`
  固定解析（未知名称/缺行/多义/截断→parse_failed，不调用模型猜测）。
- `app/execution.py::run_case(case_request, adapter, emit) -> CaseExecutionResult`：
  组装 messages（系统 Prompt + 主 Skill + 按加载策略提供的子技能 + query + 允许
  的 context，绝不含 expected），循环最多 `max_model_calls` 次；执行内部工具与
  业务工具（业务工具通过 fixtures 模拟，未匹配立即记录 fixture_miss 且不访问真
  实服务）；输出结果与轨迹事件；支持取消检查。
  `CaseRequest`（dict）：query/context/skill_snapshot_path/system_prompt/
  model_config/request_template/tool_schema/fixtures/limits/run_id/case_id/
  manifest/allowed_skill_ids。`limits` 含 `max_model_calls`、`max_steps`、
  `case_timeout_s`、`max_context_chars`、`context_overflow`（error/frozen_strategy）。
- 取消/超时：`case_timeout_s` 到点→`timeout`（infrastructure）；取消→`cancelled`。

### 2.7 优化器（`app/optimizer.py`, `app/patch.py`, `app/acceptance.py`, T3）

优化器输入（只含 optimize 集失败证据，不含 validation/final_test 内容）：

```json
{
  "project": {"id": "...", "name": "..."},
  "baseline_skill_version": {"id": "...", "manifest": {...}, "files": {"SKILL.md": "..."}},
  "allowed_paths": ["SKILL.md", "references/task_query.md"],
  "failures": [{"case_id": "...", "query": "...", "tags": [...], "expected": {...},
                "actual": {...}, "score": {...}, "events": [...]}],
  "passing_summary": {"count": 20, "sample_case_ids": [...]},
  "budget_remaining": {"tokens": 12000},
  "diagnosis_hints": {...}
}
```

优化器输出契约（§19）：必须包含 `diagnosis`（关联 case_id 与可观察事件）、
`hypotheses`、`changed_files`、`patch`、`expected_effect`、`risk_cases`。
补丁格式：

```json
{"summary": "修正路由关键词与状态映射",
 "edits": [{"path": "SKILL.md", "op": "replace",
            "search": "任务查询：查询任务列表",
            "replace": "任务查询：查询任务列表；当用户提到进行中、状态映射时使用",
            "reason": "case_008 漏选 task_query"}]}
```

- 只允许修改已存在的 `.md` 文件；不得新增/删除文件、不得改 manifest、脚本与资源。
- `patch.apply_edits(files, edits, allowed_paths) -> (patched, issues)`：编辑必须唯一
  命中，否则报 `edit_not_found` / `edit_not_unique`。
- `patch.static_check(base_files, patched_files, manifest, case_ids) -> {"ok", "checks":
  [{"name","ok","message"}], "errors": []}`：路径白名单、引用完整性、结构、
  “不得写入测试 case_id / 标准答案硬编码”扫描。
- `optimizer.build_prompt(context) -> messages`；`optimizer.parse_output(raw) -> dict`
  失败时区分 `optimizer_empty_response` / `optimizer_parse_failed`；
  `optimizer.heuristic_candidate(context) -> dict | None`（无模型/解析失败回退，
  但仍记录 `fallback_started`；无修改记 `fallback_no_change`）。
- `optimizer.generate_candidate(context, adapter, emit, budget) -> dict`：
  返回 `{"status": "ok"|"no_patch"|"failed", "diagnosis":..., "hypotheses":[],
  "changed_files":[], "patch":{"summary","edits":[]}, "expected_effect":"",
  "risk_cases":[], "error_code": null, "used_fallback": false, "raw_output": ""}`。
  必须在上报 `optimizer_*` 事件后（顺序：call_started → 失败/空/解析失败 →
  fallback_started → fallback_no_change / patch_created）才返回；`no_patch` 只是
  最终结果，不代替直接原因。
- `acceptance.evaluate(baseline_metrics, candidate_metrics, *, critical_regressions,
  infra_errors, package_ok, validation_complete) -> {"decision": "accept"|"reject"|
  "inconclusive", "rules": [{"rule","ok","detail"}], "reason": "..."}`（§17，冻结）。

### 2.8 单轮流水线（`app/single_round.py`, Lead）

`execute_run(run_id: str) -> None`：worker 已把 run 置为 running 后调用。阶段：
`preflight → baseline_eval → error_analysis → candidate_generation → static_check →
retest → decision`（baseline 模式只跑 baseline_eval；final_test 模式只跑 final_test）。
每个阶段上报 `stage_started` / `stage_completed` 并同步 `runs.stage` 与进度；
案例级事件必须带 case_id。取消检查：每个案例前后与阶段边界查询
`runs.status in (cancel_requested, cancelled)`；取消后停止派发，在途尽力终止。
异常写入 `run_failed` 事件并落终态。最终 `finish_run` 设置 decision。

### 2.9 事件（所有模块）

事件名与 level 见 `app/event_types.py`。写入统一走
`persistence.append_event(run_id, type, stage=..., case_id=..., ..., payload=...)`，
该函数在事务内分配严格递增 `seq`。SSE/历史接口由 Lead 实现。

警告/错误 payload 必须包含 `issue` 对象（`event_types.issue(...)`），字段：
`error_code/title/message/impact/next_action/cause_event_seq/technical_detail`。
大请求/响应存 `trace_artifacts`，事件里只放 `artifact_id` 引用。

## 3. HTTP API（前缀 `/api/v3`，Lead 实现）

所有错误：`{"error": {"code", "message", "field_path"?, "details"?}}`，422 带字段路径。
列表分页参数 `limit`（默认 50，上限 200）与 `offset`；返回 `{items,total,limit,offset}`。

| 方法与路径 | 行为 |
| --- | --- |
| `GET /healthz` | `{"status":"ok","version":...}` |
| `GET /projects` | 项目列表（`include_archived`） |
| `POST /projects` | `{name, description?}` → 项目 |
| `GET /projects/{pid}` | 项目详情（含 current 版本对象摘要与环境/数据配置完整性） |
| `PATCH /projects/{pid}` | 改名/描述/归档 |
| `GET /projects/{pid}/skill-versions` | 版本列表 |
| `POST /projects/{pid}/skill-versions` | multipart `file`（ZIP）或 JSON `{files:[{path,content}], manifest?, message?}`；校验失败 422；成功创建 published 版本并设为 current（`set_current` 可选） |
| `POST /projects/{pid}/skill-versions/{sid}/publish` | 草稿发布 |
| `GET /skill-versions/{sid}` | 版本 + 文件列表 |
| `GET /skill-versions/{sid}/files/{path:path}` | 文件内容（text JSON） |
| `PUT /skill-versions/{sid}/files/{path:path}` | 草稿文件编辑 |
| `GET /skill-versions/{sid}/export` | ZIP 下载 |
| `GET /skill-versions/{sid}/diff?from=` | 文件差异 |
| `GET /projects/{pid}/environments` | 环境版本列表 |
| `POST /projects/{pid}/environments` | 创建环境版本（draft 或 published） |
| `GET /environments/{eid}` | 环境详情 |
| `POST /environments/{eid}/publish` | 发布并设为 current |
| `POST /environments/test-connection` | 工具/模型连接检查（不落库） |
| `GET /projects/{pid}/datasets` | 数据集版本列表 |
| `POST /datasets/preflight` | JSONL/JSON 导入预检（不落库） |
| `POST /projects/{pid}/datasets` | 发布数据集版本 |
| `GET /datasets/{did}` | 数据集详情 + 划分/覆盖统计 |
| `GET /datasets/{did}/cases` | 案例分页/筛选（split/search/tag/status） |
| `GET /datasets/{did}/cases/{cid}` | 案例详情 |
| `GET /projects/{pid}/versions` | 版本记录（Skill/环境/数据集） |
| `GET /projects/{pid}/overview` | 概览：配置完整性、最近指标、最近运行 |
| `POST /runs` | `{project_id, mode: baseline|optimize|final_test, parent_run_id?, expected_current_version?, reuse_baseline?}` + `Idempotency-Key`；202 `{run_id}` |
| `GET /runs?project_id=` | 运行列表 |
| `GET /runs/{rid}` | 运行详情（状态/阶段/进度/指标/决策/candidate） |
| `POST /runs/{rid}/cancel` | 取消 |
| `POST /runs/{rid}/apply` | `{expected_current_version, candidate_id?}`；冲突 409；冲突返回当前版本 |
| `GET /runs/{rid}/cases?phase=&only_failed=&search=` | 案例对比数据 |
| `GET /runs/{rid}/cases/{cid}` | 单案例详情（基线/候选/评分/参数差异/事件） |
| `GET /runs/{rid}/diff` | 文件差异（候选 vs 基线） |
| `GET /runs/{rid}/events` | SSE，`id: seq`，支持 `Last-Event-ID`/`after_seq`；仅本运行事件 |
| `GET /runs/{rid}/event-history?after_seq=&limit=&level=&type=&case_id=&stage=` | 历史/筛选分页 |
| `GET /runs/{rid}/issues` | 问题摘要（错误/警告事件 + 定位 seq + 影响范围） |
| `GET /runs/{rid}/trace-artifacts/{aid}` | 大内容按需加载 |
| `GET /runs/{rid}/report.json` / `report.csv` | 评测报告下载 |
| `POST /runs/{rid}/debug-cases` | 单案例调试（阶段隔离，不写正式成绩） |
| `GET /projects/{pid}/export-summary` | 导出说明（可选） |

运行对象（`GET /runs/{rid}`）关键字段：
`id, project_id, mode, status, stage, stage_progress:{stage, completed, total,
current_case_ids, started_at, last_activity_at, message}, metrics:{baseline, candidate,
delta, diagnostics}, decision, decision_reason, acceptance, budget, cost,
snapshot:{skill_version, environment, dataset, scorer_version, fingerprints},
candidate:{...}, case_stats, created_at/started_at/finished_at`。
`status ∈ queued|running|cancel_requested|completed|failed|cancelled|interrupted`；
`completed` 时 `decision ∈ accept|reject|inconclusive`。

## 4. 前端要求（T4）

- 中文、炭灰深色、桌面优先 1280–1920；左导航 + 顶栏 + 单主视图；路由用 hash。
- 页面：项目列表、项目概览、Skill 包（240px 文件树 + 编辑器 + 草稿/发布）、
  执行环境（业务 Prompt 默认展开、模型 API/Python 切换、工具与预算折叠、发布
  后只读）、数据集（导入预检弹窗、版本、筛选表格、案例侧栏表单/JSON 切换）、
  运行详情（四标签页 + 运行记录 UX §44–§50）、版本记录（比较/设为当前/下载）。
- 运行详情默认行为：运行中首次进入打开“运行记录”；终态再次进入默认“结果概览”；
  进度按阶段展示，不混合阶段总数；未完成指标标“临时值”。
- 运行记录：问题摘要（无问题不占位，多个问题可逐项定位，定位先清除遮挡筛选并
  提示“已切换到全部记录”，聚焦目标标题）、筛选（全部记录/错误与警告 + 案例范围
  + 优化器事件）、时间日志（时间/状态图标/步骤标题/上下文/展开箭头，可同时展开
  多条）、详情（可读原因/影响 + 请求与 messages / 原始返回 / 评分差异 / 技术详情，
  大内容按需通过接口加载）、跟随/暂停与“有 N 条新记录”、空态“没有匹配的记录”、
  重连提示“实时连接中断，正在重连”、终态 30 秒后可回放历史、按 run_id+seq 去重。
- 所有空态给下一步；失败态给原因与恢复入口；启动按钮在运行时禁用；取消说明
  费用不回退。

## 5. 测试与验收约定

- 测试文件 `tests/test_a01_...py` 对应验收编号；每个测试独立临时数据目录。
- 禁止真实外网调用；假模型/假网关必须走统一适配接口。
- 验收 A01–A20 见需求文档 §35 与 §43/§54；Lead 负责 A01/A02/A08/A09/A14/A19/A20
  集成测试，T1 负责 A03/A04/A05，T2 负责 A11/A12/A13，T3 负责 A06/A07/A10
  （A10 涉及执行器与预算，可与 Lead 合测），T4 负责 A15/A16/A17/A18 的前端
  可验证部分（可用 DOM 快照/接口断言），集成断言由 Lead 补。
