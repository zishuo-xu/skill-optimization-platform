# 验收执行记录（A01–A20 + 界面联调）

最新代码验收基线：2026-10-26 全量 **395 passed**，1 个已有 Starlette/httpx 弃用提示。
下文按批次保留当时的测试数量与验收范围；早期数字不是当前总数。

## 数据集在线修订验收

- 全量 pytest：395 passed，1 个已有弃用提示；新增 9 项数据修订回归。
- API 和真实隔离浏览器验证草稿编辑/保存/重载/发布、连续两轮发布、未保存关闭保护，以及历史数据不变、基线失效与并发冲突回滚。
- 记录见 [DATASET_EDITING.md](DATASET_EDITING.md)。

## 真实连接测试验收

- 全量 pytest：386 passed，1 个已有弃用提示；新增 22 项 probe 与集成回归。
- 已发布/草稿、业务/优化两个目标、真实本地 HTTP/WS、鉴权/超时/格式异常、事件循环响应和浏览器交互均已验证。
- 真实内网/付费模型未自动探测，详见 [CONNECTION_TEST.md](CONNECTION_TEST.md)。

## 模型输出可见性验收

- 全量 pytest：364 passed，1 个已有弃用提示；新增 18 项回归。
- 普通文本、tool-only、超长 artifact、脱敏、旧案例结果、API 隔离与浏览器按需查看均已验证；现有真实记录只读检查，无新增真实模型调用。
- 使用与证据见 [MODEL_OUTPUT_VISIBILITY.md](MODEL_OUTPUT_VISIBILITY.md)。

## 2026-10-25 标准 API 与企业 HTTP 网关验收

- 全量 pytest：346 passed，1 个已有弃用提示；本批新增 27 项网关/配置回归。
- 本地真实 HTTP/WS socket 验证流结束、usage、延迟、鉴权、扩展 Body/Headers、deadline、重试边界；隔离浏览器验证完整 URL 与模板/凭据保存发布。
- 真实内网网关尚未验收；详见 [专项记录](ENTERPRISE_GATEWAY_REVIEW.md) 和 [内网接入指南](HTTP_GATEWAY_INTEGRATION.md)。

## 2026-10-25 最小界面改版验收

- 全量 pytest：319 passed；新增 13 项回归，保留 1 个已有弃用警告。
- 结果页首屏操作、验证集统计口径、过期候选禁用、应用与恢复、凭据保护、空历史后 SSE、终态全局同步与 390px 布局已做隔离浏览器验证。
- 原数据全 13 表与备份一致；本批没有调用付费模型。
- 证据、边界与截图路径见 [MINIMAL_REDESIGN.md](MINIMAL_REDESIGN.md#已完成验收2026-10-25)。

## 2026-10-25 修图语流扩展验收

- 主技能分发、12 个参数子技能、62 条案例（24 优化 / 21 验证 / 17 最终测试）。
- 全量 `.venv/bin/python -m pytest -q`：306 passed，1 个现有 Starlette/httpx 弃用警告。
- 修图案例逐条经过 execution 与完整协议评分；另验证三步渐进加载、缺参零业务调用、错误 method/script 拒绝、旧 v1 执行兼容。
- 已配置真实业务模型（openai_http / deepseek-flash）抽查 16 条独立编写的语流：首轮 15/16，补充超限澄清的 finish 选择一致性指令后 16/16；这些样本已用于开发验证，不作为全新留出集成绩。
- 真实模型抽查不使用 expected 构造模型请求；业务工具仍使用 fixtures，没有验收真实图片处理结果。
- 浏览器确认已有项目可查看 12 个子技能及新增 reference；新版本发布保留了 3 条历史运行、原模型/优化器/预算配置。
- `--refresh-image` 连续调用第二次无新增版本；ZIP 14 文件/12 reference 可导入；Skill Creator 校验通过；数据生成器重复运行内容一致。
- 设计见 [IMAGE_SKILL_EXPANSION.md](IMAGE_SKILL_EXPANSION.md)，抽查与截图在 `output/playwright/`。

## 1. 自动化验收

```bash
bash scripts/setup.sh
.venv/bin/python -m pytest -q          # 395 passed
```

用例按需求编号组织在 `tests/`：

| 编号 | 覆盖内容 | 用例文件 |
| --- | --- | --- |
| A01 | 21 子技能导入/映射、坏引用与路径穿越拒绝、导出再导入内容还原、草稿编辑与发布 | `test_a01_skill_package.py` |
| A02 | Prompt 发布后只读、环境变更生成新版本、基线缓存失效、优化器不能改环境/manifest | `test_a02_environment.py` |
| A03 | JSONL/JSON 导入校验（行号/错误码/字段路径）、固定种子划分、覆盖统计；执行器不接收 expected | `test_a03_dataset.py`、`test_a20_security.py` |
| A04 | 漏选/多选/错序/错工具/缺参/类型错/额外参的确定性计分 | `test_a04_scoring.py` |
| A05 | 分发与入参独立计分、联合结果正确 | `test_a04_scoring.py`、`test_a05_metrics.py` |
| A06 | 单轮优化：模型/回退补丁、静态检查、前后报告；失败可正常拒绝 | `test_a06_optimizer.py` |
| A07 | R/A/J 回归、critical 回归、基础设施/包完整性错误、验证不完整 → 拒绝/证据不足 | `test_a07_acceptance.py` |
| A08 | 幂等启动、事件严格递增与游标续接、取消排队运行、租约过期恢复、Worker 重跑不重复事件 | `test_a08_run_lifecycle.py` |
| A09 | 应用候选更新当前版本、版本冲突 409、回滚、导出恢复历史内容、最终测试集隔离 | `test_a09_apply_rollback.py` |
| A10 | 澄清/无工具/fixture_miss、预算耗尽 → inconclusive、不可达网关 → 基础设施错误 | `test_a10_runtime_edges.py` |
| A11 | 真实 WebSocket 网关调用、messages 不含 expected/答案 | `test_a11_ws_gateway.py` |
| A12 | 拆帧/合帧、空 delta、尾部 usage、结束标记、中断、分片工具参数 | `test_a12_stream_assembly.py` |
| A13 | 仅分发能力时 A 未评估；能力声明与 result_source | `test_a13_capabilities.py` |
| A14 | 缓存复用条件；环境/适配器/数据集变化使缓存失效 | `test_a14_cache.py` |
| A19 | 取消/崩溃恢复后不存在永久执行中的步骤 | `test_a19_cancel_restart.py` |
| A20 | 凭据不出现在日志/报告/导出/接口；观测不改变评分 | `test_a20_security.py` |

## 2. 2026-10-24 界面联调（旧版 4 类修图，真实后端，非 mock）

环境：`.venv/bin/python -m app.seed --reset --with-optimize` + `./run.sh`（http://127.0.0.1:8765）。

已人工核对：

- 项目列表、概览（配置完整性、最近指标、最近运行、主按钮随状态变化、启动侧栏）；
- Skill 包（21 子技能文件树、文件正文、草稿/发布/当前版本标记）；
- 执行环境（业务 Prompt 默认折叠、只读态与创建草稿、模型/预算/接受条件分区、凭据脱敏）；
- 数据集（45 条任务管理 + 15 条修图案例、划分/覆盖统计、筛选、案例侧栏、导入预检弹窗走真实 preflight）；
- 修图示例（渐进式 4 子技能、`tool.args` 只比较子技能参数、基线缺陷 → 候选复测 R/A/J 100%）；
- 运行详情四标签页：结果概览（一句话结论 + 选对技能 R / 参数正确 A / 整体通过 J 当前值与原值 + 下一步按钮；
  案例结果数量可点击直达筛选、主要失败原因；完整指标、判定规则、成本收在「更多」）、
  案例对比（全部/修好了/变差了/仍然失败/一直通过筛选，每行一句话失败原因，详情为预期/基线/候选三列对照）、
  文件差异（变更文件统一 diff）、运行记录；
- 运行记录：问题摘要（仅显示真实问题，如优化器未配置）、逐项定位（自动清筛选→
  “已切换到全部记录”、展开并聚焦目标行）、全部/错误与警告筛选、案例范围、优化器事件开关、
  详情分层（原因/影响→请求与 messages→原始返回→评分差异→技术细节）、SSE 断线重连提示、
  终态历史回放、按 seq 去重、跟随暂停与“有 N 条新记录”；
- 状态文案：运行中阶段/活动案例/等待模型响应；reject 与 inconclusive 的区分；
- 写操作：从界面启动一次单轮优化（真实 Worker 执行，约 3 秒完成，decision=accept）；
  从界面“应用候选”成功切换当前版本指针并弹出“候选已应用”提示；
  `POST /runs/{id}/apply` 的 409 冲突、取消排队运行、版本回滚由自动化用例覆盖。

> 说明：浏览器界面联调为人工/脚本化抽查，仓库没有引入 Playwright 作为测试依赖；
> 当前后端回归包含 306 条 pytest 用例，前端静态资源由后端直接托管、无构建步骤。

## 3. 已知边界

- 本机演示环境使用确定性 `demo_scripted` 适配器与冻结启发式回退，不代表真实模型效果；
  接入真实企业网关需在执行环境配置 `enterprise_ws`（或 `openai_http`）并重新冻结环境版本。
- 企业网关联调需要真实网关地址与凭据；仓库内仅用本地假 WS 服务验证协议与聚合逻辑。
- 单用户本地部署；多实例部署需替换共享数据库与分布式任务队列（见 README §13）。
- 最终测试集只应在阶段验收时运行；一旦用于后续优化，应转为开发数据并补充新的留出集。
