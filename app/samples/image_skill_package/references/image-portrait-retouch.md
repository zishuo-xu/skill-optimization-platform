# 人像美颜示例

这是为参数评测重新编写的公开合成技能，不包含生产接口或图片定位信息。

## 使用边界
只发送用户要求的磨皮、美白或瘦脸项，范围均为 0..1。未给强度时使用契约默认值；自然美颜包含磨皮和美白。轻微程度见映射说明。

轻微磨皮、美白、瘦脸分别映射为 0.2、0.1、0.15；明确数值优先于这些程度词。

## 参数契约
以下字段定义业务 args；不得补入契约之外的字段。required 表示必填项，defaults 表示允许补齐的默认值。

```json
{
  "method": "retouch_portrait",
  "fields": {
    "smoothSkin": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "whiten": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "slimFace": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    }
  },
  "required": [],
  "defaults": {
    "smoothSkin": 0.3,
    "whiten": 0.2,
    "slimFace": 0.2
  },
  "mappings": {
    "smoothSkin": {
      "磨皮": 0.3,
      "轻微磨皮": 0.2
    },
    "whiten": {
      "美白": 0.2,
      "轻微美白": 0.1,
      "提亮肤色": 0.2
    },
    "slimFace": {
      "瘦脸": 0.2,
      "轻微瘦脸": 0.15
    }
  },
  "success_message": "人像美颜已完成"
}
```

## 执行约束
1. 从当前有效意图取值，忽略已被否定或改口撤销的操作；缺参、无法解析或越界时先询问。
2. 使用 `editImage`，其参数对象包含 `script`、`method`、`args`、`request`。method 是操作标识，不能作为工具名。
3. `request.query` 只保留当前操作相关的原话；`totalSteps` 是合并同类操作后的总数，`currentStep` 从 1 开始。从上下文读取 `source`，用户消息 JSON 显式包含该字段时以其覆盖；仅当最终 `source` 为 `drag` 时附带 `request.source="drag"`。
4. 多步骤逐个发起。检查当前返回后才执行下一步；`code` 严格等于数字 0 才算成功，其他返回按 `message` 说明问题并停止，缺少状态码也不能报告成功。
5. 单独成功执行本功能时仅回复“人像美颜已完成”。已成功的步骤不再重复调用；多步骤全部成功后用简短结果说明结束。

## 参数示例
用户：“人像磨皮”。

```json
{
  "functionName": "editImage",
  "skillName": "sample-photo-editor",
  "arguments": {
    "script": "handlers/photo_ops.py",
    "method": "retouch_portrait",
    "args": {
      "smoothSkin": 0.3
    },
    "request": {
      "query": "人像磨皮",
      "totalSteps": 1,
      "currentStep": 1
    }
  }
}
```

## 易混意图
清除人像后方的路人 → 移除物体；自然美颜不自动增加瘦脸。

共享路由与组合约束见 [主技能](../SKILL.md)。
