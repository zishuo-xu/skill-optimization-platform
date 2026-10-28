# 真实模型连接测试

## 目标与边界

用户完成配置（含已发布只读版本）后，可以分别测试业务模型、优化模型或评分模型。支持自定义测试输入（按模型角色区分，默认“只回复 OK”），发送一次短模型请求，检查地址、鉴权、响应格式与当前流式模式，返回耗时、模型、脱敏回复摘要或明确错误。连接测试不启动评测，不调用业务工具，不保存/发布配置。

- HTTP API 与 WebSocket 网关沿用正式生产适配器、完整 URL、Body 与 Headers。
- 一次请求，无自动重试；短回复 Token 上限，约 20 秒请求超时设置。同步传输的阻塞取消仍遵循底层 timeout，不承诺独立进程级硬终止。
- 使用 environment_id + target（business / optimizer / judge）从服务端读取已有凭据；未保存草稿覆盖值按同 provider、同路径继承密钥。浏览器不接收旧凭据原值。
- 已发布版本也能测试，不要求先创建草稿。
- 规则修复（heuristic）与离线演示模型不发网络请求，界面不显示测试输入与「测试连接」按钮；Python 流程接入已移除，提交 `provider: flow` 返回 422。
- 检查接入配置保留为本地校验能力；POST /environments/test-connection 升级为真实探测，target 默认 business；mode=check 可请求旧的配置检查。
- payload.input 为可选字符串；空白使用默认测试语句，非空原文（包括换行和连续空格）直接发给所选模型；最多 16000 字符，超长返回字段错误，不静默截断。测试输入不写环境、不要求创建草稿。
- 真实探测在 threadpool 运行，避免阻塞 Web 事件循环。
- 返回 ok, performed, target, provider, model, latency_ms, message, error_code, response_preview（可空）, usage（可空）, finish_reason, response_truncated；不输出原始网关错误体或凭据。
- 仅用户点击才发送请求，界面简短提示会发起一次模型调用；不新增确认弹窗。测试过程中编辑配置后，不能沿用旧配置测试的成功状态。

## 历史验收范围

本地真实 HTTP / WS 假网关验证成功、401/403、404、429、响应格式异常、不可达、超时、流式/非流式、完整 URL/Headers/Body、已存凭据与草稿覆盖、业务/优化目标隔离、单次请求、不会创建运行或改配置。浏览器验证已发布版本可点击、两类模型分别显示结果、失败可重试、配置编辑清除旧结果。不得对原项目真实模型自动发起探测。


## 历史验收结果

- 本批新增 22 项回归，完整 pytest：386 passed，1 个已有弃用提示。
- 真实本地 HTTP/WS socket 覆盖流式/非流式、完整 endpoint、body/headers、鉴权错误/404/429、超时、无效响应、目标隔离、凭据继承、单请求和不改环境/不创建运行。
- 并发 API 测试确认 probe 正在等待模型时 healthz 可及时返回，未阻塞事件循环。
- 隔离浏览器实际通过本地 HTTP 网关点击已发布业务/优化模型，两侧分别显示 `OK ui-business`、`OK ui-optimizer`。截图：`output/playwright/connection-probe-real-http-published.png`。
- 浏览器验证成功后编辑立即清除旧结果；pending 时编辑不会回写旧成功，也不会使按钮永久禁用；mock 明确标记没有真实网络请求。
- 本地验收没有对原项目真实模型发起探测，没有保存或发布用户配置。原环境刷新后可直接使用测试按钮。


自定义输入请求示例：

```json
{"environment_id":"env_...","target":"business","input":"用一句话解释企业网关。"}
```

优化模型使用 `target: optimizer`，输入与业务模型独立。连接测试只发送一个 user message，不运行 Skill/业务工具。最多 64 输出 Token；回复达到上限或摘要发生截断会标记 `response_truncated`，不把“接口可用”等同完整任务执行。

- 新增自定义输入验收：HTTP 服务实际收到业务/优化两侧输入原文（含换行、双空格），非法类型/超长返回 422 且不出网；已发布版本未生成草稿或运行。
- 输入界面截图：`output/playwright/connection-custom-input-20261026.png`；输入编辑仅清除对应目标的旧结果，不影响另一侧。

## 评分模型连接

当前接口也支持 target=judge，临时覆盖使用 judge_config；已发布环境读取 scorer_config.judge。评分、业务、优化分别检查配置，成功状态不能相互替代。评分模型的流式设置必须与网关路由相符。

```json
{"environment_id":"env_...","target":"judge","input":"只回复 OK。"}
```

连接检查仍只是短响应探测，不能证明评分 JSON 或实际案例判定正确；需要带语义字段的案例验证。使用流程见[使用指南](USER_GUIDE.md)。
