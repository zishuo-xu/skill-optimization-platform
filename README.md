# Skill 优化平台

[![CI](https://github.com/zishuo-xu/skill-optimization-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/zishuo-xu/skill-optimization-platform/actions/workflows/ci.yml)

基于 RSI 思路，把 Skill 的**执行 → 评价 → 优化 → 重跑 → 独立验收**固化为可追溯的工作流，提升选对子技能、调用正确工具及生成正确参数的准确率。

业务执行规则来自 **Prompt + Skill**；平台用测试集衡量结果，让优化模型生成可审查的 Markdown 补丁。日常在同一测试集迭代，正式应用前再用独立验收集检验。每次只生成一个候选，后续迭代由用户发起。

## 快速开始

需要 Python 3.11+。macOS / Linux：

```bash
git clone https://github.com/zishuo-xu/skill-optimization-platform
cd skill-optimization-platform
./start.sh
```

Windows 安装 Python 并加入 PATH 后，运行 `start.bat`；macOS 也可双击 `start.command`。打开 **http://127.0.0.1:8765**。

启动脚本会安装依赖、首次初始化内置任务管理和修图示例、启动服务。内置示例使用离线执行模型与规则修复，适合熟悉操作；不会自动连接你的真实模型。服务启动还会为新旧数据库补齐「入门引导：任务协作」，预置 Skill、数据集及模型配置草稿；不调用模型、不复制历史成绩。已有数据可直接重新运行启动脚本加载新代码，**日常升级不要使用 `--reset`**。

```bash
./start.sh --no-open     # 不自动打开浏览器
./start.sh --port 8766   # 使用其他端口
./start.sh --help        # 查看完整参数
```

安装、Windows 参数、备份、升级与常见故障见[部署指南](docs/DEPLOYMENT.md)。

## 一次完整使用流程

页面只有「首页」和「设置」。首页按 **准备 → 测试 → 优化 → 验收 → 应用** 一步步提示，每次只有一个主按钮：

1. 创建项目，在「设置」导入 Skill ZIP、配置 Prompt 与模型、导入测试集（修改点保存即可，下次运行时自动生效，不需要手动发布）。
2. 首页「运行测试」：得到当前 Skill 的分数和每个失败案例的原因。
3. 「优化 Skill」：生成一个候选并在测试集重跑，首页显示原版 → 候选及修好/变差数量；可继续优化。
4. 「独立验收」：用没参与优化的验收集对原版与候选配对评测。
5. 通过后「应用候选」；旧版本保留在版本历史，可随时切回。

普通评测和优化不强制要求独立验收集；日常重跑全通过也不等于正式验收通过。详细操作见[使用指南](docs/USER_GUIDE.md)。

## 如何判断提升

| 指标 | 含义 |
| --- | --- |
| R：选对技能 | 子技能路线是否符合预期 |
| A：参数正确 | 业务工具序列和参数是否正确 |
| J：整体通过 | 路线、动作、工具、参数及执行结果综合是否通过 |

方法名、枚举、ID 等适合严格比较；描述文字可由评分模型判断语义。工具返回、最终回复是执行证据，不等于参数评分通过。默认不因缺少 finish 单独判失败，但超时、截断或业务错误仍需记录。详见[评分规则](docs/SCORING.md)。

## 已记录的真实模型闭环

2026-10-28，另建任务协作示例，预先声明初始 Skill 的两个映射缺陷，冻结数据后运行：

| 阶段 | 结果 |
| --- | --- |
| 日常基线 → 优化重跑 | 16/24（66.7%）→ 24/24（100%） |
| 独立验收，12 条各跑 3 次 | 原版 24/36 → 候选 36/36 |
| 最终留出测试 | 6/6 |

执行、优化、语义评分共 369 次真实模型请求，无规则回退；业务工具仍使用模拟返回。候选已在当时的本地项目应用。这是合成样例的历史结果，不保证复现相同分数，也不代表生产准确率。

[查看教程、输入资产与证据边界](docs/REAL_MODEL_WALKTHROUGH.md)。更新并重启后，在项目列表点击「入门引导：任务协作 → 从这里开始」。配置并发布自己的模型后，按页面指引运行；已有项目和运行记录保持不变。

## 文档入口

| 读者 / 问题 | 从这里开始 |
| --- | --- |
| 理解平台目标和 RSI 边界 | [核心概念](docs/CONCEPTS.md) |
| 从创建项目到应用候选 | [使用指南](docs/USER_GUIDE.md) |
| 编写 Skill、测试集和 context | [输入规范](docs/INPUTS.md) |
| 理解日常集与独立验收 | [数据隔离与运行模式](docs/DAILY_TEST_AND_ACCEPTANCE.md) |
| 排查通过 / 失败依据 | [评分规则](docs/SCORING.md) |
| 接入模型、部署或排障 | [部署指南](docs/DEPLOYMENT.md)、[HTTP 接入](docs/HTTP_GATEWAY_INTEGRATION.md) |
| 开发与集成 | [架构](docs/ARCHITECTURE.md)、[API](docs/API.md)、[契约](docs/CONTRACTS.md) |
| 查找所有专题与历史记录 | [完整文档导航](docs/README.md) |

## 开发与边界

AI 接手先读 [AGENTS.md](AGENTS.md)；开发流程、检查命令与提交要求见 [CONTRIBUTING.md](CONTRIBUTING.md)，长期产品约束见[设计决策索引](docs/decisions/README.md)。

后端为 Starlette + SQLite + 进程内 Worker；前端为无需构建的原生 ES Modules。主要代码在 `app/`、`web/`，自动化测试在 `tests/`。

```bash
.venv/bin/python -m pytest -q
node --test tests/*.mjs
```

平台面向 PC、单用户本地使用；不训练模型、不自动优化业务 Prompt、不运行真实生产工具，也不自动进行无限轮优化。业务工具由 fixture 模拟，脚本资源随包保存但不自动执行。没有总预算配置，仍保留请求与案例超时、步数限制、取消和实际用量统计。

测试数量和历史验收证据见[验证记录](docs/VERIFICATION.md)；最新行为以当前指南和代码为准，设计提案中的未来项不代表已实现。
