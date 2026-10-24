# 示例 Skill 包与示例数据（仅用于离线演示/测试）

- `skill_package/`：主 `SKILL.md` + `references/` 下 21 个子技能 + `manifest.json`（稳定 skill_id → 路径映射）。
- `tool_schema.json`：固定业务工具协议（21 个工具，平台管理，候选不可修改）。
- `system_prompt.md`：需求文档 §31 的固定业务系统 Prompt 模板。
- `environment.json`：示例执行环境（模型=`demo_scripted`，优化器=`heuristic`，并发/预算/接受规则等）。
- `image_skill_package/`：第二个示例 Skill 包（主 Skill + 4 个渐进式修图子技能）。
- `image_tool_schema.json`：图片编辑工具协议（`imageEditWorkflow`）。
- `image_system_prompt.md` / `image_environment.json`：图片编辑示例环境
  （provider=`demo_scripted`，model=`demo-image-v1`，domain=image）。
- `image_dataset.jsonl`：15 条修图示例（9 优化 / 4 验证 / 2 最终测试），使用同样的简化写法。
- `dataset.jsonl`：45 条示例案例（33 优化集 / 8 验证集 / 4 最终测试集），含澄清、无操作、多路线与关键案例。
  该文件使用平台**简化写法**（`query + skill + tool + mock`），导入时自动展开为完整的
  `expected`/`fixtures`；`mock` 会生成一条 `name: "*"` 的通配模拟返回，因此案例 JSON
  不会为每个工具重复堆叠 fixture；`fin_003` 显式写 `"fixtures": []` 用于演示缺模拟返回。

## 演示数据中刻意保留的缺陷（用于展示单轮优化闭环）

| 缺陷 | 位置 | 失败案例 |
| --- | --- | --- |
| 路由关键词缺失（进行中的任务 / 交给 / 改由 / 新建一个） | `SKILL.md` 分发表 | opt_001/002/004/005/009、val_001/002/003 |
| 状态映射错误 `进行中 -> active` | `references/task_query.md` | opt_033、val_003 |
| 优先级映射错误 `紧急 -> urgent` | `references/task_create.md`、`task_priority.md` | opt_008/013、val_004 |
| 缺少必填参数 `assignee_id` | `references/task_assign.md` | opt_006/007/024、val_002 |
| 缺少澄清条件（缺 title/project_id） | `SKILL.md` | opt_010/011、val_005 |

优化器（示例环境未接真实模型，记录 `optimizer_not_configured` 后使用冻结启发式回退）会基于失败证据生成 9 处局部修改（5 个文件），
经静态检查后生成候选、跑复测与 3 次配对验证，按 `accept-v1` 判定 `accept`。全流程无硬编码评分，评分由 `app/evaluation.py` 确定性计算。

### 修图示例项目刻意保留的缺陷

| 缺陷 | 位置 | 失败案例 |
| --- | --- | --- |
| 路由关键词缺失（眼睛 / 蓝色 / 背景 / 提亮） | `image_skill_package/SKILL.md` 分发表 | img_opt_fix_route、color_route、bg_route、enhance_route |
| 换色映射错误 `夕阳 -> 红色`（应为橙色） | `image-color-transfer.md` | img_opt_color_mapping |
| 缺少必填参数 `keepSubject` | `image-background-remove.md` | img_opt_bg_missing |
| 缺少必填参数 `enhanceType` | `image-enhance.md` | img_opt_enhance_missing |
| 缺少澄清条件（缺 targetColor） | `image_skill_package/SKILL.md` | img_opt_clarify |

启发式回退会基于失败证据生成 8 处局部修改（4 个文件），候选复测 R/A/J 均达到 100%。

## 关于 `demo_scripted`

`app/adapters/demo_scripted.py` 是**确定性规则模拟器**，按固定 Skill 内容（分发表、参数与映射、澄清条件）执行，
仅用于离线演示与自动化测试；任务管理用默认规则，修图项目通过
`model=demo-image-v1` 选中 `app/adapters/demo_image.py` 的渐进式修图模拟器。
两者都遵循统一 `ModelAdapter` 接口，替换为 `openai_http` 或 `enterprise_ws` 即可接入真实模型。
不同适配器的成绩不可混用（见需求 §41/§42）。
