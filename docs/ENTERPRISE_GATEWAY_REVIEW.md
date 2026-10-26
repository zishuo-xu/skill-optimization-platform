# 企业网关专项检查与修复

## 范围与验收

用户最终确定优先打磨标准 API，兼容企业 HTTP 地址、Body 与 Headers；企业协议在内网由内网 AI 继续适配。本批不新增 Python 接入模式。HTTP 接入说明见 [HTTP_GATEWAY_INTEGRATION.md](HTTP_GATEWAY_INTEGRATION.md)。同时保留已完成的 WebSocket 修复。本地模拟不等同企业网络连通。

本批修复已复现的问题：

1. 请求模板、headers 等嵌套凭据经 API 脱敏后，保存草稿必须按同 provider、同路径继承真实值，不能写入掩码；普通字段不被父值覆盖。
2. 业务与优化模型的企业网关配置均保存 ws_url、请求模板与实际生效的心跳/超时参数，兼容旧 heartbeat_s。
3. 企业连接在 finish_reason 后等待短暂尾包窗口收集 usage/DONE，然后可结束仍打开的连接；不把未完成输出当成功。
4. 心跳不算业务输出，不阻止首包超时与安全重试。
5. 顶层 api_key_ref 只在模板未显式给出 api_key 时解析到请求体；保留显式模板值，不生成 Authorization。引用不存在要明确失败。
6. deadline 在请求、流接收检查点与重试/退避生效；连接、发送及阻塞读仍受各自 transport timeout 限制，不宣称即时取消或进程级硬时限。案例与运行 deadline 收紧请求预算。
7. HTTP 收到 DONE 立即结束，保留前面的 usage；已返回业务片段后的超时/错误不重试；正常 350ms 首响应/片段间隔不能被误当超时。
8. 优化模型调用也传递取消/deadline，usage 计入运行预算；网关扩展字段不能覆盖真实任务 messages/tools。

沿用既有解析协议，不增加不清楚语义的模板占位符。验证使用本地假 HTTP / WebSocket 网关与隔离数据，不调用真实企业网关、不修改原项目配置。


## 验收记录（2026-10-25）

- 全量 `.venv/bin/python -m pytest -q`：346 passed，1 个已有 Starlette/httpx 弃用提示。
- 本批新增 27 项测试，包含本地 HTTP / WebSocket 真实 socket 服务和隔离 SQLite 配置回归，没有请求真实企业网关。
- HTTP 验证完整 endpoint、Body/Headers、显式 Authorization、usage、DONE 后保持连接、350ms 首响应/分片间隔、partial 不重试、活跃流 deadline、失败退避。
- 浏览器隔离验证 HTTP 环境 published → draft → 编辑 → 保存 → 重载 → 发布；模板与优化器独立、Body/Headers 字面凭据继承、完整 URL 与隐藏配置保留。截图 `output/playwright/http-gateway-published.png`。
- 原项目的环境页在 8765 加载新版前后端，控制台无错误；已有版本、草稿和运行记录保留，没有自动切换接入方式。
- 最新数据重启前备份 `.run/enterprise-review-20261025/data-before-restart.db`。不回滚用户在检查期间新增的运行或草稿。
- 本地测试与配置检查不证明内网真实网关的连通性、专有响应协议或真实任务效果；具体协议适配边界见 HTTP 接入指南。
