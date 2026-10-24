# 任务提醒（create_reminder）

用途：为任务创建提醒。

## 参数
- task_id：从请求中提取任务 ID（形如 T42）
- channel：提醒渠道

## 默认
默认：channel=app

## 说明
- 用户没有指定渠道时使用默认渠道。
