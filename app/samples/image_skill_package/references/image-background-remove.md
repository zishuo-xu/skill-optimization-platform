# 子技能：去除底图（image_background_remove）

## 适用场景

用户要求去掉照片背景、抠图或替换背景底图，并希望保留原图主体。
该子技能只负责背景分离，不负责调色或增强。

## 工作流调用

```json
{
  "functionName": "imageEditWorkflow",
  "skillName": "gallery-image-editor",
  "arguments": {
    "method": "imageRemoveBackground",
    "script": "scripts/ImageEditorAdaptor.ets",
    "request": {"query": "抠图，去掉背景"},
    "args": {}
  }
}
```

## 参数

当前 reference 尚未声明显式的 `args` 字段；工作流参数是否完整由实现补全。

## 约束

- 保留人物、商品等主体，不要裁掉主体边缘。
- 不要调用真实修图服务；只返回工作流参数。
