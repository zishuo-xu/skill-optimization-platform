# 替换背景（image_background_replace）

## 含义
把原背景替换为用户明确指定的场景、颜色或素材描述，并保留主体。`background` 是用户明确描述的必填字符串；`keepSubject` 固定为 true。缺少新背景时澄清；单纯把已有背景调色应走换色。

## 参数契约
```json
{
  "method": "imageReplaceBackground",
  "fields": {"background": {"type": "string", "minLength": 1}, "keepSubject": {"type": "boolean", "enum": [true]}},
  "required": ["background", "keepSubject"],
  "defaults": {"keepSubject": true},
  "mappings": {"background": {}, "keepSubject": {"保留主体": true, "保留人物": true}}
}
```

## 澄清
只说“换背景”而未说明新背景时澄清；“透明背景”应使用 `image_background_remove`。

## 正反例
- 正例：“把背景换成海边，保留人物”。
- 反例：“抠出人物并透明”应使用 `image_background_remove`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageReplaceBackground","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"把背景换成海边，保留人物"},"args":{"background":"海边","keepSubject":true}}}
```
