# 先验业务契约与实验设计

这是合成任务协作场景，不连接真实任务系统。执行/优化/语义评分必须为真实 API。此文件仅供数据设计与人工审查，不注入执行或优化模型。

- list_tasks：按 task_id 查询详情，或按 project_id 与可选 status 查询列表。状态值 todo/in_progress/done，进行中含正在处理。
- create_task：project_id/title/priority/description。优先级 high/normal/low，紧急、高优先级、加急均为 high，默认 normal。标题按明确原文，description 仅概括详情，无详情为空字符串。
- assign_task：task_id/assignee_id。成员姓名通过本例 context.members 唯一解析，重复姓名必须澄清；可使用先前创建返回的新任务 ID。
- 缺少必填字段先澄清，不产生业务调用；不支持的请求无需操作；工具失败停止。
- 路由、动作、工具名、参数结构、ID、status/priority 严格比较；仅 description 由真实评分模型按含义判断。字面相等不需要重复评分模型调用。
- 初始 Skill 故意含两个已声明的规则缺陷：进行中映射 active，紧急映射 urgent；正确业务值分别 in_progress/high。这是优化机制演示，不代表自然生产错误分布。
- 数据在第一次真实模型运行前冻结。优化仅看到日常集。独立验收为不同项目/对象/场景与表达，3次配对；最终留出集额外保留，且均不进入优化输入。
- 业务模拟返回按工具名匹配，不按预期参数阻断；错误参数由评分判定，不能因 fixture_miss 混淆模型能力。
- 数据集是人工设计的合成样本，不能用于宣称生产准确率。验收通过才应用，不修改答案追求高分。
