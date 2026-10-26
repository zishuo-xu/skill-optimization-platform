# 图片缩放（image_resize）

## 含义
按明确像素宽度或高度缩放图片。`width`/`height` 是 1..8192 的整数，至少一个必填；`keepAspectRatio` 是布尔值，单边默认 true，双边明确给出时为 false。缺少尺寸或越界时澄清。

## 参数契约
```json
{
  "method": "imageResize",
  "fields": {"width": {"type": "integer", "minimum": 1, "maximum": 8192}, "height": {"type": "integer", "minimum": 1, "maximum": 8192}, "keepAspectRatio": {"type": "boolean"}},
  "required": [],
  "defaults": {"keepAspectRatio": true},
  "mappings": {"width": {}, "height": {}, "keepAspectRatio": {"保持比例": true, "不保持比例": false}}
}
```

## 澄清
至少要有宽或高；无法解析单位或数值超出 1..8192 时澄清。用户同时给宽高时 `keepAspectRatio=false`。

## 正反例
- 正例：“宽度改成 1200，保持比例”。
- 反例：“裁成 16:9”应使用 `image_crop`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageResize","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"宽度改成 1200，保持比例"},"args":{"width":1200,"keepAspectRatio":true}}}
```
