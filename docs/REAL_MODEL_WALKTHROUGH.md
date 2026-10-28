# 真实模型任务协作闭环教程

这是一份跨环境参考教程，演示如何用用户自己的模型配置完成一次 Skill 优化闭环。示例资产本身是合成任务和固定 fixtures，可以直接导入跟跑，不要求先替换成用户业务；但不保证得到相同分数。当前平台不执行生产业务工具；如需部署优化后的 Skill，应导出后在外部 Agent 系统单独接入与验收。教程中的项目/运行 ID 仅来自本机历史记录，不能在另一环境打开；服务启动会创建一份新的引导项目，只预置输入资产，不克隆历史项目、模型配置或运行成绩。

## 1. 先理解边界

内置 `demo_scripted` 和 `heuristic` 只用于离线演示/测试。真实闭环要在「Prompt 与模型」（页面内部仍执行环境）分别配置业务模型、优化模型和评分模型并发布新环境版本。真实 API 可使用 `openai_http` 或 `enterprise_ws`，具体字段见 [HTTP_GATEWAY_INTEGRATION.md](HTTP_GATEWAY_INTEGRATION.md) 与 [WS_GATEWAY_INTEGRATION.md](WS_GATEWAY_INTEGRATION.md)。不要把 API key、完整环境 JSON、数据库或原始 trace 放进教程或提交记录。示例运行的业务工具仍是固定 fixture；它不连接外部任务系统，也不要求用户先准备业务系统。

本教程的业务工具是固定 fixture，因此只验证模型是否产生正确的任务协作调用，不验证真实任务系统的副作用。评分中只有 `description` 使用语义判断；路由、动作、工具名、字段结构、ID、`status`、`priority` 等使用严格规则。为减少复核偏差，评分模型应与业务模型分开；本次历史运行执行、优化、评分恰好都是 `deepseek-flash`，不能视为独立模型复核。

## 2. 准备并冻结输入

推荐从项目列表「入门引导：任务协作」点击「从这里开始」。服务启动时自动安装一次，新旧数据库均适用；再次启动不会覆盖你修改、改名或归档后的项目。初始 Skill 与 24/12/6 数据划分已发布，Prompt 与评分规则已预填在环境草稿中。直接进入下节配置业务、优化与评分模型，再按工作台的五步引导运行。

如果项目已归档，在项目列表勾选显示归档项目并恢复即可；无需重置数据。也可以在不启动服务的情况下执行 `.venv/bin/python -m app.seed --ensure-guide` 准备引导，不会调用模型。

以下手工导入步骤仅用于另建练习项目：

1. 准备一个新的本地项目或明确的现有项目。页面入口叫「Prompt 与模型」，其后端对象仍称执行环境。
2. 从仓库根目录打包**初始、非优化后** Skill；ZIP 根目录必须直接包含 `SKILL.md`、`manifest.json` 和 `references/`：

   ```bash
   ZIP_PATH="$PWD/task-collaboration-skill-v1.zip"
   (cd docs/examples/task-collaboration/skill-v1 && \
     zip -r "$ZIP_PATH" SKILL.md manifest.json references)
   unzip -l "$ZIP_PATH"                 # 核对根目录没有多余父目录
   ```

   在左侧「Skill 包」中选择该 ZIP 导入并发布；本练习不要先导入 `skill-candidate/`，否则会失去初始缺陷对照。到「Prompt 与模型」填入示例 `prompt.md` 并配置模型、发布环境，再到「数据集」导入 `docs/examples/task-collaboration/dataset.jsonl`。
3. 该 JSONL 明确含有 `final_test`。导入预检中必须选择「保留文件划分」；如果选择统一设为日常测试或独立验收，系统会阻止导入，避免最终案例流入优化集。导入后检查 24/12/6 统计。
4. 本次闭环的两个预设缺陷在首次运行前已声明：`进行中 → active`、`紧急 → urgent`；正确业务值分别为 `in_progress`、`high`。`business-contract.md` 只供数据设计和人工审查，**不作为执行模型输入**，也不能复制到业务 Prompt 中。
5. 将初始 Skill、数据集和执行环境发布为版本；记录数据集哈希和划分。不要在首次运行后修改 expected、fixtures 或划分来追求高分。
6. 固定三份划分：24 条优化集、12 条独立验证集、6 条最终留出集。优化器只能接收优化集失败证据；validation 用于独立验收和候选接受/拒绝判断，但不能进入优化器输入；final_test 不参与优化或候选选择。

## 3. 配置模型

在「Prompt 与模型」打开引导已准备的草稿（手工新建项目则先创建草稿），配置：

- 业务模型：接收用户请求、选择/加载子技能并调用业务工具；
- 优化模型：读取优化集失败证据，输出受协议约束的 Skill 补丁；
- 评分模型：只在字段比较方式为 semantic 时判断描述含义；其余比较保持 strict。

引导项目已预填以下评分规则，核对即可；手工新建项目时，在页面「评分规则」卡片中，把参数比较方式设为「严格」，关闭「允许多余字段」，添加字段规则：路径 `$.description`、工具名 `create_task`、方式「语义」。再配置评分模型并发布；只填评分模型不会自动改变新环境默认的宽松规则。

通过 API 创建环境时，对应 `scorer_config.compare` 可使用以下片段；只有 create_task.description 走语义评分，其余字段继承 strict。它是环境配置的一部分，不是另一个页面粘贴框：

```json
{
"scorer_config": {
  "version": "score-v1",
  "compare": {
    "version": "compare-v2",
    "mode": "strict",
    "allow_extra_keys": false,
    "field_rules": [
      {"path": "$.description", "tool": "create_task", "mode": "semantic"}
    ]
  }
}
}
```

发布后检查环境快照。连接测试只发送一次短请求，不执行 Skill、业务工具或评测；它不能替代完整闭环。教程不会提供凭据或完整环境配置，用户应在自己的环境中填写并验证。

## 4. 依次运行

基线、优化、独立验收与应用可从页面操作；最终留出目前没有页面启动入口，需要调用 API。运行模式见 [API.md](API.md) 的 `evaluate|baseline|optimize|validate|final_test`：

1. **基线**：只运行 24 条优化集，保存基线指标和失败案例。
2. **优化**：让优化模型只看基线失败证据，生成候选 Skill；检查文件差异和优化器输入摘要。
3. **候选重跑**：在同一 24 条优化集上比较基线和候选。候选不能因为 fixture 返回成功就被当作通过，必须经过评分。
4. **独立验证**：使用 12 条未见案例，基线/候选各重复 3 次；检查 R、A、J、关键回归、基础设施错误和验证完整性。
5. **应用**：只有接受条件通过才应用候选。确认页面与版本记录中的当前指针都已切换，旧版本仍可回滚。
6. **最终留出**：最后用下方 API 请求运行 6 条留出集并单独记录；不要把它们加入优化器输入或候选选择。

API 最小请求体（把占位符换成当前项目中的 ID；这些 ID 不会跨环境有效）：

```json
{"project_id":"<PID>","mode":"baseline","expected_skill_version_id":"<CURRENT_SV_ID>"}
```

基线完成后创建优化运行（候选重跑包含在这次运行内）：

```json
{"project_id":"<PID>","mode":"optimize","reuse_baseline":true}
```

优化完成后，引用优化运行启动独立验收；`source_run_id` 必须指向已完成且尚未作出 decision 的 optimize 运行：

```json
{"project_id":"<PID>","mode":"validate","source_run_id":"<OPTIMIZE_RUN_ID>"}
```

最终留出使用单独请求：

```json
{"project_id":"<PID>","mode":"final_test","skill_version_id":"<CANDIDATE_SV_ID>"}
```

普通评测可用 `mode=evaluate`；它只评测优化集、不调用优化器。请求字段和来源链约束见 [API.md §7](API.md#7-运行)。

新增的自动安装引导已通过一次完整页面验收，见[引导示例用户流程记录](GUIDE_USER_JOURNEY_2026-10-28.md)。下面保留此前手动建立项目的独立历史记录。

## 5. 本机历史闭环的参考结果

以下是 2026-10-28 本地 `output/real-task-loop/` 的历史结果；该目录不随仓库分发，不代表读者重新执行教程后的结果：

| 阶段 | 结果 |
| --- | --- |
| 基线（24 条） | J 16/24；路由 20/20；参数 12/20 |
| 候选重跑（24 条） | J 24/24；参数 20/20 |
| 独立验证（12 条 × 3 次，配对） | 基线 24/36；候选 36/36；`accept-v1` |
| 最终留出（6 条） | 6/6 |

冻结数据 SHA-256：`5d85c0a1687ae633275aa27fde0bb8a4ac853ad2df1f580e169d2601767ae3de`。

| 阶段 | 执行模型请求 | 评分模型请求 | 优化模型请求 | 历史运行 ID（仅原环境） |
| --- | --- | --- | --- | --- |
| 基线 | 68 | 4 | 0 | `run_53c7cfe30ba84769` |
| 优化与重跑 | 68 | 1 | 1 | `run_2abb664e6da042ee` |
| 独立验收 | 206 | 5 | 0 | `run_43c1e22c9a2d4f84` |
| 最终留出 | 15 | 1 | 0 | `run_771df9cbeaa64c0b` |

本次真实 API 请求共 369 次、记录 Token 744516，所有阶段无 fallback。优化器改动两个 reference 文件中的两个枚举映射：`active → in_progress`、`urgent → high`，并补充封闭枚举约束。业务工具仍是 fixture；优化器诊断摘要把 8 条失败写成 7 条，结构化失败 ID 才是准据。

## 6. 证据与复核清单

保存每阶段 summary、数据集哈希、候选 diff、优化器输入摘要和 UI 应用后的版本指针。复核时至少确认：

- 数据集未变，24/12/6 划分未漂移；
- 优化器输入没有独立验证或最终留出案例；
- 模型调用记录显示真实目标模型，未发生 fallback；
- 业务工具返回来自已声明 fixture，不能据此宣称外部系统已完成操作；
- 同型号模型作为执行/优化/评分时，报告明确写出独立性限制；
- 历史 run ID、截图和本地 URL 只作为审计线索，不写成跨环境可点击链接。

可复用示例文件（均为合成、脱敏内容）：[task-collaboration](examples/task-collaboration/)。
