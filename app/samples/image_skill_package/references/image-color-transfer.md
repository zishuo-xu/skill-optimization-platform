# 图片换色（image_color_transfer）

## 含义
把整图或用户指定区域转换为明确目标颜色，不承担滤镜、曝光或背景替换。`targetColor` 是必填字符串；`region` 是区域枚举，默认 `entire`；`preserveTexture` 是布尔值，默认 `true`。缺少目标颜色必须澄清，区域和纹理未说明才使用上述默认，并把默认字段传入 args。

## 参数契约
```json
{
  "method": "imageColorTransfer",
  "fields": {
    "targetColor": {"type": "string", "minLength": 1},
    "region": {"type": "string", "enum": ["entire", "sky", "subject", "background", "clothing"]},
    "preserveTexture": {"type": "boolean"}
  },
  "required": ["targetColor"],
  "defaults": {"region": "entire", "preserveTexture": true},
  "mappings": {"targetColor": {"夕阳": "橙色", "雪白": "白色"}, "region": {"整张": "entire", "天空": "sky", "人物": "subject", "背景": "background", "衣服": "clothing"}, "preserveTexture": {"保留纹理": true, "保持细节": true}}
}
```

## 澄清
没有目标颜色时澄清；“换色”不能用默认颜色替代。区域未指定可用 `entire`。

## 正反例
- 正例：“把天空调成蓝色”→ `targetColor=蓝色, region=sky`。
- 反例：“套一层复古滤镜”应使用 `image_filter`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageColorTransfer","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"把天空换成蓝色并保留纹理"},"args":{"targetColor":"蓝色","region":"sky","preserveTexture":true}}}
```
