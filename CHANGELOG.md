# 变更记录

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 的简化格式。

## [Unreleased]

- 模型返回日志支持正文与结构化工具参数查看，输出脱敏，大返回按需加载；旧记录明确降级展示案例保存结果。
- 统一模型调用 call_id，优化器输出同样可查看；新增 18 项输出可见性回归，全量 364 passed。

- 标准 API / 企业 HTTP 网关支持完整 URL、独立请求模板与 Headers；业务/优化模型分别配置，附内网适配指南。
- 修复嵌套凭据脱敏后保存、SSE DONE 等待、部分输出后的重试、心跳字段和 WebSocket 尾包兼容问题；优化器调用纳入运行预算。
- 新增 27 项网关与配置回归，全量 346 passed；内网真实协议留待内网验收。

- 最小界面改版：概览突出下一步，结果页首屏展示结论、操作与独立验证成绩，技术快照/规则/成本折叠。
- 环境优先展示业务模型、优化模型、预算；修复字面凭据展示、留空继承和重新渲染时新凭据输入消失。
- 修复空历史后 SSE 不显示、运行终态不同步；过期候选禁止应用，采用/恢复采用原子版本校验。
- 新增 13 项后端回归，浏览器验证应用/恢复、凭据、SSE、终态与小屏布局；全量 319 passed。

- 修图示例扩展为 12 类功能：主 Skill 负责语流路由，子技能维护参数含义、范围、默认值、映射与澄清。
- 补齐 `path + skillName` 子技能选择和 `functionName + skillName + arguments` 工作流示例。
- 数据集新增口语、否定改口、缺参和有序多操作；修图改为校验完整工具参数包装。
- 标准修图包移除刻意植入的错误，离线模拟器读取子技能契约并保留旧版格式兼容。
- 增加 `python -m app.seed --refresh-image`，在保留模型配置、预算与历史的情况下发布新版示例，不调用模型。

## [1.0.0] - 2026-10-24

首期交付：可运行的中文 Web 前后端、后台任务执行器、SQLite 数据与版本存储、
确定性评分器、模型/工具适配层、示例数据与 A01–A20 自动化验收。

### Added

- **第二个示例项目「示例项目：修图」**：渐进式图片编辑 Skill 包（主 `SKILL.md` +
  4 个 reference 子技能）、`imageEditWorkflow` 工具协议、15 条简化格式数据集；
  新增确定性图片编辑模拟器 `app/adapters/demo_image.py`（通过 `model=demo-image-v1`
  选择）。基线 R 50% / A 12.5% / J 11.1%，单轮优化后 R/A/J 均 100%，`decision=accept`。
  4 个子技能 reference 按实际调用格式（functionName/skillName/method/script/request/args）
  重写并补齐适用场景、参数表、映射与约束；数据集只校验 `args`。
- **一键启动**：`./start.sh`（macOS/Linux）与 `start.bat`（Windows 10/11）自动**停止旧服务**、
  检查 Python、创建 `.venv`、安装依赖、首次初始化示例（基线 + 单轮优化）、启动并打开浏览器；
  支持 `--reset/--no-open/--seed-only/--no-kill/--port/--host/--help`；macOS 可双击
  `start.command`。旧服务检测只匹配本仓库的 `app.server` 进程，端口被其他程序占用时不会误杀。
- **项目与版本**：项目列表/概览、Skill 包导入（ZIP/文件夹 JSON，multipart 上传）、
  manifest 校验与安全检查、草稿/发布/不可变版本、导出 ZIP、版本比较、设为当前（回滚）。
- **执行环境**：业务 Prompt、业务模型（demo/标准 API/企业网关 WebSocket/Python 流程）、
  独立优化模型、工具协议、上下文加载、运行参数、预算、评分器与接受规则；发布后只读。
- **数据集**：JSONL/JSON 导入、预检（行号/错误码/字段路径）、稳定 ID、按 group 固定种子划分、
  覆盖统计、发布不可变、同结构导出。
- **评测**：`score-v1` 确定性评分（R/A/J 独立、集合/有序路线、调用序列、
  严格 JSON 深度比较、schema 先校验、clarify/no_action、诊断分类与字段路径）。
- **单轮优化**：预检 → 基线（兼容缓存复用）→ 错误分析（仅优化集）→ 单候选生成 →
  补丁静态检查 → 复测 + 3 次配对验证 → `accept-v1` 判定；候选不覆盖当前版本。
- **Worker**：数据库租约 + 心跳、崩溃恢复、任务持久化、取消、预算耗尽处理。
- **运行记录 UX**：事件持久化与 SSE 回放/续接、问题摘要与定位、筛选、展开详情、
  跟随/暂停、按需加载大内容、状态文案与空态/失败态。
- **模型适配**：企业网关 WebSocket（SSE 式事件聚合、分片工具参数、尾部 usage、
  超时/重试/中断）、OpenAI 兼容 HTTP、流程级 `run_case`、纯文本路由解析与能力声明。
- **安全**：执行器不接收 expected/答案、优化器不读验证/最终测试集、补丁白名单与静态检查、
  凭据脱敏、fixture 未命中不访问真实服务。
- **文档**：README、架构、API、部署、演示、契约、验收记录、需求追溯、样例说明。
- **测试**：233 条 pytest 用例（A01–A20 + 双示例项目回归），GitHub Actions CI。

### Changed

- **失败案例定位**：结果概览新增「只看失败案例（N）」入口；基线运行显示「基线通过/基线失败」
  数量；案例列表直接显示首个差异字段路径（如 `$.args.keepSubject`）。
- **案例详情区分预期与实际**：新增「数据集预期（评分依据）」区块，直接展示预期
  `tool_calls` / `args`；「实际结果」区块明确展示实际路线、工具、`args` 内联 JSON，
  参数差异标注「预期（数据集）/实际（运行结果）」；工具调用摘要自动展开
  `arguments.args`，不再显示整段 method/script/request 包装。
- **支持只比较工具参数的子树**：数据集 `tool.args` / canonical `arguments_path`（如
  `args`）让 A 只比较 `arguments.args`，忽略固定的 method/script/request 包装；
  修图示例项目已改为该模式，A 直接关注子技能参数。
- **数据集支持简化写法**：可直接写 `query + skill + tool/arguments (+ mock)`，导入时自动展开为
  canonical `expected`/`fixtures`；`skill` 支持 reference 路径自动映射、`tool` 支持
  `functionName`；缺省 `fixtures` 自动生成通配模拟返回，显式 `fixtures: []` 可关闭。
  示例数据集同步改为简化写法（45 条 / 约 14KB），指标与判定保持不变；`mock` 统一返回
  用一条 `name: "*"` 通配 fixture 覆盖全部业务工具，既保留错误分发的路由诊断，也避免
  每个案例重复堆叠 21 条模拟返回。
- **Skill 包导入兼容常见结构**：根目录 `SKILL.md` / `skill.md` 均可（自动规范化），
  `reference/` 与 `references/` 均可，支持嵌套 `<子技能>/SKILL.md`；无 `manifest.json`
  时自动生成 `skill_id`、展示名与描述，显式 manifest 仍优先；导入响应返回生成/规范化警告。
- **执行环境页重构**：顶部增加环境快照摘要；配置按「业务接入 / 执行与工具 / 优化与验收」
  三组组织；折叠卡片标题直接显示关键摘要；只读版本改为摘要展示而非成片禁用输入框；
  按 provider 精简字段（演示模型/标准 API/企业网关/Python 流程分别展示）；
  数值参数改为单行输入，高级参数二级折叠；模型接入方式切换与只读草稿引导保持一致。

### Fixed

- 业务系统 Prompt 默认折叠，避免首屏被长文本占满；展开后可编辑/查看全文，
  保存校验失败时自动展开。
- 已发布执行环境的「接入方式」按钮只读无反馈：现在点击会提示创建草稿并在草稿中切换，
  同时展示只读说明与示例模型标识。
- 执行环境保存按钮在修改字段后未及时启用的问题。
- 环境配置字段名统一为后端规范（`base_repeats`/`validation_repeats`/`max_seconds`/
  `scorer_config.version` 等），并兼容旧字段读取；保存时保留未知扩展字段，避免草稿丢配置。
- 数值字段误渲染为多行文本域导致配置页冗长。
- 数据集预检接口 `coverage_stats` 未初始化导致的 500。
- 凭据脱敏规则误伤 `max_tokens`/`tokens` 等限额字段。
- Skill ZIP 导出重复写入 `manifest.json` 导致重新导入失败。
- 前端 SSE/事件历史在筛选与定位时的边界问题（由无障碍/交互联调发现）。

### Known limitations

- 示例环境使用确定性 `demo_scripted` 模型，真实效果需接入企业网关或标准 API。
- 单用户本地部署（SQLite + 进程内 Worker）；多实例需替换共享数据库与任务队列。
- A15–A18 界面验收采用真实浏览器人工联调，未纳入 CI 自动化。
