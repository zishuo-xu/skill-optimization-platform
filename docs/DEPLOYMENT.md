# 部署与运维

## 1. 环境要求

- Python 3.11+（推荐 3.12）；macOS / Linux / Windows 均可。
- 依赖全部为纯 Python wheel（Starlette、Uvicorn、httpx、websockets、python-multipart），
  不依赖 pydantic 原生扩展。
- 前端为原生 ES Modules，无需 Node.js 构建；Node.js 仅用于可选的 JS 语法检查。
- 浏览器：Chrome/Edge/Firefox/Safari 现代版本（深色主题、EventSource）。

## 2. 安装与启动

```bash
git clone https://github.com/zishuo-xu/skill-optimization-platform
cd skill-optimization-platform
./start.sh
```

`./start.sh` 一键完成：**停止本平台旧服务** → Python 3.11+ 检查 → 创建 `.venv` →
安装依赖 → 首次初始化示例（基线 + 单轮优化）→ 启动服务并打开浏览器。macOS 可双击
`start.command`。重复执行即为“重启”，不会残留旧进程。停止旧服务只匹配本目录启动的
`python -m app.server`；端口被其他程序占用时不会误杀，会提示换端口。

**Windows 10/11**：安装 Python 3.11+（python.org，勾选 “Add python.exe to PATH”），
双击仓库根目录的 `start.bat`，或在 cmd 中执行：

```bat
start.bat
start.bat --reset
start.bat --port 8766
```

`start.bat` 会优先使用 `py -3`，其次 `python`；自动**停止本仓库旧服务**、创建
`.venv\Scripts\python.exe`、安装依赖、初始化示例、启动服务，并用 PowerShell 等待健康检查后
打开浏览器。`--no-kill` 可保留已在运行的服务。
若双击后闪退，请在 cmd 中运行以查看错误信息。

| 命令 | 说明 |
| --- | --- |
| `./start.sh` | 一键启动（幂等：已有数据会跳过初始化） |
| `./start.sh --reset` | 清空 `data/` 后重新初始化并启动 |
| `./start.sh --no-open` | 启动但不打开浏览器 |
| `./start.sh --seed-only` | 只初始化/重置示例数据（会先停止旧服务） |
| `./start.sh --no-kill` | 不停止已运行的服务；已在运行时只打开浏览器 |
| `./start.sh --port 8766` | 指定端口 |
| `./start.sh --help` | 查看参数 |
| `start.bat` | Windows 等价的一键启动（同样支持 `--reset/--no-open/--seed-only/--port/--host/--help`） |

也可手动分步（适合 CI/生产）：

```bash
bash scripts/setup.sh
.venv/bin/python -m app.seed --with-optimize
./run.sh                       # 等价于 .venv/bin/python -m app.server
```

`make setup/seed/demo/start/run/test` 提供同样的快捷入口。

## 3. 配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `SKILLOPT_DATA_DIR` | `<repo>/data` | 数据根目录（SQLite、Skill 包、trace、导出、临时文件） |
| `SKILLOPT_DB_PATH` | `<data>/app.db` | SQLite 数据库路径 |
| `SKILLOPT_HOST` | `127.0.0.1` | 监听地址；对外提供服务时需自行加认证/反向代理 |
| `SKILLOPT_PORT` | `8765` | 监听端口 |
| `SKILLOPT_DISABLE_WORKER` | 空 | 设为 `1` 时不启动后台 Worker（测试/只读查看） |
| `SKILLOPT_PRICE_INPUT_1K` | `0` | 每 1K 输入 Token 单价；与输出单价一起配置后报告显示费用 |
| `SKILLOPT_PRICE_OUTPUT_1K` | `0` | 每 1K 输出 Token 单价 |
| `SKILLOPT_LOG_LEVEL` | `INFO` | 日志级别 |

模型凭据在执行环境配置中设置，可使用 `env:变量名` 引用服务端进程环境变量；须让实际运行服务的进程能读取该变量。API 返回配置时脱敏，企业网关请求体字段按配置原样使用。不要把凭据写进 Skill 或测试数据。

## 4. 数据目录与备份

```
data/
  app.db                 # SQLite（元数据、事件、结果）
  skills/<sv_id>/files/  # 不可变 Skill 包内容
  zips/<sv_id>.zip       # 导出的包缓存
  trace/<run_id>/        # trace artifacts（请求/响应/原始输出）
  exports/               # 其他导出
  tmp/                   # 临时文件
```

备份：停止服务后打包整个 `data/` 目录（或使用 SQLite 在线备份工具备份 `app.db` +
同步 `skills/`、`trace/`）。**只备份 `app.db` 会丢失 Skill 文件与 trace 内容**。

恢复：停服务 → 还原 `data/` → 启动。数据库与服务代码版本需匹配；升级前建议先备份。

旧环境表如果仍含 `tool_schema_json NOT NULL`，新版服务启动时会自动备份数据库并删除该废弃列，解决创建环境草稿时报 `NOT NULL constraint failed: environments.tool_schema_json`。备份位于数据库同目录，文件名为 `app.db.pre-tool-schema.<唯一标识>.bak`（自定义数据库名随之变化）。保留该备份直到确认升级正常；迁移需要 SQLite 3.35 或更新版本。升级只改变表结构，不删除项目、其他环境配置或运行记录；无需使用 `--reset`。

只在明确可清空的演示目录中使用 `--reset` 重置数据；日常升级、创建草稿失败或查看新代码都不需要重置。

## 5. 反向代理与 SSE

若通过 Nginx/Caddy 暴露服务，必须关闭代理对 SSE 的缓冲，否则运行日志不会实时追加：

```nginx
location /api/v3/runs/ {
    proxy_pass http://127.0.0.1:8765;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
}
```

平台本身不内置 TLS/认证。**不要直接绑定 `0.0.0.0` 暴露到公网**；需要远程访问时请在
反向代理层加 HTTPS 与身份认证，并限制来源。

## 6. 并发与任务执行

- 同一数据库建议只运行一个 Worker 实例；多个实例依赖租约互斥（`lease_owner`），
  但 SQLite 写入并发能力有限，生产多实例请迁移到 PostgreSQL/MySQL + 分布式队列。
- Worker 启动时会回收过期租约，把 `running/cancel_requested` 回 `queued` 并继续执行；
  已完成案例结果按唯一键保留（崩溃重跑不会重复计分）。
- 运行中的取消会停止派发新案例并在案例/模型调用边界终止在途请求；已产生费用不回退。

## 7. 生产加固清单

- [ ] 反向代理配置 HTTPS、访问认证（Basic/OIDC）与来源限制。
- [ ] 将 `SKILLOPT_HOST` 保持 `127.0.0.1`，只暴露代理端口。
- [ ] 定期备份 `data/`；为事件/结果表设置保留策略。
- [ ] 接入真实模型网关：执行环境选择 `openai_http` 或 `enterprise_ws`，冻结代码/解析器版本。
- [ ] 监控 `/api/v3/healthz`、Worker 心跳与 `runs` 中长时间 `running` 的租约。
- [ ] 如需多实例：迁移数据库、引入共享任务队列与统一文件存储（对象存储）。
- [ ] 明确最终测试集使用制度：仅阶段验收运行，使用后转为开发数据并补充新留出集。

## 8. 测试与 CI

```bash
.venv/bin/python -m pytest -q        # 2026-10-26：395 passed
for f in web/js/*.js web/js/views/*.js; do node --check "$f"; done
```

GitHub Actions 工作流 `.github/workflows/ci.yml` 在 push / PR 时执行同样的测试与 JS 语法检查。

## 9. 常见问题

| 现象 | 原因/处理 |
| --- | --- |
| 启动报 `Address already in use` | 端口被占用：`SKILLOPT_PORT=8766 ./run.sh` 或停止旧进程 |
| 页面样式/行为异常、点击无反应 | 前端静态资源已默认 `Cache-Control: no-store` 并带版本号；仍异常时关闭标签重开，或强制刷新（⌘⇧R / Ctrl+F5） |
| 运行一直 `queued` | Worker 未启动（检查 `SKILLOPT_DISABLE_WORKER` 未设置、查看启动日志） |
| 运行日志不实时 | 反向代理缓冲了 SSE：按第 5 节关闭 `proxy_buffering` |
| ZIP 导入返回 422 | 查看 `error.details`：路径穿越、缺失 SKILL.md/manifest、失效引用、重复 skill_id 等 |
| 数据集发布被拒 | 预检存在无效行：先修复，或显式 `exclude_invalid=true` 剔除并在导入报告中留痕 |
| 已导入数据集需要修改 | 数据集页创建编辑草稿 → 点击案例修改 JSON 并保存 → 发布新版本；旧版本保留，发布后重跑基线。见 [在线修订](DATASET_EDITING.md) |
| 保存或发布数据集返回 409 | 草稿 hash 或项目当前版本已变化；保留需要的修改，刷新获取最新版本后重新核对再保存，避免覆盖他人修改 |
| 需要验证模型地址与凭据 | 环境页填写自定义输入后点击对应模型「测试连接」；已发布配置也支持。内网网关需在内网验证，见 [连接测试](CONNECTION_TEST.md) |
| 运行记录只有调用统计 | 展开模型返回记录并点击「查看模型输出」；历史未保存的逐次回复无法恢复，可查看已保存案例结果。见 [输出说明](MODEL_OUTPUT_VISIBILITY.md) |
| 评分模型 HTTP 404，业务模型正常 | 两者是独立连接配置；核对评分 URL/模型 ID，若网关仅注册流式路由则开启评分流式，发布新环境并重跑；404 不能仅凭状态码断言根因 |
| 拉代码重启后结果仍旧 | 先用 `git rev-parse --short HEAD` 确认提交和启动目录；旧运行仍用原快照，模型/评分配置修改需发布并新建运行 |
| 无法应用候选 | `decision` 不是 `accept`，或项目当前版本已变化（409）；重新比较后再应用 |
| 后台 Worker 崩溃后运行卡住 | 重启服务会回收过期租约；如仍异常，查看 `runs` 的 `error_code/error_message` 与事件 |

旧数据库若仍有预算列，服务启动会先创建 `.pre-budget.<唯一标识>.bak` 备份，再迁移删除环境和运行中的废弃字段；保留历史结果，不需要手动填预算或删库。

运行不设置总额度或总时长预算。保留单次请求与案例超时、最大调用次数、上下文上限及手动取消；可查看实际 Token 和已配置单价下的费用统计。
