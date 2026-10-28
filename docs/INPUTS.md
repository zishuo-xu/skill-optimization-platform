# 输入规范：Prompt、Skill 与测试数据

本文是输入格式参考。操作顺序见[使用指南](USER_GUIDE.md)，评分含义见[评分规则](SCORING.md)，版本保护见[数据集编辑](DATASET_EDITING.md)。

## 输入边界

执行 Agent 的业务规则来自 **Prompt + Skill**。Prompt 写角色和通用执行约束；Skill 写分发、业务工具名称、参数、默认值、缺参和错误处理。模型连接是基础设施配置，不需要再维护一份 Tool Schema。

每个测试案例提供 query 和可选 context；expected 是平台的评分答案，fixtures 是平台返回给工具调用的模拟结果。两者不提前传给执行模型。工具调用之后，实际命中的 fixture 响应会作为本轮工具结果进入下一次模型请求。

## 导入用途优先于 split

| 导入用途 | 处理方式 |
| --- | --- |
| 日常测试集（页面默认） | 将导入案例统一设为 optimize，供评测、优化和重跑 |
| 独立验收集 | 将导入案例统一设为 validation，只用于正式验收 |
| 保留文件划分 | 保留显式 split；未指定的按 group 和比例划分 |

含 final_test 的文件必须选择“保留文件划分”，避免最终留出数据进入优化。通过 API 导入时显式设置 import_purpose；省略该字段按保留文件划分处理，不能假定等同页面默认。完整参数见 [API](API.md)。

## 上下文放在哪里

context 是案例 JSON 的顶层字段，与 query、skill、tool 同级；不是工具 arguments 的一部分。填写用户、对象或业务状态时使用脱敏值，并在请求、上下文和期望中保持一致。

```json
{"id":"assign_context_01","query":"把任务 T101 交给林禾","context":{"members":[{"name":"林禾","id":"U101"}]},"skill":"task_assign","tool":{"name":"assign_task","arguments":{"task_id":"T101","assignee_id":"U101"}},"mock":{"ok":true}}
```

context 不得夹带 expected、评分结论或只能从答案获得的提示。以图片为对象时，也把脱敏后的对象信息放在这里；具体字段由你的 Skill 定义，平台不统一规定图片业务结构。

## Skill 包格式

支持 ZIP 导入（「Skill 包」页 →「导入 ZIP」）。ZIP 根目录必须包含主 Skill 文件，
推荐结构：

```text
your-skill.zip
├── SKILL.md                  # 必需；大小写均可，导入后规范化为 SKILL.md
├── manifest.json             # 可选；缺省时自动生成
└── reference/                # 也可以是 references/
    ├── task_query.md         # 平铺子技能
    └── task_assign/
        └── SKILL.md          # 或按子技能目录嵌套
```

- **主文件**：根目录 `SKILL.md`（也兼容 `skill.md`）；ZIP 外面多套一层目录（如
  `my-skill/SKILL.md`）也可，导入时会自动去掉外层目录。
- **子技能**：`reference/` 或 `references/` 下的 `.md` 文件，支持再嵌套子目录；
  每个文件是一个子技能。
- **manifest.json 可选**。不提供时按 `reference(s)/` 自动生成映射：
  `skill_id` 取文件名（嵌套的 `xxx/SKILL.md` 取目录名），规范化为小写字母/数字/下划线；
  展示名取文件第一个 Markdown 标题，描述取第一段正文。中文或特殊文件名无法稳定生成
  `skill_id` 时会回退为 `skill_1`、`skill_2`。导入结果会返回 `manifest_generated` 警告，
  请在页面上核对子技能清单。
- **建议显式提供 manifest**（尤其是中文文件名、需要稳定 ID 或顺序时）：

```json
{
  "version": 1,
  "main": "SKILL.md",
  "subskills": [
    {"skill_id": "task_query", "name": "任务查询", "path": "reference/task_query.md",
     "description": "查询项目任务"},
    {"skill_id": "task_assign", "name": "任务指派", "path": "reference/task_assign/SKILL.md"}
  ]
}
```

- 文件 UTF-8 编码；拒绝路径穿越、绝对路径与符号链接；首期只允许改写已有 Markdown 文件，
  脚本和资源原样保存但不自动执行。
- 打包方式：把 `SKILL.md`、`reference/`（及可选 `manifest.json`）放在 ZIP 根目录即可；
  macOS/Linux 可在 Skill 目录内执行 `zip -r ../skill.zip .`，Windows 可在文件夹内全选后
  “发送到 → 压缩(zipped)文件夹”。浏览器直接选择文件夹上传的 UI 暂未提供，请使用 ZIP。

## 数据集格式

支持 `.jsonl`（推荐，每行一个案例）与 `.json`（数组，或 `{"cases":[...]}`），UTF-8。
**推荐用下面的简化写法**，只写你关心的“选中哪个子技能 + 调什么工具/参数”：

```json
{"query":"帮我修一修眼睛，让它更有神采","skill":"references/image-one-word-fix.md","tool":{"functionName":"editImage","skillName":"sample-photo-editor","arguments":{"args":{"instruction":"帮我修一修眼睛，让它更有神采"},"method":"retouch_from_text","request":{"query":"帮我修一修眼睛，让它更有神采","totalSteps":1,"currentStep":1},"script":"handlers/photo_ops.py"}},"mock":{"code":0}}
```

简化字段：

| 写法 | 等价含义 |
| --- | --- |
| `skill` | 单个选中子技能；可写 manifest `skill_id`，也可直接写 `references/xxx.md` 路径（自动映射）；多步路线写成 `["a","b"]`，顺序敏感时同时设置 `route_ordered: true` |
| `route` / `routes` | `routes` 可写平铺数组（一条路线）或二维数组（多条可选路线）；未写 `route_ordered` 时按集合比较 |
| `tool` / `tools` | 业务工具调用；可直接用 `functionName` 代替 `name`；`arguments` 原样比较；也可写 `args`，表示只比较实际参数的 `arguments.args`（适合固定 method/script/request 包装、只关心子技能参数的场景） |
| `mock` / `mocks` | 自动生成该案例所有业务工具的模拟返回；`mock` 是统一响应，`mocks` 是 `{工具名: 响应}` |
| `action` | 省略时默认 `tool_call`；有 `missing` 时推断为 `clarify`；`no_action` 需显式写 |
| `missing` | 等价于 `missing_fields`（clarify 必填） |
| `id` / `group_id` / `split` / `tags` / `context` | 可选；`id` 缺省自动生成；`split` 的生效方式由导入用途决定 |

### 渐进式 Skill 的写法

渐进式 Skill 先选中子技能：`select_skill` 返回选中正文；如需其他 reference，可再按需使用 `read_skill`。
平台**只把显式子技能选择计入 R**，读取更多 reference 不计分、也不需要写进数据集：

- 只选一次子技能（最常见）：用上面的最小示例即可，`skill` 写那一个子技能。
- 固定多跳：`"skill": ["references/a.md", "references/b.md"], "route_ordered": true`
  表示完整路线按顺序执行；不设置 `route_ordered` 时按集合比较。
- 不同输入走不同分支：用多条允许路线
  ```json
  "routes": [["a"], ["a", "b"], ["a", "c"]]
  ```
  如果不同分支的工具调用也不同，用完整格式的 `expected.variants`。
- 再次强调：`select_skill`/等价路由事件才会进入 R；`read_skill` 加载 reference 只是运行时上下文。

- 不写 `fixtures` 时：默认按 `expected.tool_calls` 自动生成模拟返回；提供了 `mock`
  则生成**一条** `name: "*"` 的通配模拟返回，覆盖所有业务工具（错误分发也能继续产出路由诊断）；
  `mocks` 按工具配置的返回优先于通配返回。想故意测试缺模拟返回，请显式写 `"fixtures": []`。
- 只关心工具内部参数子树时，可显式使用 `args`：
  ```json
  {"query":"把这张图调成蓝色","skill":"image_color_transfer","tool":{"name":"editImage","args":{"targetColor":"蓝色"}},"mock":{"code":0}}
  ```
  平台会只比较实际调用的 `arguments.args`，外层 `method`/`script`/`request` 不参与 A 评分。
  当前修图示例使用完整 `arguments`；同一工具承载多种 method 时应同时检查方法和包装，避免调用错方法仍通过。
- 也可以写 `fixtures` 映射：`{"tool_name": {"whatever":"response"}}`，或传统数组。
- 完整写法（`expected` + `fixtures` 数组）继续完全兼容，适合多路线、多工具、variants、严格比较规则等高级场景。

完整示例（高级/显式写法）：

```json
{"id":"case_001","query":"查询项目 P123 中进行中的任务","group_id":"g_status_query","split":"optimize","tags":["状态映射"],"expected":{"action":"tool_call","routes":[["task_query"]],"route_ordered":false,"call_match":"ordered_exact","tool_calls":[{"name":"list_tasks","arguments":{"project_id":"P123","status":"in_progress"}}],"missing_fields":[]},"fixtures":[{"name":"list_tasks","match":{},"response":{"tasks":[]},"consume":"always"}]}
```

- `clarify`：必须有 `missing_fields`，不能有业务 `tool_calls`；可带 routes 便于优化器归因
- `no_action`：不能有 routes / tool_calls / missing_fields
- `tool_calls` 是完整业务工具序列；内部工具 `select_skill/read_skill/finish` 不计分
- `routes` 必须对应当前 Skill 包 manifest；导入前先发布 Skill 与执行环境
- `fixtures` 的 `name` 可以是具体工具名，也可以是 `"*"`（任意业务工具的通配回退）；
  未命中记 `fixture_miss`，不会访问真实服务
- 使用“保留文件划分”时，`split` 缺省按 `group_id` 固定种子划分（保留文件划分且未显式配置比例时为 70/20/10；<10 条时退化）
- 重复 `case_id` 会阻止发布；重复 query 仅警告；无效应修复或在导入时明确剔除

仓库示例 `app/samples/dataset.jsonl`（45 条任务管理）与
`app/samples/image_dataset.jsonl`（13 类语流修图，含澄清与多操作）都使用上述简化写法，
可直接打开参考；导入后平台内部自动展开为完整的 `expected`/`fixtures`。

打开页面左侧的「示例项目：任务管理」或「示例项目：修图」，进入「运行详情」查看基线与优化运行。

已导入数据集可以在「数据集」页创建编辑草稿，点击案例修改完整 JSON，保存后发布为新版本；已发布版本和历史运行保持不变，新版本发布后需要重新运行基线。接口保护和并发冲突规则见 [数据集在线修订](DATASET_EDITING.md)。


## fixtures 与 expected 分工

expected 描述“应该怎样调用”，fixture 描述“调用后返回什么”。两者不需要使用相同的参数匹配条件。单纯评测参数生成时，按工具名匹配、match={} 通常能让错误参数进入评分器，避免把参数错误变成模拟返回缺失。

只有需要模拟业务分支时才收紧 match；多步骤要保证返回中的 ID 能支持后续调用，consume=once 还要考虑调用次数。准备失败返回时同步定义 Skill 的中止行为和测试预期。

fixture_miss 不代表从未调用：平台保留工具名及实际参数，标记未取得返回。它仍是能力失败，不会访问真实服务或猜测返回。若预期调用自身没有 fixture 覆盖，正式运行或案例试跑会在预检阻止，先修正数据再发布。

可导入的完整样例见[真实模型教程](REAL_MODEL_WALKTHROUGH.md)。
