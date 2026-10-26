# 移除物体（image_object_remove）

## 含义
删除用户明确指出的画面物体，并尽量保留其余背景。`target` 是用户明确描述的必填字符串；`preserveBackground` 固定为 true。缺少目标物时澄清，不能猜测。

## 参数契约
```json
{
  "method": "imageRemoveObject",
  "fields": {"target": {"type": "string", "minLength": 1}, "preserveBackground": {"type": "boolean", "enum": [true]}},
  "required": ["target", "preserveBackground"],
  "defaults": {"preserveBackground": true},
  "mappings": {"target": {}, "preserveBackground": {"保留背景": true}}
}
```

## 澄清
“清理一下”没有目标物时澄清；删除人物、主体或整片背景应转到对应技能并确认范围。

## 正反例
- 正例：“移除左下角的路人”。
- 反例：“去掉全部背景”应使用 `image_background_remove`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageRemoveObject","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"移除左下角的路人"},"args":{"target":"左下角的路人","preserveBackground":true}}}
```
