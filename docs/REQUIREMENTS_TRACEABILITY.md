# 需求追溯矩阵

需求来源：根目录 `Skill优化平台需求文档_v1.docx`（§1–§54）。下表给出章节到实现、测试与文档的映射；
文末列出实现口径与参考实现的差异说明。

## 1. 功能章节

| 需求 | 实现位置 | 验证 |
| --- | --- | --- |
| §1 产品目标（导入/固定 Prompt/基线/定位/补丁/复测/可审查导出回滚） | `app/single_round.py`、`app/optimizer.py`、`app/api_v3.py`、`web/` | `tests/test_a01…test_a20` |
| §2 已确定需求、默认方案、可配置项、后续扩展 | README「功能范围」；未实现项在 §13 边界说明 | 文档审查 |
| §3 首期交付边界（真实执行、禁止硬编码评分） | 全部后端模块；评分器 `app/evaluation.py` 为真实计算 | A04/A05/A06 |
| §5 用户完成一次优化的路径 | `web/js/views/*`、API 流程 | `docs/DEMO.md` |
| §6 信息架构（7 个页面） | `web/js/app.js` 路由与 8 个视图模块 | 界面联调 |
| §7 界面风格（深色、桌面优先、等宽代码、留白） | `web/css/app.css`、`web/index.html` | `docs/images/*`、界面联调 |
| §8 交互通则（单一主操作、离页提示、字段错误、空态/失败态） | `web/js/views/*`、`components.js` | 界面联调 |
| §9 Skill 包管理（ZIP/文件夹、manifest、安全检查、Markdown 白名单、草稿/发布/不可变、导出） | `app/skill_package.py`、`app/services.py`、`app/persistence.py` | A01 |
| §10 执行环境配置（Prompt/业务模型/优化模型/工具协议/加载/运行参数/预算） | `app/services.py`、`web/js/views/environment.js` | A02、A10 |
| §11 冻结与隔离（快照、执行器不可见 expected、优化器不可见验证集、凭据、可复现性） | `app/single_round.py`、`app/execution.py`、`app/services.py`、`app/reports.py` | A02、A20、A13 |
| §12 数据导入与版本（JSONL/JSON、预检、稳定 ID、导出、发布不可变、复制草稿修订） | `app/dataset.py`、`app/services.py`、`app/persistence.py`、`app/api_v3.py` | A03、`test_dataset_editing.py`、`test_dataset_editing_api.py` |
| §13 字段与扩展（context/fixtures/action/clarify/no_action/missing_fields） | `app/dataset.py`、`app/execution.py`、`app/fixtures.py`、`app/evaluation.py` | A03、A04、A10 |
| §14 划分与覆盖（group 70/20/10、固定种子、覆盖提示、未覆盖不算满分） | `app/dataset.py`、`app/services.py` | A03、界面数据集页 |
| §15 指标与有效案例（R/A/J、诊断、执行与成本、infra/能力失败） | `app/evaluation.py`、`app/single_round.py` | A04/A05/A10 |
| §16 参数比较（严格 JSON、schema 先校验、显式版本化规则） | `app/evaluation.py` | A04 |
| §17 候选接受规则（R/A/J、critical、infra、证据不足、3 次配对） | `app/acceptance.py`、`app/single_round.py` | A07、A09 |
| §18 执行阶段（预检→基线→错误分析→候选→静态检查→复测→结论） | `app/single_round.py` | A06、A08、A09 |
| §19 优化器契约（输入范围、diagnosis/hypotheses/patch、局部修改、禁止硬编码） | `app/optimizer.py`、`app/patch.py` | A06 |
| §20 单轮与状态（单候选、运行状态、候选应用冲突） | `app/single_round.py`、`app/persistence.py`、`app/services.py` | A08、A09 |
| §21–§27 界面规格（概览/数据集/调试/运行详情/案例差异/结论/导出） | `web/js/views/*`、`web/js/views/runLog.js` | 界面联调、`docs/DEMO.md` |
| §28 服务职责（前端/API/Worker/评分器/模拟器/文件存储/适配层） | 见 `docs/ARCHITECTURE.md` | 架构审查 |
| §29 最小数据模型 | `app/db.py`、`app/persistence.py` | 数据库 schema |
| §30 HTTP 接口 | `app/api_v3.py`、`docs/API.md` | A08/A09 |
| §31 固定业务 Prompt 模板 | `app/samples/system_prompt.md`、`/environment-template` | A01、A11 |
| §32 内部观测工具契约（select_skill/read_skill/finish、事件一致性） | `app/obs_tools.py`、`app/execution.py` | A11/A13 |
| §33 优化器指令要求（只读优化集、白名单、冻结协议） | `app/optimizer.py`、`app/patch.py`、`app/single_round.py` | A06 |
| §34 可靠性与运行边界（持久化、租约心跳、去重、重试、取消、隔离、脱敏） | `app/worker.py`、`app/persistence.py`、`app/execution.py`、`app/adapters/*` | A08、A10、A19、A20 |
| §35 首期验收 A01–A10 | `tests/test_a01…a10` | 见下表 |
| §36 交付与里程碑（可运行前后端、示例项目、测试命令） | 本仓库、`scripts/`、README | 全量测试 |
| §37–§40 网关/Python 接入（两种粒度、run_case 契约、WebSocket 事件聚合） | `app/adapters/{enterprise_ws,openai_http,stream_events,text_parse}.py`、`app/execution.py`；流程级粒度已按决定移除，见差异说明 8 | A11–A13 |
| §41 纯文本与原生调用（result_source、能力声明、A 未评估） | `app/adapters/text_parse.py`、`app/execution.py`、`app/evaluation.py` | A13 |
| §42 界面与运行配置（网关地址/超时/心跳/状态文案） | `web/js/views/environment.js`、`app/adapters/enterprise_ws.py` | 界面联调、A12 |
| §43 补充验收 A11–A14 | `tests/test_a11…a14` | 见下表 |
| §44–§46 运行记录布局/展开/状态文案 | `web/js/views/runLog.js`、`web/css/app.css` | 界面联调（`docs/DEMO.md` §3.3） |
| §47 各阶段记录要求 | 各模块事件上报（`app/event_types.py`、`run_events.py`） | A15（人工）、事件测试 |
| §48 失败定位示例 | `web/js/views/runLog.js` 问题摘要/定位/详情 | 界面联调 |
| §49 禁止静默吞优化器异常（optimizer_*、fallback_*） | `app/optimizer.py` | A06 |
| §50 优化器输入可见性（失败数/文件/字符数/截断/策略） | `app/optimizer.py`、运行记录 | A06 |
| §51 事件与持久化（字段、seq 递增、trace-artifacts、脱敏） | `app/persistence.py`、`app/run_events.py` | A08、A20 |
| §52 接口与订阅（event-history/SSE/Last-Event-ID/独立回放） | `app/api_v3.py`、`web/js/views/runLog.js` | A08、界面联调 |
| §53 修改位置与交付顺序 | 对应模块见 `docs/CONTRACTS.md` | 代码审查 |
| §54 增量验收 A15–A20 | 见下表 | A15–A18 人工联调；A19/A20 自动化 |

## 2. 验收矩阵

| 编号 | 自动化用例 | 结果 |
| --- | --- | --- |
| A01 | `tests/test_a01_skill_package.py` | 通过 |
| A02 | `tests/test_a02_environment.py` | 通过 |
| A03 | `tests/test_a03_dataset.py` | 通过 |
| A04 | `tests/test_a04_scoring.py` | 通过 |
| A05 | `tests/test_a04_scoring.py`、`test_a05_metrics.py` | 通过 |
| A06 | `tests/test_a06_optimizer.py` | 通过 |
| A07 | `tests/test_a07_acceptance.py` | 通过 |
| A08 | `tests/test_a08_run_lifecycle.py` | 通过 |
| A09 | `tests/test_a09_apply_rollback.py` | 通过 |
| A10 | `tests/test_a10_runtime_edges.py` | 通过 |
| A11 | `tests/test_a11_ws_gateway.py` | 通过（本地假 WS 网关） |
| A12 | `tests/test_a12_stream_assembly.py` | 通过 |
| A13 | `tests/test_a13_capabilities.py` | 通过 |
| A14 | `tests/test_a14_cache.py`、`test_a13_capabilities.py` | 通过 |
| A15 | 运行记录定位/原因/影响/后续处理 | 界面人工联调（`docs/VERIFICATION.md`） |
| A16 | 定位清筛选、展开/键盘、大内容折叠 | 界面人工联调 |
| A17 | 并发案例不串线、两个页面收到完整事件 | 界面人工联调（多标签页） |
| A18 | 刷新/断线续接/终态 30 秒后回放、不丢不重 | 界面人工联调 |
| A19 | `tests/test_a19_cancel_restart.py` | 通过 |
| A20 | `tests/test_a20_security.py` | 通过 |

最近全量验收（2026-10-26）：`.venv/bin/python -m pytest -q` → **413 passed**，1 个已有 Starlette/httpx 弃用提示。
覆盖修图语流扩展、最小界面改版、HTTP 网关、模型输出查看、自定义输入连接测试与数据集在线修订。
各批次原始结果见 [VERIFICATION.md](VERIFICATION.md)。

## 3. 实现口径与差异说明

1. **演示模型**：示例环境使用 `demo_scripted`（确定性规则模拟器），用于离线演示与自动化测试；
   它不是真实模型，成绩不代表真实效果。真实接入通过 `openai_http` / `enterprise_ws`，
   不同适配器成绩不可混用（§41/§42）。真实企业网关联调需要实际地址与凭据，仓库内用本地假
   WS 服务验证协议与事件聚合。
2. **进程重启续跑**：需求 §51 提到“自动续跑不属于本次增量范围”；本实现启动时会回收过期
   租约并把未完成运行置回 `queued` 续跑，案例结果按唯一键去重、保留已完成部分。这是额外能力，
   不改变事件/结果口径；如不希望续跑，可在启动后取消对应运行。
3. **事件存储**：参考实现提到 `runs/{rid}/events.jsonl`；本实现把事件持久化在 SQLite
   `events` 表并通过 `/events`（SSE）与 `/event-history` 回放，接口行为等价，且便于筛选/分页。
4. **运行记录增量验收 A15–A18**：仓库未引入 Playwright 作为测试依赖，采用真实浏览器人工联调
   + 截图记录（`docs/VERIFICATION.md`、`docs/images/`）；后端与之相关的接口由 A08/A20 自动化覆盖。
5. **最终测试集**：`final_test` 运行仅用于阶段验收，不参与候选选择；默认基础重复 1 次，
   判定与候选选择完全隔离。
6. **单用户本地部署**：无内置认证/TLS，需通过反向代理加固；多实例部署需迁移共享数据库与
   分布式任务队列（README §13、DEPLOYMENT §7）。
7. **模型增量事件**：适配器支持增量事件（`emit_deltas` 可选），流水线默认只持久化关键事件与
   聚合结果，避免日志膨胀；运行记录仍满足 §44–§50 的定位与详情要求。
8. **接入方式收敛（2026-10-26）**：按产品决定，执行环境只提供「标准 API」与「WebSocket 网关」两种
   模型接入（优化模型另可选规则修复）。§37 中的流程级 Python 接入（`flow` / `run_case`）已从前后端
   移除，与需求原文的“两种粒度”存在差异；`provider: flow` 返回 422。A13「仅分发能力时 A 未评估」
   由纯文本路由适配器用例覆盖，不依赖流程级接入。离线演示模型保留给示例项目与自动化测试，界面不再提供选择。
8. **多候选/多轮迭代/优化器自我改进/多人权限/真实生产工具执行**：按 §2「后续扩展」范围未实现。
9. **A 评分的参数子树扩展**：在需求 §15 的完整工具参数比较之外，增加可选的
   `tool_calls[].arguments_path`（简化写法 `tool.args`），仅当数据集显式声明时生效；
   默认仍比较完整 `arguments`。当前修图示例完整校验方法与参数包装；子树比较作为显式选项继续兼容。
10. **第二个示例项目**：修图 Skill 已扩展为 12 类语流参数子技能，数据集覆盖口语、否定改口、
    澄清与最多三步有序任务，逐项比较 method/script/request/args；不改变 A01–A20 的通用验收口径。
