# 一句话修图示例

这是为参数评测重新编写的公开合成技能，不包含生产接口或图片定位信息。

## 使用边界
处理没有对应专用功能的自然语言修饰。原样保存当前修饰片段到 instruction；明确的裁剪、滤镜或其他专用操作先走对应分支。

## 参数契约
以下字段定义业务 args；不得补入契约之外的字段。required 表示必填项，defaults 表示允许补齐的默认值。

```json
{
  "method": "retouch_from_text",
  "fields": {
    "instruction": {
      "type": "string",
      "minLength": 1
    }
  },
  "required": [
    "instruction"
  ],
  "defaults": {},
  "mappings": {
    "instruction": {}
  },
  "success_message": "一句话修图已完成"
}
```

## 执行约束
1. 从当前有效意图取值，忽略已被否定或改口撤销的操作；缺参、无法解析或越界时先询问。
2. 使用 `editImage`，其参数对象包含 `script`、`method`、`args`、`request`。method 是操作标识，不能作为工具名。
3. `request.query` 只保留当前操作相关的原话；`totalSteps` 是合并同类操作后的总数，`currentStep` 从 1 开始。从上下文读取 `source`，用户消息 JSON 显式包含该字段时以其覆盖；仅当最终 `source` 为 `drag` 时附带 `request.source="drag"`。
4. 多步骤逐个发起。检查当前返回后才执行下一步；`code` 严格等于数字 0 才算成功，其他返回按 `message` 说明问题并停止，缺少状态码也不能报告成功。
5. 单独成功执行本功能时仅回复“一句话修图已完成”。已成功的步骤不再重复调用；多步骤全部成功后用简短结果说明结束。

## 参数示例
用户：“请把人物表情修得柔和一些”。

```json
{
  "functionName": "editImage",
  "skillName": "sample-photo-editor",
  "arguments": {
    "script": "handlers/photo_ops.py",
    "method": "retouch_from_text",
    "args": {
      "instruction": "请把人物表情修得柔和一些"
    },
    "request": {
      "query": "请把人物表情修得柔和一些",
      "totalSteps": 1,
      "currentStep": 1
    }
  }
}
```

## 易混意图
背景黑白且人物保留颜色 → 背景去色；不能交给通用修饰。

共享路由与组合约束见 [主技能](../SKILL.md)。
