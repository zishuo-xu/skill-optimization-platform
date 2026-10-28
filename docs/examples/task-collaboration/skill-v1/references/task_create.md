# 创建任务
使用 create_task，字段 project_id、title、priority、description。project_id 和 title 必需，缺失则澄清对应字段。
标题使用引号内明确标题，不把项目、优先级、详情加入标题。description 根据用户详情简洁概括，保留问题与目标，不添加解决方案；没有详情时 description=""。
priority 映射：紧急/高优先级/加急 -> urgent；普通/常规 -> normal；低优先级/不着急 -> low；未指定默认为 normal。
返回成功时可从 task_id 得到新任务标识。
```json
{"functionName":"create_task","arguments":{"project_id":"P100","title":"示例标题","priority":"normal","description":""}}
```
