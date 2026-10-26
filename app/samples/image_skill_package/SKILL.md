---
name: gallery-image-editor
description: 将用户最终确认的图片编辑意图按顺序路由到一个或多个专用图片编辑 reference，并组织固定工作流调用。
---

# 图片编辑 Skill

你负责理解用户的最终有效修图意图并选择专用子技能。参数细节、字段类型、默认值和短语映射只从对应 reference 读取；不要在主 Skill 中自行发明参数。

## 语流归一与路由

- 忽略礼貌语、停顿词和“帮我、请、一下”等语气词；保留用户完整原话到 `request.query`，但 `args` 只反映改口后的最终生效意图。
- 处理改口和否定：“不要 X，只做 Y”以及“不是 X，改成 Y”只执行最后确认的 Y；不要把被否定的 X 路由出去。
- 专用技能优先于 `image_one_word_fix`。换背景是 `image_background_replace`，去背景是 `image_background_remove`；换色是 `image_color_transfer`，滤镜是 `image_filter`；裁剪是 `image_crop`，尺寸调整是 `image_resize`。
- 同一请求最多执行 3 个操作，按用户出现顺序选择 reference；如果用户一次提出超过 3 个操作，先澄清本轮要保留哪 3 个，使用 `missing_fields=["operations"]`，不要静默截断或默认执行前 3 个。同一技能的字段合并。所有必填信息齐全前，不要发出任何业务工具调用。
- 无关的普通问答、与图片编辑无关的请求，以及超出下列 reference 能力范围的请求，使用 `finish(action=no_action)`，不要编造 method。

## 子技能索引

| skill_id | 名称 | method | 语义入口关键词 |
| --- | --- | --- | --- |
| image_one_word_fix | [一句话修图](references/image-one-word-fix.md) | imageOneWordFixPicture | 修图、人像、自然、精神 |
| image_color_transfer | [图片换色](references/image-color-transfer.md) | imageColorTransfer | 换色、调成、颜色 |
| image_background_remove | [去除背景](references/image-background-remove.md) | imageRemoveBackground | 抠图、去掉背景、去除背景 |
| image_enhance | [画质增强](references/image-enhance.md) | imageEnhance | 提亮、清晰、降噪、增强 |
| image_crop | [图片裁剪](references/image-crop.md) | imageCrop | 裁剪、裁切、比例 |
| image_transform | [图片变换](references/image-transform.md) | imageTransform | 旋转、翻转、镜像 |
| image_resize | [图片缩放](references/image-resize.md) | imageResize | 缩放、尺寸、宽度、高度、像素 |
| image_tone_adjust | [色调调整](references/image-tone-adjust.md) | imageAdjustTone | 亮度、对比度、饱和度、曝光 |
| image_filter | [图片滤镜](references/image-filter.md) | imageApplyFilter | 滤镜、黑白、胶片、复古 |
| image_portrait_retouch | [人像美颜](references/image-portrait-retouch.md) | imagePortraitRetouch | 磨皮、美白、瘦脸、人像 |
| image_object_remove | [移除物体](references/image-object-remove.md) | imageRemoveObject | 移除、删除物体、去掉路人、水印 |
| image_background_replace | [替换背景](references/image-background-replace.md) | imageReplaceBackground | 换背景、替换背景、背景换成 |

选择 reference 后只读取所需文件。每个 reference 的 `## 参数契约` 是该技能唯一的字段来源。

## 业务选择格式

内部平台用 `select_skill(skill_id)` 和 `read_skill(path)` 实现映射；对业务侧选择统一展示 `path` 与 `skillName`，不要把内部 `skill_id` 当成业务调用参数。

```json
{"path":"references/image-color-transfer.md","skillName":"gallery-image-editor"}
```

各技能的 path 是 manifest 中对应的 reference 路径，`skillName` 固定为 `gallery-image-editor`。

索引中每个名称都链接到对应 reference。上面的 JSON 是一次子技能选择；复合请求按用户顺序分别选择所需 reference，不要一次选择全部文件。

## 固定工具包装

每个选中的子技能都调用 `imageEditWorkflow`。`script` 固定为 `scripts/ImageEditorAdaptor.ets`，`request.query` 必须是用户原话，`method` 和 `args` 来自对应 reference。多操作按用户顺序发出最多 3 个调用；不要把多个 method 合并成一个未声明的 method。

```json
{
  "functionName": "imageEditWorkflow",
  "skillName": "gallery-image-editor",
  "arguments": {
    "method": "<reference 中的 method>",
    "script": "scripts/ImageEditorAdaptor.ets",
    "request": {"query": "<用户原话>"},
    "args": {"<reference 中的字段>": "<解析后的值>"}
  }
}
```
