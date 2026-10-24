# 里程碑（task_milestone）

用途：为项目创建里程碑。

## 参数
- project_id：从请求中提取项目 ID（形如 P123）
- name：取“里程碑”之后的名称（如 v1.0）
- due_date：从请求中提取 ISO 日期（YYYY-MM-DD）

## 说明
- 日期必须保持 ISO 格式，不做时区换算。
