# 一句话修图（image_one_word_fix）

## 含义
处理没有更专用 reference 覆盖的单一自然语言修图诉求，完整保留独立诉求，不猜测强度、颜色或滤镜。`useOneWordFixPicture` 是必填字符串，业务含义是用户完整的一句话，不截断、不改写。

## 参数契约
```json
{
  "method": "imageOneWordFixPicture",
  "fields": {"useOneWordFixPicture": {"type": "string", "minLength": 1}},
  "required": ["useOneWordFixPicture"],
  "defaults": {},
  "mappings": {"useOneWordFixPicture": {}}
}
```

## 澄清
空请求或同时包含多个可拆分专用操作时先澄清或改走专用技能。

## 正反例
- 正例：“把脸上的小瑕疵修自然一点”。
- 反例：“把背景换成海边”，应使用 `image_background_replace`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imageOneWordFixPicture","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"帮我让人物更精神"},"args":{"useOneWordFixPicture":"帮我让人物更精神"}}}
```
