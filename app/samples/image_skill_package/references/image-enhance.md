# 画质增强（image_enhance）

## 含义
针对光线、清晰度或噪点问题做增强，不承担换色、滤镜或背景处理。`enhanceType` 是四值枚举，默认 `auto`；`strength` 是 0..1 数字，默认 0.8；未指定时将这两个默认字段都传入 args。用户明确强度但超出范围时澄清，不裁剪到边界。

## 参数契约
```json
{
  "method": "imageEnhance",
  "fields": {"enhanceType": {"type": "string", "enum": ["lighting", "clarity", "denoise", "auto"]}, "strength": {"type": "number", "minimum": 0, "maximum": 1}},
  "required": [],
  "defaults": {"enhanceType": "auto", "strength": 0.8},
  "mappings": {"enhanceType": {"提亮": "lighting", "变亮": "lighting", "暗": "lighting", "清晰": "clarity", "锐化": "clarity", "降噪": "denoise", "增强": "auto"}}
}
```

## 澄清
未说明类型可用 `auto`；用户明确给出强度时转换为 0..1 数值，无法解析时澄清。

## 正反例
- 正例：“夜景太暗，提亮一点”→ `lighting`。
- 反例：“改成胶片风格”应使用 `image_filter`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageEnhance","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"照片太暗，提亮一下"},"args":{"enhanceType":"lighting","strength":0.8}}}
```
