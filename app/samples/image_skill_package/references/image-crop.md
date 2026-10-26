# 图片裁剪（image_crop）

## 含义
按目标画幅裁剪图片并保持主体构图，不改变像素尺寸以外的内容。`aspectRatio` 是必填比例枚举；`focus` 是 `center` 或 `subject`，默认 `subject`。缺少比例时澄清。

## 参数契约
```json
{
  "method": "imageCrop",
  "fields": {"aspectRatio": {"type": "string", "enum": ["1:1", "4:3", "3:4", "16:9", "9:16"]}, "focus": {"type": "string", "enum": ["center", "subject"]}},
  "required": ["aspectRatio"],
  "defaults": {"focus": "subject"},
  "mappings": {"aspectRatio": {"方形": "1:1", "横版": "16:9", "竖版": "9:16", "四比三": "4:3", "三比四": "3:4"}, "focus": {"居中": "center", "主体": "subject"}}
}
```

## 澄清
没有目标比例时澄清；“缩小到 800 像素”应使用 `image_resize`。

## 正反例
- 正例：“裁成竖版 9:16，主体居中”。
- 反例：“把宽度改为 800”应使用 `image_resize`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageCrop","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"裁成竖版 9:16，主体构图"},"args":{"aspectRatio":"9:16","focus":"subject"}}}
```
