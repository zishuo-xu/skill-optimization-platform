# 任务管理业务 Skill 包

本 Skill 包用于项目与任务管理业务系统的执行 Agent。主 Skill 负责分发与编排，
`references/` 下每个子技能负责一个具体业务动作。所有业务动作必须通过固定工具协议完成，
不能凭自然语言假装已经调用工具。

## 子技能索引

| skill_id | 子技能 | 业务工具 | 适用场景关键词 |
| --- | --- | --- | --- |
| task_query | 任务查询 | list_tasks | 查询任务、任务列表、我的任务、任务详情 |
| task_create | 任务创建 | create_task | 创建任务、新建任务、建任务 |
| task_update_status | 任务状态更新 | update_task_status | 更新任务状态、修改状态、标记完成、标记、已完成 |
| task_assign | 任务指派 | assign_task | 指派任务、转交任务、负责人 |
| task_priority | 优先级设置 | set_priority | 设置优先级、优先级、紧急程度 |
| project_query | 项目查询 | get_project | 查询项目、项目详情、项目信息 |
| project_members | 项目成员 | list_members | 项目成员、成员列表、团队成员、成员 |
| task_comment | 任务评论 | add_comment | 任务评论、评论、留言 |
| task_attachment | 附件管理 | add_attachment | 附件、上传文件 |
| task_time_log | 工时登记 | log_work | 工时、登记工时、耗时 |
| task_dependency | 任务依赖 | add_dependency | 依赖、前置任务、阻塞 |
| task_milestone | 里程碑 | create_milestone | 里程碑、阶段目标 |
| task_tag | 标签管理 | add_tag | 标签、打标签 |
| task_search | 任务搜索 | search_tasks | 搜索任务、关键词搜索、查找任务、搜索 |
| task_batch | 批量操作 | batch_update_tasks | 批量、批量修改、批量更新 |
| task_export | 任务导出 | export_tasks | 导出、导出任务、Excel |
| task_stats | 任务统计 | query_stats | 统计、报表、任务统计 |
| task_reminder | 任务提醒 | create_reminder | 提醒、到期提醒、催办 |
| task_template | 模板创建 | create_from_template | 模板、从模板创建、使用模板 |
| task_archive | 任务归档 | archive_task | 归档、归档任务 |
| permission_check | 权限校验 | check_permission | 权限、权限校验、能否操作、能否 |

## 分发规则

1. 读取本次用户请求，按上表“适用场景关键词”匹配子技能；同一请求命中多个子技能时，
   选择匹配关键词最长、最具体的子技能。
2. 用户请求可能是多步骤任务，按执行顺序选择子技能；单轮只调用必要的业务工具。
3. 选定子技能后调用 `select_skill`（稳定 skill_id），再调用 `read_skill` 读取其文件。
4. 读取到子技能文件后，严格按文件中的「参数」与「映射」构造业务工具调用。
5. 业务工具参数必须来自用户请求、上下文或工具返回；不得编造 ID 或参数值。
6. 无法匹配任何子技能时，返回 `no_action`，不要猜测业务工具。

## 输出要求

- 需要业务动作时，按子技能要求调用对应业务工具，然后通过 `finish` 汇报 action 与
  selected_skills。实际工具调用以运行时事件为准，不能靠终止摘要补记。
- 缺少完成业务动作所必需的字段时，先查询可查询的信息；仍无法获得时返回 clarify，
  并列出缺失字段。
