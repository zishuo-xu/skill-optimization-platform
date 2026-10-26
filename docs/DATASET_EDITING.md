# 数据集在线修订

## 最小流程

已发布数据集保持不可变；从当前或历史版本创建编辑草稿，在案例侧栏编辑 JSON，保存后发布为新版本。旧运行继续引用原数据，不改变历史成绩。草稿不自动成为当前数据集，不用于运行；发布新版本后需重跑基线。

本批只补齐已有案例的编辑闭环，不增加批量编辑或增删行界面。

## 接口与保护

- POST /datasets/{id}/draft：复制已有已发布版本（parent_id 指向来源），完整保留案例 ID、group、split、query、context、expected、fixtures、tags；不改变当前指针。
- PUT /datasets/{id}/cases/{case_id}：只允许 draft；body={case:完整案例, expected_hash:当前数据集hash}；case_id 不允许改名；返回 {dataset:更新后的详情, case:保存案例}。
- 保存验证该案例格式/路线/工具/参数标签；拒绝非法 split 和 group 跨 split 冲突，不静默重新划分其他案例。必须用原子 hash 条件更新，避免覆盖并发修改。
- POST /datasets/{id}/publish：draft 发布前完整重验；body={expected_hash, expected_current_version, set_current:true}。同事务校验草稿 hash/状态与项目当前指针，发布并切换版本；冲突不部分写入。已有 published 版本的历史“设为当前”行为保持兼容。
- 已发布版本的 PUT 返回409。草稿hash、覆盖与统计随保存更新；publish非法数据返回具体错误。
- 编辑与发布不触发模型请求。界面仅修改临时/草稿，已有运行的版本、样本和指标不变。

## 界面

版本栏提供“创建编辑草稿”，在草稿状态提供“发布数据集”；案例侧栏的 JSON 编辑支持保存/错误提示，保存后刷新表格/覆盖/hash。未保存 JSON 离开侧栏或切换数据集时提醒，不会把只读版本假装为可编辑。

验收用隔离数据：published → draft → 修改 query/预期参数 → 保存 → 重载 → 发布，验证旧案例/历史运行不变、草稿期间当前指针不变、发布后基线缓存失效；验证不合法字段、split冲突、并发hash冲突与发布校验。


## 已完成验收

- 本批新增 9 项回归，全量 pytest：395 passed，1 个已有 Starlette/httpx 弃用提示。
- API 验证复制业务字段一致、query/预期参数修订、已发布只读、hash/项目指针冲突回滚、规范化分组冲突、发布完整重验、旧运行/旧案例不变，以及发布后基线不再兼容。
- 真实隔离浏览器验证创建草稿、默认 JSON 编辑、非法文本保留、关闭确认取消、保存后重载与同页连续发布两轮；共保留 v1/v2/v3 三个已发布版本，旧 v1 query 不变。
- 另验证完整案例加载失败时只提示重试，不允许用列表摘要打开可保存编辑器，避免丢失预期参数或 fixtures。
- 支持 X / Esc / 背景关闭统一未保存提醒；清理页面时关闭遗留 drawer。无 guard 的既有 overlay 仍同步执行关闭。
- UI 截图：`output/playwright/dataset-edit-real-two-publishes.png`，截图为本地验收产物，不随源码提交。
- 测试使用隔离数据与离线适配器，没有请求真实模型，没有修改原项目数据。

使用：数据集 → 创建编辑草稿 → 点击案例 → 修改 JSON 并保存 → 发布数据集。保存仅改变草稿，发布才设为当前版本。
