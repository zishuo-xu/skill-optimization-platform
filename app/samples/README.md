# 示例 Skill 包与示例数据（仅用于离线演示/测试）

- `skill_package/`：主 `SKILL.md` + `references/` 下 21 个子技能 + `manifest.json`（稳定 skill_id → 路径映射）。
- `system_prompt.md`：需求文档 §31 的固定业务系统 Prompt 模板。
- `environment.json`：示例执行环境（模型=`demo_scripted`，优化器=`heuristic`，并发/预算/接受规则等）。
- `image_skill_package/`：主技能负责语流理解与路由，13 个子技能负责修图参数契约。
  平台以 `select_skill(skill_id)` 选择子技能并返回正文，内部映射到 `path + skillName`；业务调用为 `functionName + skillName + arguments`，
  `arguments` 包含 `method / script / request(query,totalSteps,currentStep,source?) / args`。标准包字段完整，不再故意缺参数。
- `image_system_prompt.md` / `image_environment.json`：图片编辑示例环境
  （provider=`demo_scripted`，model=`demo-image-v1`，domain=image）。
- `image_dataset.jsonl`：覆盖 13 类操作、澄清、否定改口和有序复合任务。
  共 117 条（52 优化 / 34 验证 / 31 最终测试）。使用完整 `tool.arguments`，同时比较 method、script、当前步骤 query、步骤编号和 args，防止参数正确但调错业务方法。
  运行 `python scripts/build_image_sample_assets.py` 可重新生成。
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

历史演示中，示例环境使用冻结启发式回退，基于失败证据生成 9 处局部修改（5 个文件），经复测和 3 次配对验证按 `accept-v1` 判定 `accept`。当前 seed 命令仅启动优化，候选验收需单独运行；评分由 `app/evaluation.py` 计算。

### 语流修图示例

13 个子技能：一句话修图、换色、去背景、增强、裁剪、旋转/镜像、尺寸、色调、滤镜、人像精修、移除物体、替换背景、背景去色。
主 Skill 只决定路由、否定/改口与操作顺序；字段含义、范围、默认值、自然语言映射及澄清规则放在对应 reference。
每个 reference 的“参数契约” JSON 与人类可读说明、完整工具调用示例保持一致。

该示例的文案和标识为合成教学内容，参数中不包含图片定位符。
工具、方法与脚本路径仅为合成协议标识，未对接真实图片服务；
fixtures 用 `code: 0` 模拟成功，非零码及 `message` 模拟业务失败，不实际生成图片。主 Skill 与 references 可直接作为路由/参数契约参考。
标准包不再为制造优化提升而植入错误；优化集无失败案例时不生成虚假改进，结果为证据不足（无需优化）。
需要演示失败修复时可使用任务管理示例或在隔离测试中显式构造缺陷变体。

已有本地修图项目可执行 `.venv/bin/python -m app.seed --refresh-image`：
发布新的 Skill/数据集版本，保留业务模型、优化模型、预算及所有历史；该操作不调用模型。
不要用 `--reset` 覆盖自己的接入配置。

设计与验证边界见 `docs/IMAGE_SKILL_EXPANSION.md`。

## 关于 `demo_scripted`

`app/adapters/demo_scripted.py` 是**确定性规则模拟器**，按固定 Skill 内容（分发表、参数与映射、澄清条件）执行，
仅用于离线演示与自动化测试；任务管理用默认规则，修图项目通过
`model=demo-image-v1` 选中 `app/adapters/demo_image.py` 的渐进式修图模拟器。
两者都遵循统一 `ModelAdapter` 接口，替换为 `openai_http` 或 `enterprise_ws` 即可接入真实模型。
不同适配器的成绩不可混用（见需求 §41/§42）。

修图示例默认单次运行预算为 1,000,000 tokens，覆盖默认三轮独立配对验收。`--refresh-image` 保留已有预算；旧项目若仍为 300,000，应在配置中调整后再正式验收。真实模型的实际用量取决于模型与请求。冻结启发式不负责修复 v3 JSON 参数契约；需要优化此类错误时接入优化模型。
