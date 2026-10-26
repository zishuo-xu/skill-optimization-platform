# 去除背景（image_background_remove）

## 含义
分离并保留图片主体，输出透明背景；不替换成新背景。`keepSubject` 是固定为 true 的布尔字段，通常直接使用默认值；`outputBackground` 是固定为 `transparent` 的字符串。只有当语句无法判断用户是在去除整片背景还是移除某个物体时才澄清意图，不因为主体名称未出现而追问。

## 参数契约
```json
{
  "method": "imageRemoveBackground",
  "fields": {"keepSubject": {"type": "boolean", "enum": [true]}, "outputBackground": {"type": "string", "enum": ["transparent"]}},
  "required": ["keepSubject", "outputBackground"],
  "defaults": {"keepSubject": true, "outputBackground": "transparent"},
  "mappings": {"keepSubject": {"保留主体": true, "保留人物": true}, "outputBackground": {"透明背景": "transparent"}}
}
```

## 澄清
“替换背景”不是去背景；若用户没有说明主体且图片包含多个候选主体，先澄清。

## 正反例
- 正例：“抠出人物，背景透明”。
- 反例：“换成海边背景”应使用 `image_background_replace`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageRemoveBackground","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"去掉照片背景，保留人物"},"args":{"keepSubject":true,"outputBackground":"transparent"}}}
```
