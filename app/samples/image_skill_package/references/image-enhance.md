# 子技能：画质增强（image_enhance）

## 适用场景

用户觉得照片偏暗、模糊、噪点多或整体画质不足，希望提亮、增强清晰度或降噪。
该子技能不负责换色和去背景。

## 工作流调用

```json
{
  "functionName": "imageEditWorkflow",
  "skillName": "gallery-image-editor",
  "arguments": {
    "method": "imageEnhance",
    "script": "scripts/ImageEditorAdaptor.ets",
    "request": {"query": "增强一下夜景照片"},
    "args": {"strength": 0.8}
  }
}
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `strength` | number | 否 | 增强强度，默认 0.8 |

- strength

## 默认

strength=0.8

## 约束

- 先判断用户要解决的是光线、清晰度还是噪点，再调用工作流。
- 不要对同一张图重复叠加增强强度。
