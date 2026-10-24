# 图片编辑 Skill（示例：修图）

本 Skill 使用渐进式加载：先根据分发表选择一个子技能，再读取对应 reference，
最后调用固定的图片编辑工作流工具 `imageEditWorkflow`。

## 子技能分发

| skill_id | 名称 | 业务方法 | 适用关键词 |
| --- | --- | --- | --- |
| image_one_word_fix | 一句话修图 | imageOneWordFixPicture | 修图、人像 |
| image_color_transfer | 图片换色 | imageColorTransfer | 换色 |
| image_background_remove | 去除底图 | imageRemoveBackground | 抠图 |
| image_enhance | 画质增强 | imageEnhance | 增强 |

选中子技能后，只读取对应的一个 reference 文件，然后调用 `imageEditWorkflow`。
业务参数由子技能 reference 中的参数区与映射区决定。

## 约束

- 不直接修改原始图片，只返回编辑工作流参数。
- 不要调用未在工具协议中声明的工具。
