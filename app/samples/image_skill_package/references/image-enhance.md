# 画质增强示例

这是为参数评测重新编写的公开合成技能，不包含生产接口或图片定位信息。

## 使用边界
改善照明、细节或噪点。未指定模式用 auto，未指定强度用 0.8；明确百分比换算到 0..1，越界时询问用户，不能截断数值。

## 参数契约
以下字段定义业务 args；不得补入契约之外的字段。required 表示必填项，defaults 表示允许补齐的默认值。

```json
{
  "method": "enhance_detail",
  "fields": {
    "enhanceType": {
      "type": "string",
      "enum": [
        "lighting",
        "clarity",
        "denoise",
        "auto"
      ]
    },
    "strength": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    }
  },
  "required": [],
  "defaults": {
    "enhanceType": "auto",
    "strength": 0.8
  },
  "mappings": {
    "enhanceType": {
      "提亮": "lighting",
      "变亮": "lighting",
      "暗": "lighting",
      "清晰": "clarity",
      "锐化": "clarity",
      "降噪": "denoise",
      "增强": "auto"
    }
  },
  "success_message": "画质增强已完成"
}
```

## 执行约束
本功能直接调用自己的业务方法，不查询风格版本。
1. 从当前有效意图取值，忽略已被否定或改口撤销的操作；缺参、无法解析或越界时先询问。
2. 使用 `editImage`，其参数对象包含 `script`、`method`、`args`、`request`。method 是操作标识，不能作为工具名。
3. `request.query` 只保留当前操作相关的原话；`totalSteps` 是合并同类操作后的总数，`currentStep` 从 1 开始。从上下文读取 `source`，用户消息 JSON 显式包含该字段时以其覆盖；仅当最终 `source` 为 `drag` 时附带 `request.source="drag"`。
4. 多步骤逐个发起。检查当前返回后才执行下一步；`code` 严格等于数字 0 才算成功，其他返回按 `message` 说明问题并停止，缺少状态码也不能报告成功。
5. 单独成功执行本功能时仅回复“画质增强已完成”。已成功的步骤不再重复调用；多步骤全部成功后用简短结果说明结束。

## 参数示例
用户：“给照片降噪，强度 60%”。

```json
{
  "functionName": "editImage",
  "skillName": "sample-photo-editor",
  "arguments": {
    "script": "handlers/photo_ops.py",
    "method": "enhance_detail",
    "args": {
      "enhanceType": "denoise",
      "strength": 0.6
    },
    "request": {
      "query": "给照片降噪，强度 60%",
      "totalSteps": 1,
      "currentStep": 1
    }
  }
}
```

## 易混意图
让背景模糊 → 景深虚化，本包不支持；不能误当成清晰度增强。

共享路由与组合约束见 [主技能](../SKILL.md)。
