# 图片变换（image_transform）

## 含义
旋转或翻转图片；至少有一个有效的非默认变换。`angle` 是 -180/-90/0/90/180 的整数，顺时针为正，默认 0；`flip` 是 none/horizontal/vertical，默认 none。两者都默认值时澄清。

## 参数契约
```json
{
  "method": "imageTransform",
  "fields": {"angle": {"type": "integer", "enum": [-180, -90, 0, 90, 180]}, "flip": {"type": "string", "enum": ["none", "horizontal", "vertical"]}},
  "required": [],
  "defaults": {"angle": 0, "flip": "none"},
  "mappings": {"angle": {"顺时针九十度": 90, "逆时针九十度": -90, "旋转一百八十度": 180}, "flip": {"水平翻转": "horizontal", "左右翻转": "horizontal", "垂直翻转": "vertical", "上下翻转": "vertical"}}
}
```

## 澄清
只说“变换一下”或只产生 `angle=0, flip=none` 时澄清具体变换。

## 正反例
- 正例：“顺时针旋转 90 度并水平翻转”。
- 反例：“裁成 1:1”应使用 `image_crop`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageTransform","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"顺时针旋转 90 度并水平翻转"},"args":{"angle":90,"flip":"horizontal"}}}
```
