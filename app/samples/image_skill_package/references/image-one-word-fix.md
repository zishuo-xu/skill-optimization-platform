# 子技能：一句话修图（image_one_word_fix）

## 适用场景

用户用一句自然语言描述局部或整体修图诉求，例如调整眼睛神采、肤色、光线、质感。
该子技能不负责换色、去背景或画质增强。

## 工作流调用

```json
{
  "functionName": "imageEditWorkflow",
  "skillName": "gallery-image-editor",
  "arguments": {
    "method": "imageOneWordFixPicture",
    "script": "scripts/ImageEditorAdaptor.ets",
    "request": {"query": "帮我修一修眼睛，让它更有神采"},
    "args": {"useOneWordFixPicture": "帮我修一修眼睛，让它更有神采"}
  }
}
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `useOneWordFixPicture` | string | 是 | 用户的完整修图诉求，保留原句，不拆分子任务 |

- useOneWordFixPicture

## 约束

- 一句话修图只传入整句需求，不要额外编造强度、滤镜或颜色。
- 如果用户同时要求换色或去背景，应交由对应子技能处理。
