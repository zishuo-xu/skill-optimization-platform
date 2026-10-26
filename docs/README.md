# 文档导航

| 文档 | 内容 |
| --- | --- |
| [README](../README.md) | 项目总览、快速开始、功能、架构摘要、评分口径、验收映射 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 组件边界、数据模型、状态机、事件模型、可靠性、设计取舍 |
| [API.md](API.md) | `/api/v3` REST/SSE 接口、示例请求、错误与状态码 |
| [CONNECTION_TEST.md](CONNECTION_TEST.md) | 业务/优化模型真实连接测试、自定义输入与验收边界 |
| [HTTP_GATEWAY_INTEGRATION.md](HTTP_GATEWAY_INTEGRATION.md) | 标准 API（OpenAI 兼容）：URL、模型 ID、API Key 与旧配置说明 |
| [WS_GATEWAY_INTEGRATION.md](WS_GATEWAY_INTEGRATION.md) | WebSocket 网关：协议差异、页面填写、返回解析规则 |
| [MODEL_OUTPUT_VISIBILITY.md](MODEL_OUTPUT_VISIBILITY.md) | 运行记录中的模型正文、工具参数与历史结果查看规则 |
| [DATASET_EDITING.md](DATASET_EDITING.md) | 导入后创建草稿、编辑案例、CAS 发布与版本保护 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 安装、配置、数据备份、反向代理/SSE、生产加固、故障排查 |
| [DEMO.md](DEMO.md) | 演示数据、预期结果、界面验证步骤（含截图） |
| [CONTRACTS.md](CONTRACTS.md) | 模块/数据结构/API/事件契约（开发基线） |
| [VERIFICATION.md](VERIFICATION.md) | A01–A20 自动化验收与界面联调记录 |
| [REQUIREMENTS_TRACEABILITY.md](REQUIREMENTS_TRACEABILITY.md) | 需求 §1–§54 到实现/测试的追溯与差异说明 |
| [app/samples/README.md](../app/samples/README.md) | 示例 Skill 包、数据集与刻意保留的缺陷 |

## 设计与专项记录

以下文档保留设计边界和各阶段证据；当前用法以以上使用文档及 API 为准，提案中的后续能力不代表已经实现。

| 文档 | 内容 |
| --- | --- |
| [PLATFORM_REVIEW_RSI_2026-10-25.md](PLATFORM_REVIEW_RSI_2026-10-25.md) | RSI 方向的平台评审、问题与后续建议 |
| [MINIMAL_REDESIGN.md](MINIMAL_REDESIGN.md) | 已选“最小改版”的范围与验收；保留单轮内核和简洁页面 |
| [IMAGE_SKILL_EXPANSION.md](IMAGE_SKILL_EXPANSION.md) | 修图主技能分发、12 类参数子技能与示例验证 |
| [ENTERPRISE_GATEWAY_REVIEW.md](ENTERPRISE_GATEWAY_REVIEW.md) | 网关专项问题、修复与本地验收边界 |
