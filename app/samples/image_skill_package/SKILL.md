---
name: sample-photo-editor
description: 将图片编辑请求分解为专用操作，并生成可评测的合成工具参数；包含九类直接编辑与一类必须先查版本的质感风格。
---

# 合成图片编辑示例

本包是公开教学与离线参数评测材料。工具名称、方法、路径、句子和服务返回均为示例设计，不连接生产修图服务，也不处理图片定位符。

## 选择与分解

- 先确定用户最终保留的意图。否定与改口撤回的操作不能执行，礼貌词不产生额外步骤。
- 明确说“滤镜”归滤镜；“风格/风格化”归质感风格。不带风格意图的亮度、饱和度、曝光调整归色调调整，不能因此查询风格版本。
- 优先使用专用功能。一句话修饰仅用于尚无专用功能的普通修图诉求。
- 背景失去颜色、背景黑白并保留主体原色、主体突出，路由到背景去色。整图灰调或黑白滤镜路由到滤镜。背景模糊属于虚化，本包尚无该功能，应提示选择可用功能，不调用背景去色或清晰度增强。
- 按用户顺序拆分并合并同类兼容字段，最多三个操作。超限时询问保留哪些操作，missing_fields=["operations"]。先检查已知的必填参数；质感风格的版本相关范围必须等查询返回后再检查，不能提前猜测。
- 与编辑无关或没有收录的能力以 no_action 结束；必要参数缺失以 clarify 结束。

## 子技能目录

| skill_id | 名称 | method | 语义入口关键词 |
| --- | --- | --- | --- |
| image_one_word_fix | [一句话修图](references/image-one-word-fix.md) | retouch_from_text | 修图、修得、人像、自然、精神 |
| image_color_transfer | [图片换色](references/image-color-transfer.md) | recolor_region | 换色、调成、颜色 |
| image_enhance | [画质增强](references/image-enhance.md) | enhance_detail | 提亮、清晰、降噪、增强 |
| image_crop | [图片裁剪](references/image-crop.md) | crop_frame | 裁剪、裁切、比例 |
| image_transform | [图片变换](references/image-transform.md) | transform_frame | 旋转、翻转、镜像 |
| image_resize | [图片缩放](references/image-resize.md) | resize_frame | 缩放、尺寸、宽度、高度、像素 |
| image_tone_adjust | [色调调整](references/image-tone-adjust.md) | adjust_tone | 亮度、对比度、饱和度、曝光 |
| image_filter | [图片滤镜](references/image-filter.md) | apply_preset | 滤镜、黑白、胶片、复古 |
| image_background_decolor | [背景去色](references/image-background-decolor.md) | desaturate_backdrop | 背景黑白、背景去色、主体突出 |
| image_style | [质感风格](references/image-style.md) | apply_style | 质感风格、风格、风格化 |

## 参数与执行

逐个调用 select_skill(skill_id) 选择必要子技能；返回中已包含对应 reference 正文，不需要再次调用 read_skill。仅当还需读取正文引用的其他文件时使用 read_skill(path)。业务层技能名统一为 sample-photo-editor。每个 reference 的参数契约是该操作唯一字段来源。

只有 image_style 必须先调用 query_style_version，再依据返回执行 apply_style 或转一句话修图；即使用户明确说出风格也不能跳过查询。其他九个子技能直接执行各自方法，禁止为它们添加版本查询。

业务工具统一为 editImage，arguments 中必须有 script、method、args、request。script 固定为 handlers/photo_ops.py。method 与 args 从 reference 取值，没有可调参数的操作使用 args={}。

request.query 仅含当前步骤相关的原话，不能把完整复合请求复制到每个步骤。totalSteps 为本轮合并后的操作数，currentStep 从 1 起递增；版本查询是同一风格操作的前置调用，不额外增加操作数，查询与应用使用相同的步骤编号；单操作二者都为 1。从上下文读取 source；用户消息 JSON 显式包含 source 时以该值覆盖。source 为 drag 才传 request.source="drag"，其他值省略。

每次仅发出一个业务调用并检查返回。code 为数字 0 时记录成功并继续；非零或缺失状态码时停止，根据 message 说明问题，不猜测完成也不自动重试。已经成功的步骤不能重复执行。普通操作成功时使用 reference 的 success_message；质感风格按其专用规程处理 message 和 memoryValue。

通过 finish 结束平台评测；selected_skills 与实际选择一致，summary 承载面向用户的结果。此内部观测接口与业务工具参数分开。

```json
{"functionName":"editImage","skillName":"sample-photo-editor","arguments":{"script":"handlers/photo_ops.py","method":"desaturate_backdrop","args":{},"request":{"query":"让背景变为黑白","totalSteps":1,"currentStep":1}}}
```
