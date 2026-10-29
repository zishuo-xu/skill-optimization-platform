# 标准 API 接入（OpenAI 兼容）

执行环境里的「标准 API」（`provider: openai_http`）按 OpenAI 兼容的 Chat Completions 协议调用模型，
页面上只需要三项：**URL、模型 ID、API Key**。业务模型和优化模型分别配置，可以使用不同服务。
除标准 API 外，平台只支持 [WebSocket 网关](WS_GATEWAY_INTEGRATION.md)。

## 页面上怎么填

| 字段 | 填什么 | 平台怎么用 |
| --- | --- | --- |
| URL | API 根地址，如 `https://api.example.com/v1`；也可直接填完整的 `…/chat/completions` | 根地址自动追加 `/chat/completions`；已以该路径结尾时原样使用。不会自动插入 `/v1` |
| 模型 ID | 服务商的模型 ID | 请求体 `model` |
| API Key | 密钥本身，或 `env:变量名`（由服务端从环境变量读取） | 请求头 `Authorization: Bearer <key>` |

业务模型的温度、最大输出 Token、超时和流式开关在「高级：采样与超时」里，评测建议温度固定为 0。优化模型的流式开关在「高级：优化请求」里，评分模型在「高级：评分请求」里，三者独立保存。

页面新建环境及未设置该字段的草稿默认开启优化流式；初次安装的引导示例也显式开启。明确设置的 `false` 会保留。已发布环境缺少 `optimizer_config.stream` 时，仍按非流式发送，页面如实显示「未启用」，不会自动改写历史版本。

若业务模型正常、优化模型返回 404，除 URL、模型 ID 外还需核对流式模式；部分网关仅注册流式路由。对已有环境创建草稿，在「优化模型 → 高级：优化请求」开启流式，测试连接后保存并发布新版本，再使用新环境运行。只拉代码、重启或刷新页面不会修改已发布配置。通过 API 配置时设置 `optimizer_config.stream: true`，不要放进 `request_template`。

保存后页面只显示 API Key「已配置」，不会回填原值；编辑草稿时留空表示沿用原密钥。切换到 WebSocket 网关时不会带走这个密钥。
「测试连接」会发一次短请求，显示耗时、回复摘要或错误原因，详见 [连接测试](CONNECTION_TEST.md)。

对应的环境配置（占位值）：

```json
{
  "provider": "openai_http",
  "base_url": "https://api.example.invalid/v1",
  "model": "your-model-id",
  "api_key_ref": "env:MODEL_API_KEY",
  "temperature": 0,
  "max_tokens": 2048,
  "timeout_s": 120,
  "stream": true
}
```

## 旧配置

早期版本允许在标准 API 下配置「完整请求地址」`url`、请求体附加字段 `request_template` 和请求头 `headers`（企业 HTTP 网关写法）。现在：

- 页面加载旧环境时，`url` 合并进 URL 一项；保存后只保留 `base_url`。不以 `/chat/completions` 结尾的自定义路径不再属于标准 API，请改用 WebSocket 网关或让网关提供兼容地址。
- 仍带 `request_template` / `headers` 的环境，会在表单里显示为「旧配置」并照常随请求发送；清空为 `{}` 并保存即可移除。
- 通过接口直接提交这些字段仍然有效：`url` 优先于 `base_url`；`request_template` 合并进请求体，但不能覆盖 `model`、`messages`、`tools`、`stream` 和采样参数；显式的 `Authorization` 请求头优先于 API Key。

## 当前支持的响应

非流式响应遵循 Chat Completions：

```json
{
  "choices": [{
    "message": {
      "content": "...",
      "tool_calls": [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "tool_name", "arguments": "{\"key\":\"value\"}"}
      }]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
}
```

流式响应支持 SSE：`data: {"choices":[{"delta":...}]}`，包括拆分工具参数、尾部 usage 和 `[DONE]`。收到 DONE 后立即结束；在完整结束标记之前的网络中断不当作成功。已有部分业务输出后不自动重试，避免重复请求。

“测试连接”会向所选业务模型或优化模型发送一次短请求，支持自定义测试输入、已发布版本和未保存草稿，显示耗时、脱敏回复摘要或错误原因。测试不执行 Skill 和业务工具，可能产生少量模型调用费用。真实内网连通性应在内网点击验证。旧的本地配置检查可通过 `mode=check` 请求。详见 [连接测试](CONNECTION_TEST.md)。

## 留给内网 AI 的适配边界

如果公司协议与上述格式不同，集中修改 `app/adapters/openai_http.py`，不要修改 Skill 分发、业务工具执行、答案隔离或评分器。

| 差异 | 修改位置 |
| --- | --- |
| 完整 URL、POST 地址生成 | `OpenAIHTTPAdapter.__init__` / `_default_url` |
| 签名、临时令牌、特殊鉴权 | `_headers`（不要把凭据写进 Prompt / 事件） |
| Body 字段重命名、企业包装层 | `_build_body`（保留实际 messages/tools） |
| 非流式响应包装层 | `_result_from_payload` |
| SSE 外层事件、增量结构 | `_consume_stream`，必要时 `stream_events.py` |

内网修改适配器实现后，应发布新的执行环境版本并重新运行基线，避免复用旧实现的评测缓存。

保持 `ModelAdapter.invoke(messages, config, tools, emit=...)` 返回统一结构：`content`、`tool_calls`（解析后的 name/arguments）、`finish_reason`、`usage`、`model`。Python 接入代码在这层封装即可（平台已不提供流程级 `run_case` 接入）。

同步 HTTP 的取消在收到数据、退避等检查点生效；阻塞中的连接/读等待受 transport timeout 限制，不承诺即时中止。运行/案例 deadline 收紧后续请求和流式检查，不能把它当成独立进程级硬终止。若内网要求严格即时取消，需要在适配器内引入可取消的异步传输或独立进程。

## 本地验收

```bash
.venv/bin/python -m pytest -q tests/test_http_gateway_review.py tests/test_gateway_configuration.py
.venv/bin/python -m pytest -q
```

先用本地 HTTP 假网关覆盖请求格式、鉴权、流结束、工具参数、错误和重试，再在内网执行脱敏真实样例。外网测试通过不能证明企业网络可连通。
