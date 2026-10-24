# 批量操作（batch_update_tasks）

用途：批量更新多个任务的状态。

## 参数
- task_ids：从请求中提取所有任务 ID（如 T42、T43）
- status：按下方映射从请求的状态词转换

## 状态映射
映射：进行中 -> in_progress；已完成 -> done；待开始 -> todo

## 说明
- task_ids 是数组，顺序与请求中出现顺序一致。
