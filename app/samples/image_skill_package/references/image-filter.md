# 图片滤镜（image_filter）

## 含义
应用命名滤镜并控制强度，不把滤镜名称误当作颜色迁移。`filterName` 是五值必填枚举；`strength` 是 0..1 数字，默认 0.5，未指定时传入默认强度。缺滤镜类型时澄清。

## 参数契约
```json
{
  "method": "imageApplyFilter",
  "fields": {"filterName": {"type": "string", "enum": ["film", "black_white", "vintage", "warm", "cool"]}, "strength": {"type": "number", "minimum": 0, "maximum": 1}},
  "required": ["filterName"],
  "defaults": {"strength": 0.5},
  "mappings": {"filterName": {"胶片": "film", "黑白": "black_white", "复古": "vintage", "暖色滤镜": "warm", "冷色滤镜": "cool"}}
}
```

## 澄清
没有明确滤镜类型时澄清；“整体变暖色”若未要求滤镜应使用 `image_color_transfer`。

## 正反例
- 正例：“加 0.7 强度的胶片滤镜”。
- 反例：“把天空调蓝”应使用 `image_color_transfer`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageApplyFilter","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"加 0.7 强度的胶片滤镜"},"args":{"filterName":"film","strength":0.7}}}
```
