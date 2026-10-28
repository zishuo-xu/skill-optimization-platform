# 文档导航

当前产品是一套以 Prompt + Skill 为业务输入、以工具调用准确率为目标的评测与优化平台。文档按使用任务组织；设计提案和验证记录单独保留。

## 建议阅读顺序

首次使用：[项目首页](../README.md) → [核心概念](CONCEPTS.md) → [使用指南](USER_GUIDE.md) → [真实模型完整教程](REAL_MODEL_WALKTHROUGH.md)。

开发或排障：先读相关使用规则，再查[架构](ARCHITECTURE.md)、[API](API.md)、[模块契约](CONTRACTS.md)和[部署指南](DEPLOYMENT.md)。

## 当前使用指南

| 文档 | 回答的问题 |
| --- | --- |
| [核心概念](CONCEPTS.md) | 平台优化什么；执行循环与优化循环是什么；三类模型各做什么 |
| [使用指南](USER_GUIDE.md) | 如何创建项目、试跑、评测、优化、验收并应用 |
| [输入规范](INPUTS.md) | Prompt、Skill ZIP、query/context、expected、fixture 怎样写 |
| [日常测试与独立验收](DAILY_TEST_AND_ACCEPTANCE.md) | 各运行模式使用哪些数据，怎样防止评测泄漏 |
| [评分规则](SCORING.md) | R/A/J、finish、参数语义判断、通过/失败如何解释 |
| [数据集编辑](DATASET_EDITING.md) | 如何修改草稿、发布新版本，怎样处理并发冲突 |
| [示例导览](DEMO.md) | 内置离线示例与真实模型样例有什么区别 |
| [真实模型完整教程](REAL_MODEL_WALKTHROUGH.md) | 从可导入的输入资产重走完整闭环，历史证据能说明什么 |

## 执行与接入参考

| 文档 | 内容 |
| --- | --- |
| [Prompt 与 Skill 执行](PROMPT_SKILL_EXECUTION.md) | 通用 call_tool、工具规则来源、执行器输入边界 |
| [业务链路展示](BUSINESS_TRACE_VIEW.md) | 加载子技能、调用工具、内部协议详情与运行分组 |
| [模型输出查看](MODEL_OUTPUT_VISIBILITY.md) | 模型正文、工具参数、历史实际结果与缺失记录 |
| [模型连接测试](CONNECTION_TEST.md) | 业务/优化/评分模型连接检查与证据边界 |
| [标准 HTTP 接入](HTTP_GATEWAY_INTEGRATION.md) | URL、模型 ID、鉴权与流式响应 |
| [WebSocket 接入](WS_GATEWAY_INTEGRATION.md) | 网关报文、返回解析、超时和重试 |

## 开发与运维参考

AI 接手顺序：[AGENTS](../AGENTS.md) → [开发协作指南](../CONTRIBUTING.md) → [设计决策索引](decisions/README.md) → 对应模块与测试。

| 文档 | 内容 |
| --- | --- |
| [AI 开发入口](../AGENTS.md) | 阅读顺序、产品约束、代码定位和证据要求 |
| [开发协作指南](../CONTRIBUTING.md) | 环境、验证、真实模型边界、提交和推送 |
| [设计决策索引](decisions/README.md) | 已实现的长期决定、原因、限制与代码依据 |
| [架构](ARCHITECTURE.md) | 组件职责、数据模型、状态机、冻结与隔离 |
| [API](API.md) | REST/SSE 端点、请求、响应和错误 |
| [模块契约](CONTRACTS.md) | 案例、执行结果、评分、补丁和事件格式 |
| [部署](DEPLOYMENT.md) | 启动、配置、数据库升级、备份、Worker 与排障 |
| [内置示例资产](../app/samples/README.md) | seed 输入、修图合成协议、确定性示例边界 |
| [变更记录](../CHANGELOG.md) | 按演进记录功能修改；旧条目不作为现行操作说明 |

## 历史设计与验证证据

以下文件记录某个阶段的设计、测试数量或运行结果，可能含已替换的界面和流程。当前用法以上述指南为准，不能把历史测试数当作本次执行结果，也不能把规划项当作已实现能力。

| 文档 | 记录范围 |
| --- | --- |
| [引导示例用户验收](GUIDE_USER_JOURNEY_2026-10-28.md) | 从空配置到真实模型基线、优化、独立验收、应用的页面路径与修复 |
| [验证记录](VERIFICATION.md) | 各批次测试、浏览器与真实 API 证据 |
| [需求追溯](REQUIREMENTS_TRACEABILITY.md) | 原始需求 §1–§54 与实现差异 |
| [RSI 平台评审](PLATFORM_REVIEW_RSI_2026-10-25.md) | 评审发现与改进建议 |
| [最小界面改版](MINIMAL_REDESIGN.md) | 早期页面收敛决策与验收 |
| [RSI 页面简化](RSI_UI_SIMPLIFICATION.md) | 工作台与配置分层演进 |
| [用户流程审查](USER_FLOW_AUDIT_2026-10-27.md) | 用户操作检查与修正证据 |
| [渐进加载补齐](PROGRESSIVE_LOOP_2026-10-27.md) | Agent loop 的执行约束演进 |
| [选择时自动加载](SELECT_LOAD.md) | select_skill 合并加载正文的改动 |
| [试跑实时过程](TRIAL_STREAMING.md) | 试跑事件增量展示的改动 |
| [修图示例扩展](IMAGE_SKILL_EXPANSION.md) | 13 类子技能、参数覆盖与验证边界 |
| [企业网关专项](ENTERPRISE_GATEWAY_REVIEW.md) | 网关修复与本地验证证据 |

## 文档维护规则

- 首页只放目标、启动和入口；输入格式统一维护在 INPUTS，避免多份长示例互相冲突。
- 运行模式与隔离以 DAILY_TEST_AND_ACCEPTANCE 为使用说明；评分以 SCORING 为说明；接口细节集中在 API/CONTRACTS，并与代码一起更新。
- 当前指南不写固定“最新测试数”。验证记录标明时点、执行方式、是否真实模型和是否真实业务工具。
- 示例必须区分人工构造缺陷、模型补丁、规则回退与实际业务副作用；运行 ID 只是所在环境的证据标识。
- 改动后检查本地链接、示例 JSON/manifest/数据集预检和关键行为说明；不要上传凭据或整个本地运行数据库。
