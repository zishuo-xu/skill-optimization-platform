# 图片编辑 Skill（示例：修图）

本 Skill 采用渐进式加载：先根据用户请求选择一个子技能，再读取对应 reference，
最后调用固定的图片编辑工作流工具 `imageEditWorkflow`。

## 子技能分发

| skill_id | 名称 | 业务方法 | 适用关键词 |
| --- | --- | --- | --- |
| image_one_word_fix | 一句话修图 | imageOneWordFixPicture | 修图、人像 |
| image_color_transfer | 图片换色 | imageColorTransfer | 换色 |
| image_background_remove | 去除底图 | imageRemoveBackground | 抠图 |
| image_enhance | 画质增强 | imageEnhance | 增强 |

选中子技能后，只读取对应的一个 reference 文件，然后调用 `imageEditWorkflow`。
不要一次加载所有 reference，也不要调用工具协议之外的业务工具。

## 固定工作流格式

所有图片编辑子技能共用同一个业务工具，调用结构如下（`skillName` 用于业务侧归属标识）：

```json
{
  "functionName": "imageEditWorkflow",
  "skillName": "gallery-image-editor",
  "arguments": {
    "method": "<子技能对应的方法名>",
    "script": "scripts/ImageEditorAdaptor.ets",
    "request": {"query": "<用户原始请求>"},
    "args": {"<子技能参数名>": "<从请求或 reference 映射得到的值>"}
  }
}
```

- `method` 由分发表中该子技能的“业务方法”列决定。
- `script` 固定为 `scripts/ImageEditorAdaptor.ets`。
- `request.query` 保留用户原话。
- 评测只关注 `arguments.args` 中的子技能参数；不要编造用户未提供的颜色、主体或强度。

## 约束

- 不直接修改原始图片，只返回编辑工作流参数。
- 不调用未在工具协议中声明的工具。
- 缺少必要参数时向用户澄清，不要猜测默认业务值。
