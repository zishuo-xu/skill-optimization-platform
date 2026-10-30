# 图片缩放示例

这是为参数评测重新编写的公开合成技能，不包含生产接口或图片定位信息。

## 使用边界
设置像素尺寸，至少提供一边。单边尺寸保持比例，双边明确尺寸不保持比例；整数范围 1..8192。不可将小数舍入或将越界值截断。

## 参数契约
以下字段定义业务 args；不得补入契约之外的字段。required 表示必填项，defaults 表示允许补齐的默认值。

```json
{
  "method": "resize_frame",
  "fields": {
    "width": {
      "type": "integer",
      "minimum": 1,
      "maximum": 8192
    },
    "height": {
      "type": "integer",
      "minimum": 1,
      "maximum": 8192
    },
    "keepAspectRatio": {
      "type": "boolean"
    }
  },
  "required": [],
  "defaults": {
    "keepAspectRatio": true
  },
  "mappings": {
    "width": {},
    "height": {},
    "keepAspectRatio": {
      "保持比例": true,
      "不保持比例": false
    }
  },
  "success_message": "图片缩放已完成"
}
```

## 执行约束
本功能直接调用自己的业务方法，不查询风格版本。
1. 从当前有效意图取值，忽略已被否定或改口撤销的操作；缺参、无法解析或越界时先询问。
2. 使用 `editImage`，其参数对象包含 `script`、`method`、`args`、`request`。method 是操作标识，不能作为工具名。
3. `request.query` 只保留当前操作相关的原话；`totalSteps` 是合并同类操作后的总数，`currentStep` 从 1 开始。从上下文读取 `source`，用户消息 JSON 显式包含该字段时以其覆盖；仅当最终 `source` 为 `drag` 时附带 `request.source="drag"`。
4. 多步骤逐个发起。检查当前返回后才执行下一步；`code` 严格等于数字 0 才算成功，其他返回按 `message` 说明问题并停止，缺少状态码也不能报告成功。
5. 单独成功执行本功能时仅回复“图片缩放已完成”。已成功的步骤不再重复调用；多步骤全部成功后用简短结果说明结束。

## 参数示例
用户：“高度设为 720”。

```json
{
  "functionName": "editImage",
  "skillName": "sample-photo-editor",
  "arguments": {
    "script": "handlers/photo_ops.py",
    "method": "resize_frame",
    "args": {
      "height": 720,
      "keepAspectRatio": true
    },
    "request": {
      "query": "高度设为 720",
      "totalSteps": 1,
      "currentStep": 1
    }
  }
}
```

## 易混意图
裁成方形 → 裁剪；没有宽高 → 询问 width_or_height。

共享路由与组合约束见 [主技能](../SKILL.md)。
