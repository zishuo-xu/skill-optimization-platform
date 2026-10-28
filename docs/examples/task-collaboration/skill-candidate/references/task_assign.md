# 指派任务
使用 assign_task，必须传 task_id 和 assignee_id。用户提供 U 开头的 ID 直接使用；姓名从 context.members 唯一匹配。
任务 ID 来自请求或前一步 create_task 返回。缺字段时澄清 task_id 或 assignee_id，不编造、不默认自己。
```json
{"functionName":"assign_task","arguments":{"task_id":"T100","assignee_id":"U100"}}
```
