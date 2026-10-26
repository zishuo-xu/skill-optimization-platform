# 色调调整（image_tone_adjust）

## 含义
只调整用户明确指定的亮度、对比度、饱和度或曝光值，不自行补齐未提及字段。四个字段分别是整数 -100..100（前三者）或数字 -5..5（曝光），无默认值；缺少明确数值必须澄清。

## 参数契约
```json
{
  "method": "imageAdjustTone",
  "fields": {"brightness": {"type": "integer", "minimum": -100, "maximum": 100}, "contrast": {"type": "integer", "minimum": -100, "maximum": 100}, "saturation": {"type": "integer", "minimum": -100, "maximum": 100}, "exposure": {"type": "number", "minimum": -5, "maximum": 5}},
  "required": [],
  "defaults": {},
  "mappings": {"brightness": {}, "contrast": {}, "saturation": {}, "exposure": {}}
}
```

## 字段说明与澄清
- `brightness`、`contrast`、`saturation` 是整数，范围 -100 到 100；`exposure` 是数字，范围 -5 到 5。每个字段都代表用户明确说出的数值，不能用“提亮一点”猜成固定数字。
- 没有明确数值时澄清；“增强画质”走 `image_enhance`，本技能没有默认值。

## 正反例
- 正例：“亮度提高 30，饱和度降低 10”。
- 反例：“调成蓝色”应使用 `image_color_transfer`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageAdjustTone","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"亮度提高 30，饱和度降低 10"},"args":{"brightness":30,"saturation":-10}}}
```
