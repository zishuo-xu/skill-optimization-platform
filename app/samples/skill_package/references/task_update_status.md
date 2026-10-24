# 任务状态更新（task_update_status）

用途：更新单个任务的状态。

## 参数
- task_id：从请求中提取任务 ID（形如 T42）
- status：按下方映射从请求的状态词转换

## 状态映射
映射：进行中 -> in_progress；已完成 -> done；待开始 -> todo

## 说明
- 任务 ID 必须来自用户请求。
