# 子技能：图片换色（image_color_transfer）

## 适用场景

用户希望把整张图片或画面氛围调整为目标颜色，例如蓝色、暖色、夕阳色调。
该子技能只负责颜色迁移，不负责去除背景或增强画质。

## 工作流调用

```json
{
  "functionName": "imageEditWorkflow",
  "skillName": "gallery-image-editor",
  "arguments": {
    "method": "imageColorTransfer",
    "script": "scripts/ImageEditorAdaptor.ets",
    "request": {"query": "把这张图调成蓝色"},
    "args": {"targetColor": "蓝色"}
  }
}
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `targetColor` | string | 是 | 目标颜色；从用户描述或颜色映射中提取 |

- targetColor

## 映射

夕阳 -> 红色

## 约束

- 用户没有给出目标颜色时不要猜测，应要求澄清。
- 颜色映射按本 reference 的映射表执行；不要自行扩展未声明的颜色。
