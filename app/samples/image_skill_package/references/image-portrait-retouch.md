# 人像美颜（image_portrait_retouch）

## 含义
对人物执行自然磨皮、美白和瘦脸。三个字段都是 0..1 数字，只传用户请求的操作；未指定强度时分别默认磨皮 0.3、美白 0.2、瘦脸 0.2；“轻微磨皮/轻微美白/轻微瘦脸”映射为 0.2/0.1/0.15。自然美颜才展开为磨皮和美白。

## 参数契约
```json
{
  "method": "imagePortraitRetouch",
  "fields": {"smoothSkin": {"type": "number", "minimum": 0, "maximum": 1}, "whiten": {"type": "number", "minimum": 0, "maximum": 1}, "slimFace": {"type": "number", "minimum": 0, "maximum": 1}},
  "required": [],
  "defaults": {"smoothSkin": 0.3, "whiten": 0.2, "slimFace": 0.2},
  "mappings": {"smoothSkin": {"磨皮": 0.3, "轻微磨皮": 0.2}, "whiten": {"美白": 0.2, "轻微美白": 0.1, "提亮肤色": 0.2}, "slimFace": {"瘦脸": 0.2, "轻微瘦脸": 0.15}}
}
```

## 澄清
没有人像或没有明确美容动作时澄清；强度应保持 0..1。

## 正反例
- 正例：“自然美颜”→ `{smoothSkin:0.3,whiten:0.2}`。
- 反例：“让眼睛更有神”可使用 `image_one_word_fix`。

## 工作流例子
```json
{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"imagePortraitRetouch","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"自然美颜"},"args":{"smoothSkin":0.3,"whiten":0.2}}}
```
