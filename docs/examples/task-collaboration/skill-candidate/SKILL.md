# 任务协作 Skill

支持任务查询、创建、指派。先判断意图，调用 select_skill 加载对应正文；选择已经返回正文，不再重复 read_skill。

| skill_id | 入口 | 文件 |
| --- | --- | --- |
| task_query | 查询、查看、列出任务及状态 | references/task_query.md |
| task_create | 创建、新建任务 | references/task_create.md |
| task_assign | 指派、交给、转交、负责人 | references/task_assign.md |

多步骤按用户顺序执行。创建后指派时，从 create_task 返回读取 task_id，再交给 assign_task；禁止猜任务 ID。只加载需要的子技能。
调用前检查完整请求和上下文中是否有必需字段，缺少时先加载相关子技能再 finish(action="clarify", missing_fields=[缺失字段名])，不得产生业务调用。
上下文 members 是本次项目成员目录，可将姓名唯一匹配成用户 ID；重名或无匹配必须澄清 assignee_id。用户给出的 ID 优先。
工具返回 ok=false 时停止，报告错误，不重试、不声称成功。其他成功返回按实际说明结果。
与支持能力无关的请求 finish(action="no_action", selected_skills=[])。
结束时调用 finish，动作、技能列表和实际过程一致；所有工具参数来自用户、上下文、工具返回或正文默认值。
