# WebSocket 网关接入

执行环境里的「WebSocket 网关」（`provider: enterprise_ws`）对接的是企业内网的多模型聚合网关：
**OpenAI 兼容的 Chat Completions 报文，经一条 WebSocket 长连接发送**。报文语义与 OpenAI 官方一致，
传输、身份和流式格式由网关私有约定。除标准 API 外，平台只支持这一种调用方式。

## 协议要点

| 维度 | 标准 OpenAI API | 本网关 |
| --- | --- | --- |
| 传输 | HTTP POST + SSE（`data:` 行） | WebSocket，整份请求 JSON 作为一帧发送；响应逐帧、每帧一个完整 chunk JSON |
| 结束 | `data: [DONE]` | 没有结束标记，以 `finish_reason` 出现或连接关闭判定 |
| usage | 尾部单独一帧 | 每帧都带（累计值） |
| 鉴权 | `Authorization: Bearer` | 请求体 `api_key`（接入服务名）+ 握手请求头 `sender`（工号） |
| 模型 | 单厂商 | 网关按 `model` 路由多厂商模型 |
| 遥测 | 无 | 每帧注入 `result_info` / `debug_info`，可能回显整份请求，帧体积大 |

请求体示例（占位值，不可直接使用）：

```json
{
  "api_key": "<接入服务名>",
  "user": "<调用方标识>",
  "model": "<模型名>",
  "stream": true,
  "extra_body": { "enable_thinking": false },
  "stream_options": { "include_usage": true },
  "temperature": 0.0,
  "max_tokens": 1024,
  "messages": [{ "role": "system", "content": "..." }, { "role": "user", "content": "..." }],
  "tools": [ ... ]
}
```

## 在页面上怎么填

业务模型和优化模型分别配置，字段相同：

| 字段 | 填什么 | 对应报文 |
| --- | --- | --- |
| WebSocket 地址 | `ws://<网关地址>/<网关路径>` 或 `wss://…` | 连接地址 |
| 模型名 | 网关支持的模型名 | `model` |
| 接入服务名（api_key） | 接入服务名；也可写 `env:变量名` 从环境变量读取 | 请求体 `api_key` |
| 请求体附加字段（JSON） | `user`、`extra_body`、`stream_options` 等网关字段；点「填入示例」可得到模板 | 原样合并进请求体 |
| 握手请求头（JSON） | `{"sender": "<工号>"}` | 建立连接时的 HTTP 请求头 |

`model`、`messages`、`tools`、`stream`、`temperature`、`max_tokens` 由平台按环境配置填写，
附加字段不能覆盖 `messages` / `tools`。平台不会为 WebSocket 生成 `Authorization` 头；
请求体附加字段里显式写了 `api_key` 时以它为准。

保存后可用「测试连接」发一次短请求（最多 64 个输出 Token，不运行 Skill 和业务工具）。

## 平台如何解析返回

- 每帧按完整 JSON 解析，拼接 `choices[0].delta.content` 与按 `index` 拼接 `delta.tool_calls` 的参数片段；也兼容 SSE 文本帧。
- 收到带 `finish_reason` 的帧即结束：该帧已带 `usage` 时立即结束；不带时最多再等 1 秒收集尾部 usage 或结束标记。网关在结束后保持连接或主动断开都可以。
- `usage` 取最后一帧的累计值，不逐帧相加。
- 没有 `choices`、`code` 为 `0` / `200` 的确认帧不算错误；`code` 为其他值且带 `message` 的帧按网关错误处理，401 类不重试。
- `result_info`、`debug_info` 等遥测字段解析后立即丢弃，不写入运行记录，也不占用缓冲；单帧上限默认 16 MiB（环境配置 `max_size` 可调）。
- 已收到业务输出后连接中断，记为 `stream_interrupted`，不拼接半成品、不重试；连接、首包、空闲、总超时和心跳在「高级：超时与心跳」里配置。

## 验证范围

`tests/test_ws_gateway_protocol.py` 用本地真实 WebSocket 服务按上表行为模拟网关，覆盖：请求体与 `sender` 头、
无 `[DONE]` 且连接不关闭、每帧累计 usage、流式工具调用、确认帧与错误帧、每帧 60KB 遥测共 200 帧、单帧 1.5MB。
本地模拟不等同内网真实网关的连通性和模型效果，首次接入请在执行环境页用「测试连接」确认。
