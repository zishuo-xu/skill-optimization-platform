# HTTP / SSE 接口参考

- Base URL：`http://127.0.0.1:8765/api/v3`（单用户本地部署，无认证）。
- 所有请求/响应为 JSON（ZIP 导入、导出下载除外）；字符集 UTF-8。
- 时间均为 UTC ISO-8601（毫秒），界面按本地时区展示。
- 分页按接口定义：运行、案例等接口支持 `limit`/`offset`；项目、Skill 版本、环境和数据集列表返回 `items`/`total`，不统一接受分页参数。事件历史支持 `after_seq` 加 `limit`/`offset`，当前先取最多 1000 条匹配记录再切片，total 是该窗口数量；读取更多历史应推进 after_seq，不能把窗口 total 当作整个运行的事件总数。
- 错误统一格式：

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Skill 包校验失败",
    "field_path": "model_config.provider",
    "details": [{"code": "missing_reference", "path": "references/x.md", "message": "…"}]
  }
}
```

常见状态码：`200` 成功、`201` 资源创建、`202` 运行已入队、`204` 无内容、
`401` 认证（真实模型网关侧）、`404` 不存在、`409` 版本/状态冲突、`422` 校验失败、
`429` 限流（适配器重试）、`500` 平台错误。

---

## 1. 健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | `{status,version,time,worker_started}` |

## 2. 项目

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/projects` | 项目列表；每项含 current 版本摘要、`latest_run`/`recent_run`、`run_count`、完整性与主操作 |
| POST | `/projects` | `{name, description?}`，返回 201 |
| GET | `/projects/{pid}` | 项目详情（当前 Skill/环境/数据集摘要 + `completeness` + `primary_action`） |
| PATCH | `/projects/{pid}` | `{name?, description?, archived?}` |
| GET | `/projects/{pid}/overview` | 配置完整性、最近指标、最近运行、兼容基线、主操作 |
| GET | `/projects/{pid}/versions` | `{skill_versions, environments, datasets, audit}` |
| GET | `/projects/{pid}/pipeline` | 首页流程：五步状态、当前分数、候选/验收摘要、推荐操作、进行中运行与最近运行 |
| GET | `/projects/{pid}/workspace` | 工作副本：每类输入当前版本、待生效草稿与 `pending`/`missing` 列表 |
| POST | `/projects/{pid}/workspace/{skill\|dataset}/draft` | 返回或创建（从当前版本复制）可编辑的工作副本 |
| POST | `/projects/{pid}/workspace/environment` | 把 Prompt 与模型表单保存为待生效草稿（凭据沿草稿链继承） |
| POST | `/projects/{pid}/workspace/{kind}/discard` | 放弃该类未生效修改；草稿标记为 `discarded`，不出现在版本列表 |
| POST | `/projects/{pid}/start` | `{mode, source_run_id?}` 一步启动运行，返回 `{run, frozen}` |
| GET | `/projects/{pid}/export-summary` | 导出说明与版本数量 |

引导项目的列表项、详情及概览额外包含 `guide`：`key`（稳定标识 `task-collaboration-v1`）、`title`、`environment_draft_id`（初始草稿）、`skill_count` 与 `split_counts`（初始资产规模，并非修改后的实时统计）。普通项目省略该字段。引导身份不依赖可修改的项目名称。若有已应用的独立验收记录，且当前 Skill、环境、数据仍与该记录一致，还返回 `completed_run_id` 与 `completed_at`；当前输入变化后省略完成字段。

### 工作副本与一步启动

页面不再要求手动“创建草稿 → 发布”。每类输入（Skill、环境、数据集）最多一个待生效草稿：沿草稿父链能追溯到项目当前版本的最新草稿。`POST /projects/{pid}/start` 在 `evaluate`/`baseline` 和不带 `source_run_id` 的 `optimize` 前，把内容确有变化的待生效草稿逐类发布并设为当前（数据集带 hash 与当前指针 CAS），内容未变的工作副本直接丢弃；`frozen` 列出本次生效的输入。继续优化、`validate` 和 `final_test` 沿用来源证据链，不吸收新修改。冻结失败返回原错误码，`details.kind` 指出哪一类输入。原有发布/激活接口保持兼容。

`pipeline.current` 取值 `prepare | test | optimize | validate | apply | done`；`actions[]` 的 `id` 为 `settings | evaluate | optimize | continue | validate | apply | view_run`。分数块只含 `joint/routing/tool_args` 的 `value/passed/evaluated/status`。运行中的 `stage_progress` 另含 `outcomes`（`passed/failed/infra`）、`recent_failures`（最近 5 条，含 `case_id/kind/reason`）与 `reused`（复用条数），用于实时进度与剩余时间估算。

`overview` 关键字段：

```json
{
  "completeness": {
    "ready": true,
    "missing": [],
    "items": {
      "skill_package": {"ok": true, "label": "Skill 包", "detail": "sv_… · 21 个子技能", "route": "#/projects/p/skill"},
      "environment": {"ok": true, "label": "执行环境", "detail": "版本 v1 · demo_scripted", "route": "#/projects/p/environment"},
      "dataset": {"ok": true, "label": "数据集", "detail": "版本 v1 · 45 个案例", "route": "#/projects/p/dataset"},
      "baseline": {"ok": true, "label": "兼容基线", "detail": "基线运行 run_…", "route": "#/runs/run_…"}
    }
  },
  "metrics": {
    "routing": {"value": 0.833333, "status": "ok", "evaluated": 30, "passed": 25, "total_cases": 33},
    "tool_args": {"value": 0.633333, "status": "ok", "evaluated": 30, "passed": 19, "total_cases": 33},
    "joint": {"value": 0.606061, "status": "ok", "evaluated": 33, "passed": 20, "total_cases": 33},
    "dataset": {"id": "ds_…", "version_seq": 1, "case_count": 45},
    "evaluated_at": "2026-10-24T08:45:30.000Z",
    "run_id": "run_…",
    "stale": false
  },
  "baseline": {"available": true, "run_id": "run_…", "compatible": true, "reason": ""},
  "primary_action": {"mode": "optimize", "label": "单轮优化", "enabled": true, "reason": ""},
  "recent_runs": {"items": [], "total": 2, "limit": 8, "offset": 0}
}
```

## 3. Skill 版本

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/projects/{pid}/skill-versions` | 版本列表 |
| POST | `/projects/{pid}/skill-versions` | 导入；支持 3 种 Content-Type，见下 |
| GET | `/skill-versions/{sid}` | 版本详情（manifest、files、file_tree、summary） |
| GET | `/skill-versions/{sid}/files/{path}` | 文件内容 `{path,encoding,text,editable}` |
| PUT | `/skill-versions/{sid}/files/{path}` | 仅草稿：`{content}` |
| POST | `/skill-versions/{sid}/publish` | `{set_current?:true}` 发布（草稿→已发布）并可选设为当前 |
| POST | `/projects/{pid}/skill-versions/{sid}/publish` | 同上（项目作用域别名） |
| POST | `/skill-versions/{sid}/activate` | `{expected_current_version?}` 设为当前（回滚） |
| GET | `/skill-versions/{sid}/export` | 下载 ZIP（含 manifest，不含密钥/答案） |
| GET | `/skill-versions/{sid}/diff?from=` | 文件差异 `{items|changes:[{path,status,additions,deletions,diff}]}` |

导入三种方式：

```bash
# 1) 原始 ZIP 字节
curl -X POST http://127.0.0.1:8765/api/v3/projects/$PID/skill-versions \
  -H 'Content-Type: application/zip' --data-binary @skill.zip

# 2) multipart 表单（前端使用）
curl -X POST http://127.0.0.1:8765/api/v3/projects/$PID/skill-versions \
  -F file=@skill.zip -F status=draft -F message=my-draft

# 3) 文件夹 JSON
curl -X POST http://127.0.0.1:8765/api/v3/projects/$PID/skill-versions \
  -H 'Content-Type: application/json' \
  -d '{"files":[{"path":"SKILL.md","content":"# …"}],"status":"draft","message":"draft"}'
```

校验失败返回 422，`details` 中逐项给出错误码：`missing_main`、`manifest_generated`、
`missing_reference`、`duplicate_skill_id`、`duplicate_path`、`path_traversal`、
`absolute_path`、`symlink`、`encoding_error`、`bad_manifest`、`too_large`。

## 4. 执行环境

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/projects/{pid}/environments` | 版本列表（凭据字段脱敏） |
| POST | `/projects/{pid}/environments` | 创建草稿/发布版本 |
| GET | `/environments/{eid}` | 详情（凭据脱敏） |
| POST | `/environments/{eid}/publish` | `{set_current?:true}` |
| POST | `/environments/{eid}/activate` | `{expected_current_version?}` 设为当前（回滚） |
| POST | `/environments/test-connection` | `{environment_id?, target?, input?, model_config?, optimizer_config?, judge_config?, mode?}`；默认真实探测一次，`target` 为 `business`/`optimizer`/`judge`，`input` 最多 16000 字符；`mode=check` 仅做本地配置检查，不出网 |
| GET | `/environment-template` | 内置 Prompt + 示例模型/加载/接受配置 |

创建环境请求（可从 `/environment-template` 获取完整模板）：

```json
{
  "prompt": "你是使用 Skill 包完成用户任务的执行 Agent。…",
  "model_config": {
    "provider": "demo_scripted",
    "model": "demo-scripted-v1",
    "temperature": 0,
    "max_tokens": 768,
    "timeout_s": 30
  },
  "optimizer_config": {"provider": "heuristic", "model": "frozen-heuristic-v1"},
  "loader_config": {"max_steps": 8, "max_model_calls": 12, "max_context_chars": 60000, "context_overflow": "error"},
  "run_config": {"concurrency": 3, "max_model_calls": 12, "case_timeout_s": 120, "base_repeats": 1, "validation_repeats": 3},
  "scorer_config": {"version": "score-v1"},
  "acceptance_config": {"rules_version": "accept-v1", "validation_repeats": 3},
  "status": "published"
}
```

- 已发布版本只读；修改必须创建新版本（草稿→发布）。运行启动后环境/规则被快照冻结。

`POST /environments/test-connection` 示例：

```json
{"environment_id":"env_…","target":"business","input":"用一句话解释企业网关。"}
```

响应包含 `ok`、`performed`、`target`、`provider`、`model`、`latency_ms`、`message`、`error_code`、`response_preview`、`usage`、`finish_reason` 与 `response_truncated`；不会返回凭据或原始网关错误体。连接测试不执行 Skill、不调用业务工具、不创建运行。

以上为默认 `mode=probe` 的响应字段；探测失败可能仍返回 HTTP 200，应检查 `ok` 和 `error_code`。
`performed=false` 表示未发起网络探测。`mode=check` 返回本地配置检查结果，不保证相同字段或真实连通性。
默认 `target=business`；切换为 `optimizer` 时从同一环境读取优化模型配置。
临时覆盖所选模型可传 `model_config`，同 provider 的未更改凭据由服务端继承；不保存配置。
空白 `input` 使用“只回复 OK”，非空文本原样发送；最多 64 输出 Token、无自动重试。详见 [连接测试](CONNECTION_TEST.md)。

## 5. 单案例试跑

`POST /projects/{pid}/debug-cases` 与 `/runs/{rid}/debug-cases` 默认返回完整 JSON。请求头增加 `Accept: application/x-ndjson` 时，接口改为逐行返回：

```json
{"type":"event","event":{"seq":1,"type":"model_call_started", "payload":{}}}
{"type":"event","event":{"seq":2,"type":"tool_call_finished", "payload":{}}}
{"type":"result","result":{"trial":true,"events":[]}}
```

事件来自同一次真实执行，可能包括模型调用、技能读取、业务工具、评分模型和评分完成；不会发送隐藏推理或凭据。正常结束时只发送一次终态，类型为 `result` 或 `error`；每 15 秒无业务事件时发送 `{"type":"heartbeat"}`。断流且未收到终态必须视为未完成，不能自动重试。客户端断开时服务会设置取消标记，执行器在下一个可取消检查点停止。默认 JSON 行为和试跑的数据隔离不变。

## 6. 数据集

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/projects/{pid}/datasets` | 版本列表 |
| POST | `/datasets/preflight` | 只校验不落库；返回统计/错误/警告/划分/覆盖 |
| POST | `/projects/{pid}/datasets` | 发布数据集版本（有错误需 `exclude_invalid=true` 明确剔除） |
| GET | `/datasets/{did}` | 详情 + split_counts + coverage |
| GET | `/datasets/{did}/cases?split=&search=&tag=&limit=&offset=` | 案例列表 |
| GET | `/datasets/{did}/cases/{cid}` | 案例详情（expected/fixtures；工具调用可带 `arguments_path`） |
| GET | `/datasets/{did}/coverage` | 子技能/工具/动作/多路线覆盖 |
| POST | `/datasets/{did}/draft` | 从已发布版本复制编辑草稿，不改变项目当前指针 |
| PUT | `/datasets/{did}/cases/{cid}` | 仅 draft 可改；`{case, expected_hash}` 原子保存单个案例 |
| POST | `/datasets/{did}/publish` | draft 发布并可设为当前；需 `expected_hash` 与 `expected_current_version`，冲突 409、校验失败 422；已发布版本仍兼容无 guard 的“设为当前” |
| POST | `/datasets/{did}/activate` | `{expected_current_version?}` 设为当前 |
| GET | `/datasets/{did}/export` | 同结构 JSONL 下载 |

在线修订时，先创建草稿（201 返回含 `id`、`hash` 的数据集详情），再 GET 草稿案例详情，修改完整案例后保存。
PUT 不是局部字段补丁：需携带 `case_id`、`query`、`context`、`expected`、`fixtures`、`split`、`group_id`、`tags`，且不可更名 `case_id`。
不要使用列表摘要代替完整案例，否则可能丢失参数与模拟返回。保存响应为 `{dataset, case}`，下一次保存/发布使用返回的新 `dataset.hash`。

发布草稿请求：

```json
{
  "expected_hash": "草稿当前 hash",
  "expected_current_version": "项目当前 current_dataset_id",
  "set_current": true
}
```

`expected_current_version` 是发布前的项目当前数据集 ID，并非草稿 ID；当前无数据集时显式传 `null`。
草稿保存只改变草稿，发布并设为当前才切换项目指针；hash/指针冲突返回 409 并回滚，非法案例或缺 guard 返回 422。
同一非空 group 不可跨 split，编辑不会静默重分配其他案例。旧版本与运行结果保留，新版本需重跑基线。
完整流程与边界见 [数据集在线修订](DATASET_EDITING.md)。

预检请求：

```json
{
  "filename": "dataset.jsonl",
  "content": "{...JSONL 文本...}",
  "split_config": {"optimize": 0.7, "validation": 0.2, "final_test": 0.1},
  "seed": 42,
  "project_id": "prj_…"
}
```

预检响应：

```json
{
  "ok": false,
  "format": "jsonl",
  "stats": {"total": 45, "valid": 44, "invalid": 1, "splits": {"optimize": 33, "validation": 7, "final_test": 4}, "coverage": {}},
  "errors": [{"line": 7, "case_id": "case_…", "code": "unknown_skill_id", "message": "未知 skill_id: task_x", "field_path": "expected.routes[0][0]"}],
  "warnings": [{"line": 9, "code": "duplicate_query", "message": "query 与第 3 行案例重复"}],
  "coverage": {"total": 45, "split_counts": {}, "skills": {}, "tools": {}, "multi_route_cases": 1, "single_route_cases": 44, "actions": {"tool_call": 40, "clarify": 3, "no_action": 2}, "clarify_cases": 3, "no_action_cases": 2},
  "case_summaries": [{"case_id": "opt_001", "query": "…", "split": "optimize", "group_id": "g_status_query", "tags": ["进行中的任务"]}]
}
```

## 7. 运行

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/runs` | 创建运行；支持 `Idempotency-Key`；立即返回 202 + 运行对象。`mode` 为 `evaluate`/`baseline`/`optimize`/`validate`/`final_test`；`validate` 必须带 `source_run_id`。 |
| GET | `/runs?project_id=&status=&limit=&offset=` | 运行列表 |
| GET | `/runs/{rid}` | 运行详情（状态/阶段/进度/指标/接受规则/候选/成本/issue 摘要） |
| POST | `/runs/{rid}/cancel` | 取消；queued 直接 cancelled，running 先 cancel_requested |
| POST | `/runs/{rid}/apply` | `{expected_current_version, candidate_id?}` 应用候选；按当前版本 CAS 与候选来源链校验，冲突 409 |
| GET | `/runs/{rid}/cases?phase=&only_failed=&search=&changed=&limit=&offset=` | 案例对比（默认聚合基线/候选与验证集配对） |
| GET | `/runs/{rid}/cases/{cid}` | 单案例详情（`baseline`/`candidate` 实际结果 + 事件） |
| GET | `/runs/{rid}/diff` | 候选文件差异、诊断、假设、检查 |
| GET | `/runs/{rid}/issues` | 问题摘要（warning/error 且带 issue 的事件） |
| GET | `/runs/{rid}/events` | SSE 事件流，`id: <seq>`；支持 `Last-Event-ID`/`?after_seq=` |
| GET | `/runs/{rid}/event-history?after_seq=&limit=&offset=&level=&type=&stage=&case_id=&errors_only=` | 历史事件回放/筛选 |
| GET | `/runs/{rid}/trace-artifacts/{aid}` | 按需加载大内容 `{artifact, content}` |
| GET | `/runs/{rid}/events/{seq}/model-output` | 查看指定模型完成事件的脱敏正文/工具参数；新记录 `source=captured`，旧记录按实际保存结果返回 `legacy_case_result`，不可恢复时为 `unavailable` |
| GET | `/runs/{rid}/report.json` | 评测报告 JSON 下载 |
| GET | `/runs/{rid}/report.csv` | 评测报告 CSV 下载 |
| GET | `/runs/{rid}/analysis` | 当前分析模型摘要、最新诊断附件 `latest`、可复制的 `evidence_markdown`；不调用模型 |
| POST | `/runs/{rid}/analysis` | `{question?: string}`（最多 2000 字）；使用当前发布环境的优化模型生成并保存只读诊断报告 |
| POST | `/runs/{rid}/debug-cases` 或 `/projects/{pid}/debug-cases` | 试跑：立即执行一个案例并按环境评分规则评分（含评分模型），同步返回；不写入运行、案例结果与指标。见下方说明 |

`event-history` 与 SSE 中的 `fixture_miss` 事件可附加 `display_issue`，保留原始 `payload`。运行问题摘要使用展示文案，并附 `comparison_rows` / `comparison_note`；每行包含 `fixture_index`、`field_path`、`actual`、`condition`，能可靠定位案例声明时增加 `expected`（均为脱敏展示文本）。此信息从运行绑定版本读取，仅供排查，不重算评分、不参与模型执行或优化。

AI 分析协议为 `run-analysis-v1`。POST 在线程池执行，单次模型请求至多 120 秒，不自动重试或规则回退；保留优化配置的流式开关。运行未结束或已有分析请求时返回 409，缺少真实模型配置或输入非法时 422。模型调用失败返回 HTTP 200、`status=failed` 与脱敏 `error` 和诊断材料；成功为 `status=completed`。响应含 `artifact_id`、`metadata`、`analysis`、`markdown`，GET 的 `latest` 结构相同。

每次 POST 新建 `kind=run_analysis` 附件，包含适配器报告的模型名、分析环境版本/hash、用量、证据 hash 和事件截止序号。流式适配器可能返回配置模型名，不能据此确认供应方内部路由。GET 返回最新附件，旧附件仍可按 ID 读取；不更新原运行事件、指标、决策或 Skill。日常案例最多采样 8 条，失败优先；事件优先前 60 条警告/错误及最后 60 条，总证据至多 60000 字符，`coverage` 说明遗漏与截断。留出运行不发送案例与事件载荷；诊断不进入优化器补丁流程。

试跑请求体（`POST …/debug-cases`）：

- `case_id`：数据集中的案例；`dataset_id` 可选（默认项目当前数据集，或该运行的数据集）。
- `variant`：仅在 `/runs/{rid}/…` 下有效，`baseline`（默认）用该运行的基线 Skill，`candidate` 用候选 Skill；运行没有候选时返回 422。
- `skill_version_id` / `environment_id`：可选，显式指定版本。
- `query`：不给 `case_id` 时试跑一句自由输入；没有预期，因此不评分（`scored=false`），业务工具统一返回占位结果 `{"ok": true}`。

返回 `{case_id, query, split, expected, scored, result, skill_version, environment, events, judge_usage, trial: true, note}`，
`result` 与 `/runs/{rid}/cases` 中单侧结果同形（`passed/status/diagnostics/judgments/tool_calls/…`，另有 `error_message`、`raw_output`）。
执行在线程池中进行，单次最长为环境的 `case_timeout_s`。

创建运行：

```json
{
  "project_id": "prj_…",
  "mode": "evaluate | baseline | optimize | validate | final_test",
  "reuse_baseline": true,
  "source_run_id": null,
  "evaluation_run_id": null,
  "parent_run_id": null,
  "skill_version_id": null
}
```

- `evaluate` 与 `baseline` 只运行日常优化集；`evaluate` 是普通评测，`baseline` 不复制缓存。
  已完成的 `evaluate`/`baseline` 运行都可作为后续 optimize 的基线缓存来源，复制发生在 optimize。
- `mode=optimize` 在成功生成候选并重跑候选后完成，返回 `decision=null`，不在此阶段 accept/reject；
  没有失败证据、候选未生成或静态检查失败时可完成为 `decision=inconclusive`，平台异常则为 `failed`。
  正式验收必须另建 `mode=validate`，携带 `source_run_id`。
- `validate` 只接受本项目已完成、尚无 decision 的 optimize 来源；服务端校验 root 基线、候选来源、环境、
  静态检查、版本完整性及 validation 集独立性，然后在独立 validation 集上按冻结配置的重复次数配对执行
  `accept-v1`。默认重复 3 次，可配置更多；若配置低于 3，验收证据不足并为 `inconclusive`。
  validation 集不得与来源链的 optimize/final_test 案例在 case_id、规范化 query 或 group_id 上重叠。
- `evaluation_run_id` 仅用于 `optimize`，绑定本项目已完成的 `evaluate`/`baseline` 记录及当前 Skill；不能与 `source_run_id` 同用。明确来源时不会替换为其他较新的结果。
- `source_run_id` 用于继续优化时，优先使用该来源的 `candidate` 复测结果；新运行的 root 基线仍指向迭代链起点。环境更新后可继续优化，由执行条件决定是否补测；独立验收的来源环境检查保持严格。
- `reuse_baseline` 默认 true。未指定来源时，从本项目已完成的日常评测/优化运行中选择一个兼容来源的 baseline 结果，不混合多个运行；不会读取 validation/final_test 结果。
- 兼容条件包括 Skill、日常案例内容及 expected/fixtures、Prompt、业务模型、加载/执行/评分配置、重复次数与执行协议。优化模型、日志设置、仅留出数据变化不触发日常重测；原运行 fingerprint 和版本快照不改写。
- `snapshot.evaluation_reuse` 记录来源运行/phase、Skill、原因、复用/待执行数量及 `(case_id, repeat_index)` 清单。仅 pass/fail 且无基础设施错误的唯一结果可复用；缺失、重复、infra 等结果补测。全部复用时 `baseline_cache_hit=true`，部分复用仍为 false，`baseline_run_id` 保留实际来源。
- 优化使用 `results_preparation` 阶段。补测后仍有缺失或基础设施错误，以 `evaluation_incomplete` 结束，不调用优化器。复用结果保留历史证据和用量，但历史用量不重复计入新运行指标成本；候选仍重跑完整日常集。
- 返回 202 与 `run_view`；后台 Worker 异步执行。

运行对象关键字段（以下示例为成功 `optimize` 完成、尚待验收；`validate` 完成后才会填充最终 decision）：

```json
{
  "id": "run_…", "mode": "optimize", "status": "completed", "stage": "decision",
  "stage_progress": {"stage": "decision", "completed": 33, "total": 33, "current_case_ids": [], "message": "候选已完成测试集重跑，待独立验收", "last_activity_at": "…"},
  "decision": null, "decision_reason": "候选已完成测试集重跑，待独立验收",
  "snapshot": {"skill_version": {"id": "sv_…", "label": "sv_…"}, "environment": {"id": "env_…", "version_seq": 1}, "dataset": {"id": "ds_…", "version_seq": 1, "case_count": 45}, "scorer_version": "score-v1", "fingerprint": "…", "base_repeats": 1, "validation_repeats": 3},
  "acceptance": {"rules_version": "accept-v1", "validation_repeats": 3, "frozen": {"fingerprint": "…", "scorer_version": "score-v1", "base_repeats": 1}, "decision": null, "reason": "候选已完成测试集重跑，待独立验收", "rules": [], "applied_at": null},
  "metrics": {
    "baseline": {"routing": {}, "tool_args": {}, "joint": {}, "diagnostics": {}, "execution": {}},
    "candidate": {"routing": {}, "tool_args": {}, "joint": {}, "diagnostics": {}, "execution": {}},
    "delta": {"routing": 0.1667, "tool_args": 0.3667, "joint": 0.3939},
    "validation": null,
    "final": null,
    "temporary": false
  },
  "execution": {"planned": 33, "completed": 33, "valid": 33, "infra_errors": 0, "duration_ms": 388, "tokens": 126262, "cost": null, "fixture_misses": 0, "capability_failures": 0},
  "cost": {"tokens": 126262, "total_tokens": 126262, "amount": null, "duration_ms": 388},
  "candidate": {"id": "cand_…", "skill_version_id": "sv_…", "changed_files": ["SKILL.md"], "patch": {"summary": "…", "edits": []}, "diagnosis": {}, "hypotheses": [], "expected_effect": "…", "risk_cases": [], "checks": {"ok": true, "checks": []}},
  "case_stats": {"planned": 33, "completed": 33, "valid": 33, "errors": 0},
  "can_apply": false
}
```

取消/应用示例：

```bash
curl -X POST http://127.0.0.1:8765/api/v3/runs/$RID/cancel
curl -X POST http://127.0.0.1:8765/api/v3/runs/$RID/apply \
  -H 'Content-Type: application/json' \
  -d "{\"expected_current_version\":\"$BASELINE_SV\"}"
```

应用冲突返回 409：

```json
{"error": {"code": "version_conflict", "message": "项目当前版本已不是本轮基线版本，请重新比较", "details": {"current_version": "sv_new", "baseline_version": "sv_base"}}}
```

## 8. SSE 与事件

`GET /runs/{rid}/events` 返回：

```text
id: 15
data: {"id": 1234, "run_id": "run_…", "seq": 15, "ts": "2026-10-24T08:45:30.123Z", "stage": "candidate_generation", "case_id": null, "split": null, "variant": null, "repeat_index": null, "attempt": null, "call_id": "opt_…", "type": "optimizer_not_configured", "level": "error", "payload": {"…": "…", "issue": {"error_code": "optimizer_not_configured", "title": "优化器未配置", "message": "…", "impact": "…", "next_action": "…", "cause_event_seq": null}}}

: keep-alive
```

- 支持 `Last-Event-ID` 头或 `?after_seq=`；终态运行仍可回放历史。
- 前端按 `run_id + seq` 去重；断线重连后用 `event-history` 补齐缺口。
- 事件字段：`run_id, seq, ts, stage, case_id, split, variant, repeat_index, attempt, call_id, type, level, payload`。
- `level ∈ debug|info|warning|error`；warning/error 且 payload 含 `issue` 时进入问题摘要。
- 事件类型/阶段注册表见 `app/event_types.py`；运行记录 UX 对应需求 §44–§54。

## 9. 报告与导出

- `GET /runs/{rid}/report.json`：版本指纹、评分口径、前后指标、逐案例差异、接受规则与结论、
  候选补丁与文件差异、成本、问题摘要；不包含凭据与最终测试答案。
- `GET /runs/{rid}/report.csv`：逐案例对比（UTF-8 BOM，Excel 可直接打开）。
- `GET /skill-versions/{sid}/export`：Skill 包 ZIP（含 manifest），不含密钥/验证答案/执行器私有配置。
- `GET /datasets/{did}/export`：与导入相同结构的 JSONL。
