# 修图 Skill：语流路由与参数契约扩展

## 目标

主 Skill 根据用户最终有效意图分发子技能；子技能把口语表达转换为固定工作流参数。
保留一个 `editImage` 业务工具，通过 method 区分修图功能。
不在主 Skill 堆放参数细节，不使用最终自然语言回复质量代替路由与参数评测。
工具、方法与脚本路径为本仓库的合成协议标识，未对接真实图片服务。

## 职责

- 主 Skill：语流归一、改口/否定/歧义处理、功能边界、必要 reference 路由、多意图顺序。
- 子技能：method、字段类型/范围/枚举、默认值、自然语言映射、澄清条件、正反例与调用示例。
- 工具包装：method、固定 script、request.query 当前操作片段，request.totalSteps/currentStep 步骤编号、args 由对应子技能产生。
- 业务选择格式使用 `{"path":"references/<子技能>.md","skillName":"sample-photo-editor"}`。
- 业务调用格式使用 `{"functionName":"editImage","skillName":"sample-photo-editor","arguments":{"method":"...","script":"handlers/photo_ops.py","request":{"query":"当前步骤相关原话","totalSteps":1,"currentStep":1},"args":{}}}`。
- 平台内部 select_skill 将 manifest 稳定 ID 映射到业务 path 并返回所选正文；read_skill 可按需读取其他 reference，不需为所选正文再读一次。内部观测协议不替代上述业务格式。
- 模拟器：读取技能正文中的契约后确定性演示，不读取数据集 expected，不等于真实模型。
- 数据集：完整比较工具调用（包括 method/script/request/args），包含口语、负例、澄清与多操作。

## 功能契约

| skill_id | method | args |
| --- | --- | --- |
| image_one_word_fix | retouch_from_text | instruction: string，保留独立的一句话修图诉求 |
| image_color_transfer | recolor_region | targetColor: string 必填；region: entire/sky/subject/background/clothing 默认 entire；preserveTexture: boolean 默认 true |
| image_enhance | enhance_detail | enhanceType: lighting/clarity/denoise/auto；strength: number 0..1 默认 0.8 |
| image_crop | crop_frame | aspectRatio: 1:1/4:3/3:4/16:9/9:16 必填；focus: center/subject 默认 subject |
| image_transform | transform_frame | angle: -180/-90/0/90/180 默认 0（顺时针为正）；flip: none/horizontal/vertical 默认 none，必须有有效变换 |
| image_resize | resize_frame | width/height: integer 1..8192，至少一个；keepAspectRatio: boolean，单边尺寸为 true，显式双边尺寸为 false |
| image_tone_adjust | adjust_tone | brightness/contrast/saturation: integer -100..100；exposure: number -5..5；只传用户明确指定的字段和值 |
| image_filter | apply_preset | filterName: film/black_white/vintage/warm/cool 必填；strength: number 0..1 默认 0.5 |
| image_background_decolor | desaturate_backdrop | 无可调业务参数，args={}；只让背景灰度化，主体颜色保留 |
| image_style | query_style_version → apply_style | 查询 args={}；应用的 template/intent 及调节字段按返回版本选取，不支持则转一句话修图 |

每个 reference 在 `## 参数契约` 下提供 JSON 对象：`method`、`fields`（JSON Schema properties）、`required`、`defaults`、`mappings`（按字段组织的短语→协议值）。
此块与参数表/示例应一致，供模型阅读并让离线模拟器读取；不包含案例 ID 或答案表。

## 语流规则

- 使用语义而非机械关键词做路由；停顿词不构成新操作。
- “不要 X，只做 Y”“不是 X，改成 Y”以最后确认的有效意图为准；只将该步骤相关原话写入 request.query。
- 专用技能优先于一句话兜底；风格不等于滤镜，改变颜色不等于滤镜，尺寸不等于裁剪。
- 多操作最多 3 项，按用户顺序；同一技能的兼容字段合并，完整核对必填项后再执行任何业务调用。
- 缺颜色/目标物/尺寸等必要信息时澄清，不以默认值替代缺失的用户意图；仅允许使用 reference 明确声明的默认值。
- 未指向修图的普通问答为 no_action；范围外的图像能力不编造 method。

## 实施与验收

1. 保持 manifest、10 个 references、业务 Prompt 和内嵌参数契约一致。
2. 更新离线模拟器，让其读取子技能契约，支持代表性的语流/复合任务；保持 demo 标识。
3. 扩展数据集，使每个功能在 optimize/validation/final_test 都有独立案例，并覆盖缺参、否定/改口、多操作。
4. 完整版技能不再故意缺参数；优化练习如需故障应显式使用独立变体，不能污染可直接参考的标准技能。
5. 检查包导入/导出、逐案例执行、完整协议评分与基线/优化生命周期，运行原有回归。
6. 保留已有版本和真实模型配置；以新版本提供扩展技能，避免清空历史。

验收时区分离线模拟器结果、真实模型抽查与真实图片处理服务；当前版本的真实模型抽查和图片处理服务尚待验证。

## 当前版本：九个直接编辑与一个版本分支（2026-10-29）

现有示例调整为 10 个子技能，manifest.version=4。只在质感风格的 reference 内声明查询及版本表；其余九个 reference 不执行版本查询。明确风格仍先查版本：例如黑白只受版本 2～4 支持，柔光只受版本 4 支持。参数越界截到该版本边界，不支持的参数/预设改走一句话修图；未知版本、查询失败停止，不能假设设备能力。

本版包含 112 条案例（45 日常 / 32 独立验收 / 35 最终测试）。风格案例分别提供 query_style_version、apply_style 或替代编辑的 fixture，预期包括完整有序调用。版本信息只通过查询返回提供；查询不增加用户操作计数，不与编辑在同一批调用发出。模拟器读取 reference 的版本表与实际返回，测试另外覆盖同一请求换版本后选参变化和缺查询 fixture 时保留真实失败。

版本化子技能借鉴了生产流程形态，但文案和接口重新编写，不包含生产原文或图片标识。现有本地项目通过 `python -m app.seed --refresh-image` 发布新版本，保留模型配置和旧运行。旧版示例的其他子技能仍可在历史包里回放；不修改它们的历史评分。

## 历史公开合成版（2026-10-27）

13 个子技能均已改写：使用边界、机器可读参数契约、执行约束、完整调用样例、易混意图。工具名为 `editImage`，技能名为 `sample-photo-editor`，脚本标识为 `handlers/photo_ops.py`；这些是虚构教学协议，没有附带生产接口映射。

评测只关注路由、业务参数和请求步骤信息，不收集或断言图片定位符。背景去色没有业务可调参数，因此 args 为空对象有效；顶层 arguments 仍包含四个必填键。

request.query 不能重复塞入整个复合请求。每次调用都带 totalSteps/currentStep；从上下文读取 source，用户结构化消息显式提供该字段时覆盖；最终 source 为 drag 时才附带同值，其他来源省略。逐步调用并检查 code，数字 0 才可继续；非零、缺失或异常状态码立即结束，不重试已成功步骤。返回 message 只作为错误信息展示。

数据集共 117 条：52 优化、34 验证、31 最终测试。每个子技能均包含独立的三个分组样例，以及同组的来源标记和失败返回变体。额外覆盖数值上下界、百分比换算、缺参、非法枚举、显式人像强度、背景去色与滤镜/虚化的区分，以及复合任务中断。

严格参数评分仍以完整调用为准；最终文案、异常状态码及失败后停止需要通过运行时测试额外验证，不能将参数通过率称为真实修图成功率。确定性模拟器没有读取数据集 expected；不代表真实模型泛化能力或图片质量。旧版已记录的真实模型抽查结果不适用于本版。

本修图示例使用合成标识。更新现有本地示例时可使用 --refresh-image 保留历史并发布新样例版本；对接真实网关需适配本合成协议。
