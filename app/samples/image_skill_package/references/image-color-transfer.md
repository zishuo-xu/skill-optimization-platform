# 图片换色示例

这是为参数评测重新编写的公开合成技能，不包含生产接口或图片定位信息。

## 使用边界
改变整图或指定区域的颜色。targetColor 必须由用户明确给出；未指定区域用 entire，纹理保留默认为 true。预设滤镜不属于此操作。

## 参数契约
以下字段定义业务 args；不得补入契约之外的字段。required 表示必填项，defaults 表示允许补齐的默认值。

```json
{
  "method": "recolor_region",
  "fields": {
    "targetColor": {
      "type": "string",
      "minLength": 1
    },
    "region": {
      "type": "string",
      "enum": [
        "entire",
        "sky",
        "subject",
        "background",
        "clothing"
      ]
    },
    "preserveTexture": {
      "type": "boolean"
    }
  },
  "required": [
    "targetColor"
  ],
  "defaults": {
    "region": "entire",
    "preserveTexture": true
  },
  "mappings": {
    "targetColor": {
      "夕阳": "橙色",
      "雪白": "白色"
    },
    "region": {
      "整张": "entire",
      "天空": "sky",
      "人物": "subject",
      "背景": "background",
      "衣服": "clothing"
    },
    "preserveTexture": {
      "保留纹理": true,
      "保持细节": true
    }
  },
  "success_message": "图片换色已完成"
}
```

## 执行约束
1. 从当前有效意图取值，忽略已被否定或改口撤销的操作；缺参、无法解析或越界时先询问。
2. 使用 `editImage`，其参数对象包含 `script`、`method`、`args`、`request`。method 是操作标识，不能作为工具名。
3. `request.query` 只保留当前操作相关的原话；`totalSteps` 是合并同类操作后的总数，`currentStep` 从 1 开始。从上下文读取 `source`，用户消息 JSON 显式包含该字段时以其覆盖；仅当最终 `source` 为 `drag` 时附带 `request.source="drag"`。
4. 多步骤逐个发起。检查当前返回后才执行下一步；`code` 严格等于数字 0 才算成功，其他返回按 `message` 说明问题并停止，缺少状态码也不能报告成功。
5. 单独成功执行本功能时仅回复“图片换色已完成”。已成功的步骤不再重复调用；多步骤全部成功后用简短结果说明结束。

## 参数示例
用户：“把衣服调成橙色”。

```json
{
  "functionName": "editImage",
  "skillName": "sample-photo-editor",
  "arguments": {
    "script": "handlers/photo_ops.py",
    "method": "recolor_region",
    "args": {
      "targetColor": "橙色",
      "region": "clothing",
      "preserveTexture": true
    },
    "request": {
      "query": "把衣服调成橙色",
      "totalSteps": 1,
      "currentStep": 1
    }
  }
}
```

## 易混意图
加暖色滤镜 → 滤镜；只说换个颜色 → 询问 targetColor。

共享路由与组合约束见 [主技能](../SKILL.md)。
