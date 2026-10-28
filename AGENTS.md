# AI 开发入口

本文件适用于整个仓库，供 AI 开发者快速接手。先遵循当前任务的明确要求；个人模型选择、代理偏好和临时任务状态不固化为项目规则。

## 项目目标与阅读顺序

这是基于 RSI 思路的 Skill 优化平台：执行 → 评价 → 优化 → 日常重跑 → 独立验收 → 应用。目标是提高子技能选择、业务工具调用及参数生成的准确率。

开始工作时：

1. 查看 `git status --short` 和当前分支，识别已有修改，不覆盖其他人的工作。
2. 阅读 [README](README.md)、[核心概念](docs/CONCEPTS.md)和[设计决策索引](docs/decisions/README.md)。
3. 按任务查阅 [开发指南](CONTRIBUTING.md)、[架构](docs/ARCHITECTURE.md)、[API](docs/API.md)或[评分规则](docs/SCORING.md)，再检查对应代码与测试。
4. 历史评审和验证记录只作为线索；发生冲突时核对实现，并明确区分当前行为、缺陷与目标设计。

## 必须保持的产品约束

- 业务执行规则来自 **Prompt + Skill**。工具名称和参数写在 Skill 中，通过通用 `call_tool` 接收；不重新引入独立 Tool Schema 配置。
- `evaluate`/`baseline`/`optimize` 使用日常测试集（内部 split 为 `optimize`）。日常评测和优化不能强制要求独立验收集。
- 优化生成一个候选并重跑日常集；成功重跑不等于 accept。`validate` 才使用独立验收集与冻结的 accept-v1 规则，达到门槛后才能应用。
- 执行模型不能收到 expected 或评分标签；优化器不能收到 validation/final_test 内容。保留候选来源链、root 基线、数据重复检查和应用时的版本比较。
- 子技能首次加载后，模型必须有机会看到正文再产生业务调用；不把同一批次中尚未读取新规则的调用当成有效执行。不要恢复 select_skill 后强制重复 read_skill 的流程。
- 工具执行使用 fixture。不得默认接通真实业务服务、执行包内脚本或编造成功返回。fixture_miss 仍保留实际尝试的工具名和参数。
- finish 协议与业务评分分开；默认不单因缺少 finish 判失败。评分模型仅参与配置为语义比较的参数值判断，不能代替结构校验或接受规则。
- 已发布 Skill、环境、数据和历史运行保持版本证据；修改走草稿与新版本，保留 hash、完整性与原子冲突检查。
- 不重新引入总 Token、金额或总时长预算。保留请求/案例超时、调用次数、上下文限制、取消和实际用量统计。
- 页面面向 PC，突出加载子技能、业务工具调用、失败依据和下一步；内部协议放详情，避免把实现复杂度变成额外配置。

## 代码定位

| 任务 | 主要入口 |
| --- | --- |
| 页面与交互 | `web/js/views/`、`web/js/components.js`、`web/css/app.css` |
| API 与应用服务 | `app/api_v3.py`、`app/services.py` |
| 运行模式与候选链 | `app/single_round.py`、`app/services.py` |
| Agent loop 与工具事件 | `app/execution.py`、`app/obs_tools.py`、`app/fixtures.py` |
| 模型接入 | `app/adapters/` |
| 评分与正式验收 | `app/evaluation.py`、`app/judge.py`、`app/acceptance.py` |
| 优化与补丁 | `app/optimizer.py`、`app/patch.py` |
| Skill、数据与存储 | `app/skill_package.py`、`app/dataset.py`、`app/persistence.py`、`app/db.py` |

## 工作与验证

先说明要解决的行为和验收标准，再实施必要改动。若委派任务，明确文件所有权、输入、约束和验收标准，避免多人同时改同一文件；主代理负责最终复核。代理选择遵循当前会话要求，不更改用户选择的主模型。

按影响运行检查，命令及测试映射见 [CONTRIBUTING](CONTRIBUTING.md)：

```bash
.venv/bin/python -m pytest -q
node --test tests/*.mjs
git diff --check
```

- 纯文档修改检查链接、锚点、示例格式与行为说明；不要用旧测试数量冒充本次检查。
- 执行/评分/隔离/存储改动需要相关回归；跨模块修改再运行全量。页面行为修改应在实际浏览器走查相应用户流程。
- 自动化回归使用隔离数据和假模型/本地网关。真实模型验证须在当前任务授权范围内，已有授权无需重复确认；单独记录运行 ID、输入版本、实际模型、回退与结果。
- 连接成功、离线测试通过、真实模型完成、业务副作用是不同证据，不能互相替代。复查优化是否使用了 heuristic 回退。

## 数据与交付

- `data/`、`output/`、`.run/`、`.playwright-cli/` 是本地运行数据，不提交。不要为了测试或修复使用 `make clean`、`--reset` 或删除现有项目。
- 手工脚本如需写库，先设置隔离的 `SKILLOPT_DATA_DIR`/`SKILLOPT_DB_PATH`，再导入 app 并初始化数据库；不要误写用户当前数据库。
- 不把密钥、完整环境配置、原始请求或数据库复制进文档。可分发示例使用合成、脱敏数据，并标明 fixture 与真实模型边界。
- 变更行为时同步相应指南、契约与 CHANGELOG；设计边界变化更新决策索引，避免文档复制出多份不一致规则。
- 提交前审查暂存区。提交记录写明问题、改动点、验证与限制，不能只写“更新”或“修复”。不要混入无关改动。
- 用户要求推送时，检查远端分支并正常推送，不默认 force push；推送后核对远端提交。最终说明实际提交号、验证范围和未解决事项。
