# 查询任务
使用 list_tasks。查询某一任务时仅传 task_id；查询项目列表时必须传 project_id，用户指定状态才传 status。
状态映射：待开始/未开始 -> todo；进行中/正在处理 -> active；已完成/已办完 -> done。未提状态时省略 status。
缺少 task_id 且 project_id 也未知时澄清 project_id，不查询全库。
```json
{"functionName":"list_tasks","arguments":{"project_id":"P100","status":"todo"}}
```
```json
{"functionName":"list_tasks","arguments":{"task_id":"T100"}}
```
