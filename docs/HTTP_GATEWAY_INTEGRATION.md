# 标准 API 与企业 HTTP 网关接入

优先使用标准 Chat Completions API。企业 HTTP 网关复用同一适配器，额外配置完整 URL、请求 Body 扩展字段和 Headers。企业内部的特殊协议由内网环境按本文列出的边界适配。

## 配置

业务模型和优化模型分别保存配置，两者可以使用不同服务。网页的“HTTP API / 企业网关”选项对应 `provider: openai_http`。

标准服务示例（所有凭据均为占位，不可直接用于生产）：

```json
{
  "provider": "openai_http",
  "base_url": "https://model.example.invalid/v1",
  "model": "your-model",
  "api_key_ref": "env:MODEL_API_KEY",
  "stream": true,
  "timeout_s": 120,
  "max_retries": 2,
  "max_tokens": 2048
}
```

`base_url` 为 API 根路径，平台追加 `/chat/completions`；若已经以该路径结尾，不重复追加。平台不会自动插入 `/v1`。

企业网关如果提供完整请求地址，使用高级配置中的 `url`：

```json
{
  "provider": "openai_http",
  "url": "https://gateway.example.invalid/company/model/invoke",
  "model": "your-model",
  "headers": {
    "Authorization": "YOUR_COMPANY_AUTH_VALUE",
    "X-Company": "your-tenant"
  },
  "request_template": {
    "api_key": "YOUR_SERVICE_ID",
    "user": "your-user",
    "extra_body": {"enable_thinking": false},
    "stream_options": {"include_usage": false}
  },
  "stream": true,
  "timeout_s": 120
}
```

- `url` 为最终 POST 地址，优先于 `base_url`，不会追加任何路径。
- `request_template` 是实际 JSON Body 的扩展字段，`api_key`、`user`、嵌套 `extra_body` 等按原结构发送。
- 平台根据当前任务写入 `model`、`messages`、`tools`、`stream` 和采样参数。这些执行字段不接受 `extra_body` 覆盖，模板中的 tools 也不能替代运行时工具列表。
- HTTP 的 `api_key_ref` 在没有显式 `Authorization` Header 时生成 `Bearer <value>`；与 Body 中的 `api_key` 是两个独立配置。若网关只需要 Body 中的 `api_key`，不必配置 `api_key_ref`。
- `headers` 中的值按字符串发送；当前不对自定义 Header 值提供模板占位符解析。
- `api_key_ref` 支持 `env:NAME` / `{"env":"NAME"}`，在服务端解析。浏览器不会拿到引用的实际值。缺失显式引用会报鉴权错误。
- 公共 API 返回字面凭据掩码；同 provider 创建草稿时，未更改的凭据按父版本同路径继承，包含嵌套 Body 和 Headers。请保留 `parent_id`，不能把独立的 `***` 作为新凭据。
- 若网关不支持尾部 usage，可通过 `stream_options.include_usage=false` 明确配置；缺少 usage 不会被伪造为有统计数据。

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

“检查接入配置”仅进行本地配置检查，不代表已连接到内网网关。真实连通和模型行为要在内网环境验收。

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

保持 `ModelAdapter.invoke(messages, config, tools, emit=...)` 返回统一结构：`content`、`tool_calls`（解析后的 name/arguments）、`finish_reason`、`usage`、`model`。Python 接入代码在这层封装即可，不应把“Python 流程级 run_case”混用于单次模型请求。

同步 HTTP 的取消在收到数据、退避等检查点生效；阻塞中的连接/读等待受 transport timeout 限制，不承诺即时中止。运行/案例 deadline 收紧后续请求和流式检查，不能把它当成独立进程级硬终止。若内网要求严格即时取消，需要在适配器内引入可取消的异步传输或独立进程。

## 本地验收

```bash
.venv/bin/python -m pytest -q tests/test_http_gateway_review.py tests/test_gateway_configuration.py
.venv/bin/python -m pytest -q
```

先用本地 HTTP 假网关覆盖请求格式、鉴权、流结束、工具参数、错误和重试，再在内网执行脱敏真实样例。外网测试通过不能证明企业网络可连通。
