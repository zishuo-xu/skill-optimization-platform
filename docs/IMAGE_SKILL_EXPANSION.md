# 修图 Skill：语流路由与参数契约扩展

## 目标

主 Skill 根据用户最终有效意图分发子技能；子技能把口语表达转换为固定工作流参数。
保留一个 `imageEditWorkflow` 业务工具，通过 method 区分修图功能。
不在主 Skill 堆放参数细节，不使用最终自然语言回复质量代替路由与参数评测。
新增方法是本仓库的示例协议，未证明外部 ImageEditorAdaptor SDK 已实现这些功能。

## 职责

- 主 Skill：语流归一、改口/否定/歧义处理、功能边界、必要 reference 路由、多意图顺序。
- 子技能：method、字段类型/范围/枚举、默认值、自然语言映射、澄清条件、正反例与调用示例。
- 工具包装：method、固定 script、request.query 原始输入、args 由对应子技能产生。
- 业务选择格式使用 `{"path":"references/<子技能>.md","skillName":"gallery-image-editor"}`。
- 业务调用格式使用 `{"functionName":"imageEditWorkflow","skillName":"gallery-image-editor","arguments":{"method":"...","script":"scripts/ImageEditorAdaptor.ets","request":{"query":"用户原话"},"args":{}}}`。
- 平台内部 select_skill/read_skill 负责将 manifest 稳定 ID 映射到业务 path；内部观测协议不替代上述业务格式。
- 模拟器：读取技能正文中的契约后确定性演示，不读取数据集 expected，不等于真实模型。
- 数据集：完整比较工具调用（包括 method/script/request/args），包含口语、负例、澄清与多操作。

## 功能契约

| skill_id | method | args |
| --- | --- | --- |
| image_one_word_fix | imageOneWordFixPicture | useOneWordFixPicture: string，保留独立的一句话修图诉求 |
| image_color_transfer | imageColorTransfer | targetColor: string 必填；region: entire/sky/subject/background/clothing 默认 entire；preserveTexture: boolean 默认 true |
| image_background_remove | imageRemoveBackground | keepSubject: boolean 固定 true；outputBackground: string 固定 transparent |
| image_enhance | imageEnhance | enhanceType: lighting/clarity/denoise/auto；strength: number 0..1 默认 0.8 |
| image_crop | imageCrop | aspectRatio: 1:1/4:3/3:4/16:9/9:16 必填；focus: center/subject 默认 subject |
| image_transform | imageTransform | angle: -180/-90/0/90/180 默认 0（顺时针为正）；flip: none/horizontal/vertical 默认 none，必须有有效变换 |
| image_resize | imageResize | width/height: integer 1..8192，至少一个；keepAspectRatio: boolean，单边尺寸为 true，显式双边尺寸为 false |
| image_tone_adjust | imageAdjustTone | brightness/contrast/saturation: integer -100..100；exposure: number -5..5；只传用户明确指定的字段和值 |
| image_filter | imageApplyFilter | filterName: film/black_white/vintage/warm/cool 必填；strength: number 0..1 默认 0.5 |
| image_portrait_retouch | imagePortraitRetouch | smoothSkin/whiten/slimFace: number 0..1；只包含请求的操作，未指定强度时分别默认 0.3/0.2/0.2；自然美颜默认磨皮+美白 |
| image_object_remove | imageRemoveObject | target: string 必填（用户明确描述）；preserveBackground: boolean 固定 true |
| image_background_replace | imageReplaceBackground | background: string 必填（用户明确描述）；keepSubject: boolean 固定 true |

每个 reference 在 `## 参数契约` 下提供 JSON 对象：`method`、`fields`（JSON Schema properties）、`required`、`defaults`、`mappings`（按字段组织的短语→协议值）。
此块与参数表/示例应一致，供模型阅读并让离线模拟器读取；不包含案例 ID 或答案表。

## 语流规则

- 使用语义而非机械关键词做路由；停顿词不构成新操作。
- “不要 X，只做 Y”“不是 X，改成 Y”以最后确认的有效意图为准；保留原话于 request.query。
- 专用技能优先于一句话兜底；换背景不等于去背景，改变颜色不等于滤镜，尺寸不等于裁剪。
- 多操作最多 3 项，按用户顺序；同一技能的兼容字段合并，完整核对必填项后再执行任何业务调用。
- 缺颜色/目标物/尺寸等必要信息时澄清，不以默认值替代缺失的用户意图；仅允许使用 reference 明确声明的默认值。
- 未指向修图的普通问答为 no_action；范围外的图像能力不编造 method。

## 实施与验收

1. 扩展并保持 manifest、12 个 references、工具 schema、业务 Prompt 一致。
2. 更新离线模拟器，让其读取子技能契约，支持代表性的语流/复合任务；保持 demo 标识。
3. 扩展数据集，使每个功能在 optimize/validation/final_test 都有独立案例，并覆盖缺参、否定/改口、多操作。
4. 完整版技能不再故意缺参数；优化练习如需故障应显式使用独立变体，不能污染可直接参考的标准技能。
5. 检查包导入/导出、逐案例执行、完整协议评分与基线/优化生命周期，运行原有回归。
6. 保留已有版本和真实模型配置；以新版本提供扩展技能，避免清空历史。

验收时区分离线模拟器结果、真实模型抽查与真实图片处理服务：本次交付主要为前两层的路由和参数契约。
