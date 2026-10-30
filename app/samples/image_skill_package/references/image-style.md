# 质感风格

这是根据“版本查询后再编辑”的业务形态重新编写的公开合成示例，仅适用于本包的示例修图助手。名称、接口和语料均为教学设计，不对应生产服务，不处理图片定位符。

## 使用边界

- “胶片风格”“黑白风格”“加质感风格”属于本功能；明确说“滤镜”走 [图片滤镜](image-filter.md)。
- 不含风格意图的亮度、饱和度、曝光请求走 [色调调整](image-tone-adjust.md)。
- 用户说出具体风格只说明意图明确，不代表设备支持，仍须查询版本。其他九个子技能不使用本查询。
- 0 为关闭风格；1 自然、2 胶片、3 电影、4 动漫。版本 2 起增加 5 鲜艳、6 明快、7 黑白。

## 参数契约

两种方法都通过 `editImage` 调用，不能把 method 当作工具名。arguments 含 `script`、`method`、`args`、`request`，script 固定 `handlers/photo_ops.py`。以下契约同时供模型阅读和离线模拟器执行；版本表从查询返回选取，不能从用户请求或记忆猜测。

```json
{
  "method": "apply_style",
  "version_query": {"method": "query_style_version", "args": {}, "response_field": "version"},
  "fallback_skill": "image_one_word_fix",
  "unsupported_version": 0,
  "fields": {
    "template": {"type": "integer", "minimum": 0, "maximum": 7},
    "intent": {"type": "integer", "enum": [0, 1]},
    "temperature": {"type": "number"},
    "saturation": {"type": "number"},
    "tone": {"type": "number"},
    "grain": {"type": "number"},
    "vignette": {"type": "number"},
    "softLight": {"type": "number"}
  },
  "required": ["template", "intent"],
  "defaults": {"template": 1},
  "mappings": {"template": {"关闭风格": 0, "原色风格": 0, "自然风格": 1, "胶片风格": 2, "电影风格": 3, "动漫风格": 4, "鲜艳风格": 5, "明快风格": 6, "黑白风格": 7}},
  "parameter_labels": {"temperature": "色温", "saturation": "饱和度", "tone": "光影", "grain": "颗粒", "vignette": "暗角", "softLight": "柔光"},
  "versions": {
    "1": {"templates": [0, 1, 2, 3, 4], "ranges": {"temperature": [0, 20], "saturation": [0, 10], "tone": [0, 10]}, "defaults": {"temperature": 5, "saturation": 6, "tone": 5}},
    "2": {"templates": [0, 1, 2, 3, 4, 5, 6, 7], "ranges": {"temperature": [0, 20], "saturation": [0, 10], "tone": [0, 10]}, "defaults": {"temperature": 5, "saturation": 6, "tone": 5}},
    "3": {"templates": [0, 1, 2, 3, 4, 5, 6, 7], "ranges": {"temperature": [0, 9], "saturation": [0, 9], "tone": [0, 9], "grain": [0, 9], "vignette": [0, 9]}, "defaults": {"temperature": 5, "saturation": 6, "tone": 5, "grain": 5, "vignette": 5}},
    "4": {"templates": [0, 1, 2, 3, 4, 5, 6, 7], "ranges": {"temperature": [0, 9], "saturation": [0, 9], "tone": [0, 9], "grain": [0, 9], "vignette": [0, 9], "softLight": [0, 9]}, "defaults": {"temperature": 5, "saturation": 6, "tone": 5, "grain": 5, "vignette": 5, "softLight": 5}}
  },
  "success_message": "质感风格已完成"
}
```

查询响应示例：`{"code":0,"version":3}`。version 必须是整数；0 表示明确不支持。缺字段、字符串、布尔值或未收录版本均表示能力未知，停止并说明版本无法确认，不得伪装成“不支持”继续编辑。

## 操作规程

1. 先加载本文，再调用 `query_style_version`，args 必须为 `{}`。查询是前置步骤，不能在同一批次发出查询和编辑。
2. 查询 code 必须严格为数字 0；其他返回按 message 说明问题并停止，不重试，不调用替代编辑。
3. 读取 version。明确为 0，或已知版本不支持请求的风格/参数时，加载 [一句话修图](image-one-word-fix.md)，用该步骤原话做一次替代编辑。本次不再调用 apply_style，也不重查版本。
4. 已知且支持时按版本表生成 args。template 未指定时用 1；只传本次提及的调节项，不补齐所有参数。指定具体风格或数值且未使用默认业务值时 intent=1；使用默认风格或默认数值时 intent=0。不得用模板的默认值掩盖未识别的命名风格。
5. 数值超出该版本范围时截到最大/最小值。不同版本范围不可混用；不支持的字段不能靠截断来“支持”。未说明数值但点名了支持的调节项，使用该版本 defaults。
6. 调用 apply_style，检查 code；非数字 0 按 message 停止。成功时使用 message（未提供时用 success_message）；返回 memoryValue 时按字段中文名称补充实际偏好，不猜测未返回的值。
7. 当前请求结束后，下一个新的风格编辑意图重新从步骤 1 开始，不沿用缓存；同一意图已经成功的调用不能重复执行。

两次调用的 request.query 均为本次风格步骤相关原话。totalSteps/currentStep 按用户编辑操作计数，查询不另占一步；source=drag 时两次均携带 source。替代编辑沿用该操作的编号。

## 参数示例

用户：“给照片加黑白风格”。必须先查询，再等待返回。本例查询返回版本 2，随后应用 template=7；版本 1 则应转一句话修图，不能发送 template=7。

```json
[
  {"functionName":"editImage","skillName":"sample-photo-editor","arguments":{"script":"handlers/photo_ops.py","method":"query_style_version","args":{},"request":{"query":"给照片加黑白风格","totalSteps":1,"currentStep":1}}},
  {"functionName":"editImage","skillName":"sample-photo-editor","arguments":{"script":"handlers/photo_ops.py","method":"apply_style","args":{"template":7,"intent":1},"request":{"query":"给照片加黑白风格","totalSteps":1,"currentStep":1}}}
]
```

## 边界与易混淆情况

| 请求 / 返回 | 处理 |
| --- | --- |
| 胶片滤镜 | 直接走滤镜，不查询版本 |
| 胶片风格，返回版本 1 | 查询后 template=2、intent=1 |
| 黑白风格，返回版本 1 | 查询后转一句话修图 |
| 质感风格色温 200，返回版本 2 / 3 | 查询后 temperature 分别为 20 / 9，默认 template=1、intent=0 |
| 质感风格柔光 5，返回版本 3 / 4 | 前者替代编辑，后者允许 softLight=5 |
| 查询返回错误或未知版本 | 停止，不应用，不假设支持 |

共享调用封装见 [主技能](../SKILL.md)。
