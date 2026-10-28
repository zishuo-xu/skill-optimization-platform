# 模型输出可见性

## 已确认的问题

最近真实修图基线 run_9bf1441b40dc4755：77 次 model_call_finished 只有统计，无 response；其中 75 次 content_chars=0，但实际返回结构化工具调用。案例结果保存了业务工具调用，缺少逐次调用正文/内部工具参数的日志入口。

## 本批方案

- 新调用在现有 model_call_finished 中记录脱敏的 response（content、tool_calls、finish_reason、usage、model），不记录请求凭据、原始网关帧和隐藏 reasoning。
- 统一 call_id，保持调用、返回与工具执行关联；优化器完成事件也提供返回内容。
- 大于 8KB 的 response 使用现有 trace_artifacts 按需加载，日志只保留摘要和引用，避免整段返回堵塞 SSE/日志列表。
- 日志行明确“查看模型输出”，正文为空时说明结构化工具调用是输出；默认显示简短摘要，展开后再读详细正文/参数。
- 历史事件没有逐调用输出时，通过只读接口展示该案例已保存的实际结果，并明确是案例级结果，不伪造原始逐次回复。多个阶段/重复结果分别标注。未保存的返回无法恢复。
- 新增 GET /runs/{run_id}/events/{seq}/model-output：source=captured|legacy_case_result|unavailable，scope=model_call|case_result，message，response（可空），case_results（历史列表）。
- 输出涉及的凭据字段和配置中的已知凭据值脱敏；不改变实际执行/评分使用的模型返回。

验收使用隔离数据和本地假模型，覆盖普通文本、仅工具返回、内部工具、超长返回、脱敏、旧记录和不改变评分。已有真实运行只读取，不重跑收费调用。


## 已完成验收

- 全量 pytest：364 passed，保留 1 个已有弃用提示；本批新增 18 项回归。
- 验证普通文本、仅工具调用、内部工具参数、call_id、usage、脱敏与 reasoning 排除、大文件 offload、保存失败预览和旧记录降级；原有评分/接受规则测试保持通过。
- API 验证不存在记录 404、非模型记录 422、跨 run artifact 拒绝、扁平/早期 wrapper artifact 兼容、完整 Unicode 大文本往返。
- 隔离浏览器实际点击验证普通输出、tool-only、历史案例结果、旧优化器摘要和超过 25000 字符的长输出；最终 DOM 实测 25032 字符与原文一致，末尾哨兵可读取，失败重试入口可用。
- 原修图运行 run_9bf1441b40dc4755 的记录 #13 已通过真实服务与浏览器只读验证：明确返回 legacy_case_result，能查看已保存业务工具参数，没有补造原始逐次回复。
- 截图在 `output/playwright/model-output-*.png`；长输出 DOM 证据为 `model-output-long-dom-20261026.png`。服务重启前最新数据备份 `.run/model-output-visibility/before-restart.db`。
- 同时修复输出元数据将数字 input_tokens/output_tokens 误显示为密钥掩码的问题；真正 token/authorization 字段仍脱敏，独立 Node 断言通过。

使用路径：运行详情 → 运行记录 → 展开“模型返回完成” → “查看模型输出”。新调用可查看逐次正文/工具参数；历史记录仅在实际有保存数据时展示案例级结果。此功能没有重跑真实模型。


## 2026-10-28 案例实际结果

“预期与实际”中的实际侧同时展示调用参数、已保存的工具返回和最终回复。调用参数继续按当前规则评分；工具返回和最终回复作为执行证据展示，不因增加展示而改变历史分数。

新执行分别保存 `tool_results` 和 `final_response`，最终回复优先使用有效 `finish.summary`。历史案例从已有记录中只读补齐，按案例、执行阶段和重复轮次隔离；没有保存的返回显示未保存，不伪造空对象或成功结果。工具返回的 JSON null 是已保存的有效返回，与没有记录区分。

验证：Python 全量 685 项、Node 30 项通过；真实历史基线只读检查保持 60/64 原分数。PC 浏览器核对实际侧显示 code=0 工具返回与“背景去色已完成”回复，不重新调用模型。
