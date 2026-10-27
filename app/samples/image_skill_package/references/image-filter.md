# 图片滤镜示例

这是为参数评测重新编写的公开合成技能，不包含生产接口或图片定位信息。

## 使用边界
应用整图预设。filterName 必须是契约枚举；strength 默认 0.5，范围 0..1。提到滤镜但未说明种类时询问类型。

## 参数契约
以下字段定义业务 args；不得补入契约之外的字段。required 表示必填项，defaults 表示允许补齐的默认值。

```json
{
  "method": "apply_preset",
  "fields": {
    "filterName": {
      "type": "string",
      "enum": [
        "film",
        "black_white",
        "vintage",
        "warm",
        "cool"
      ]
    },
    "strength": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    }
  },
  "required": [
    "filterName"
  ],
  "defaults": {
    "strength": 0.5
  },
  "mappings": {
    "filterName": {
      "胶片": "film",
      "黑白": "black_white",
      "复古": "vintage",
      "暖色滤镜": "warm",
      "冷色滤镜": "cool",
      "灰调滤镜": "black_white"
    }
  },
  "success_message": "图片滤镜已完成"
}
```

## 执行约束
1. 从当前有效意图取值，忽略已被否定或改口撤销的操作；缺参、无法解析或越界时先询问。
2. 使用 `editImage`，其参数对象包含 `script`、`method`、`args`、`request`。method 是操作标识，不能作为工具名。
3. `request.query` 只保留当前操作相关的原话；`totalSteps` 是合并同类操作后的总数，`currentStep` 从 1 开始。从上下文读取 `source`，用户消息 JSON 显式包含该字段时以其覆盖；仅当最终 `source` 为 `drag` 时附带 `request.source="drag"`。
4. 多步骤逐个发起。检查当前返回后才执行下一步；`code` 严格等于数字 0 才算成功，其他返回按 `message` 说明问题并停止，缺少状态码也不能报告成功。
5. 单独成功执行本功能时仅回复“图片滤镜已完成”。已成功的步骤不再重复调用；多步骤全部成功后用简短结果说明结束。

## 参数示例
用户：“加暖色滤镜，强度 40%”。

```json
{
  "functionName": "editImage",
  "skillName": "sample-photo-editor",
  "arguments": {
    "script": "handlers/photo_ops.py",
    "method": "apply_preset",
    "args": {
      "filterName": "warm",
      "strength": 0.4
    },
    "request": {
      "query": "加暖色滤镜，强度 40%",
      "totalSteps": 1,
      "currentStep": 1
    }
  }
}
```

## 易混意图
背景黑白、主体保持彩色 → 背景去色；整体灰调滤镜 → black_white。

共享路由与组合约束见 [主技能](../SKILL.md)。
