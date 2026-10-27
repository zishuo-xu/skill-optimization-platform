"""Build the complete, protocol-level image sample dataset.

The generated JSONL is intentionally independent of the demo adapter: every
tool expectation contains method, fixed script, step-scoped query, step indices, and args. All inputs are synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "samples" / "image_dataset.jsonl"
SCRIPT = "handlers/photo_ops.py"

CASES = [
    ("image_one_word_fix", "retouch_from_text", "帮我让人物更精神", {"instruction": "帮我让人物更精神"}),
    ("image_color_transfer", "recolor_region", "把天空调成蓝色", {"targetColor": "蓝色", "region": "sky", "preserveTexture": True}),
    ("image_background_remove", "cutout_subject", "去掉照片背景，保留人物", {"keepSubject": True, "outputBackground": "transparent"}),
    ("image_enhance", "enhance_detail", "夜景太暗，帮我提亮", {"enhanceType": "lighting", "strength": 0.8}),
    ("image_crop", "crop_frame", "裁成竖版 9:16，主体构图", {"aspectRatio": "9:16", "focus": "subject"}),
    ("image_transform", "transform_frame", "顺时针旋转 90 度并水平翻转", {"angle": 90, "flip": "horizontal"}),
    ("image_resize", "resize_frame", "宽度改成 1200，保持比例", {"width": 1200, "keepAspectRatio": True}),
    ("image_tone_adjust", "adjust_tone", "亮度提高 30，饱和度降低 10", {"brightness": 30, "saturation": -10}),
    ("image_filter", "apply_preset", "加 0.7 强度的胶片滤镜", {"filterName": "film", "strength": 0.7}),
    ("image_portrait_retouch", "retouch_portrait", "自然美颜", {"smoothSkin": 0.3, "whiten": 0.2}),
    ("image_object_remove", "erase_object", "移除左下角的路人", {"target": "左下角的路人", "preserveBackground": True}),
    ("image_background_replace", "replace_backdrop", "把背景换成海边，保留人物", {"background": "海边", "keepSubject": True}),
]

VARIANTS = {
    "image_one_word_fix": ("帮我把这张照片修得自然一点", {"instruction": "帮我把这张照片修得自然一点"}),
    "image_color_transfer": ("整张图换成暖色并保持细节", {"targetColor": "暖色", "region": "entire", "preserveTexture": True}),
    "image_background_remove": ("抠图，输出透明背景", {"keepSubject": True, "outputBackground": "transparent"}),
    "image_enhance": ("把模糊的照片变清晰", {"enhanceType": "clarity", "strength": 0.8}),
    "image_crop": ("裁成方形 1:1，居中", {"aspectRatio": "1:1", "focus": "center"}),
    "image_transform": ("上下翻转图片", {"angle": 0, "flip": "vertical"}),
    "image_resize": ("高度调整为 800", {"height": 800, "keepAspectRatio": True}),
    "image_tone_adjust": ("增加曝光 1", {"exposure": 1}),
    "image_filter": ("套复古滤镜", {"filterName": "vintage", "strength": 0.5}),
    "image_portrait_retouch": ("轻微磨皮和瘦脸", {"smoothSkin": 0.2, "slimFace": 0.15}),
    "image_object_remove": ("删除右侧的电线杆", {"target": "右侧的电线杆", "preserveBackground": True}),
    "image_background_replace": ("换成纯白背景", {"background": "纯白", "keepSubject": True}),
}

VALIDATION = {
    "image_one_word_fix": ("让人物的笑容自然一点", {"instruction": "让人物的笑容自然一点"}),
    "image_color_transfer": ("把衣服换成绿色", {"targetColor": "绿色", "region": "clothing", "preserveTexture": True}),
    "image_background_remove": ("去除商品图的底色，主体保持完整", {"keepSubject": True, "outputBackground": "transparent"}),
    "image_enhance": ("给模糊照片做降噪", {"enhanceType": "denoise", "strength": 0.8}),
    "image_crop": ("按横版 16:9 裁剪，居中取景", {"aspectRatio": "16:9", "focus": "center"}),
    "image_transform": ("把图片逆时针转 90 度", {"angle": -90, "flip": "none"}),
    "image_resize": ("把高度缩放到 600 并保持比例", {"height": 600, "keepAspectRatio": True}),
    "image_tone_adjust": ("降低对比度 15", {"contrast": -15}),
    "image_filter": ("应用冷色滤镜", {"filterName": "cool", "strength": 0.5}),
    "image_portrait_retouch": ("给人像轻微美白", {"whiten": 0.1}),
    "image_object_remove": ("去掉画面中的水印", {"target": "画面中的水印", "preserveBackground": True}),
    "image_background_replace": ("将原背景替换为办公室", {"background": "办公室", "keepSubject": True}),
}

FINAL = {
    "image_one_word_fix": ("把人物肤色修得均匀自然", {"instruction": "把人物肤色修得均匀自然"}),
    "image_color_transfer": ("只把现有背景调成紫色，保留纹理", {"targetColor": "紫色", "region": "background", "preserveTexture": True}),
    "image_background_remove": ("输出透明底的商品主体", {"keepSubject": True, "outputBackground": "transparent"}),
    "image_enhance": ("这张图噪点很多，帮我降噪", {"enhanceType": "denoise", "strength": 0.8}),
    "image_crop": ("裁成横向 4:3，主体优先", {"aspectRatio": "4:3", "focus": "subject"}),
    "image_transform": ("左右翻转照片", {"angle": 0, "flip": "horizontal"}),
    "image_resize": ("宽高都设为 1024，不保持比例", {"width": 1024, "height": 1024, "keepAspectRatio": False}),
    "image_tone_adjust": ("曝光降低 0.5", {"exposure": -0.5}),
    "image_filter": ("使用黑白滤镜，强度 0.8", {"filterName": "black_white", "strength": 0.8}),
    "image_portrait_retouch": ("轻微瘦脸", {"slimFace": 0.15}),
    "image_object_remove": ("擦除右上角的杂物", {"target": "右上角的杂物", "preserveBackground": True}),
    "image_background_replace": ("背景换成森林，保留主体", {"background": "森林", "keepSubject": True}),
}

# Background desaturation deliberately has no image identifiers or adjustable fields.
CASES.append(("image_background_decolor", "desaturate_backdrop", "让背景变为黑白，人物颜色不变", {}))
VARIANTS["image_background_decolor"] = ("主体突出，背景失去颜色", {})
VALIDATION["image_background_decolor"] = ("仅给背景去色，主体颜色照旧", {})
FINAL["image_background_decolor"] = ("背景灰度，主体维持彩色", {})


def call(method: str, query: str, args: dict) -> dict:
    return {"functionName": "editImage", "skillName": "sample-photo-editor", "arguments": {"method": method, "script": SCRIPT, "request": {"query": query, "totalSteps": 1, "currentStep": 1}, "args": args}}

def make(case_id: str, split: str, group: str, skill: str, method: str, query: str, args: dict) -> dict:
    return {"id": case_id, "query": query, "group_id": group, "split": split, "tags": [skill], "skill": skill, "tool": call(method, query, args), "mock": {"code": 0}}

def main() -> None:
    rows: list[dict] = []
    for skill, method, query, args in CASES:
        rows.append(make(f"{skill}_opt_1", "optimize", f"{skill}_opt", skill, method, query, args))
        query2, args2 = VARIANTS[skill]
        query_val, args_val = VALIDATION[skill]
        query_fin, args_fin = FINAL[skill]
        rows.append(make(f"{skill}_opt_2", "optimize", f"{skill}_opt", skill, method, query2, args2))
        rows.append(make(f"{skill}_val_1", "validation", f"{skill}_val", skill, method, query_val, args_val))
        rows.append(make(f"{skill}_fin_1", "final_test", f"{skill}_fin", skill, method, query_fin, args_fin))

    # Clarification, no-op, negation and bounded multi-intent coverage.
    rows.append({"id": "clarify_color", "query": "帮我换色", "group_id": "clarify_color", "split": "validation", "skill": "image_color_transfer", "action": "clarify", "missing": ["targetColor"], "mock": {"code": 0}})
    rows.append({"id": "clarify_resize", "query": "帮我调整尺寸", "group_id": "clarify_resize", "split": "validation", "skill": "image_resize", "action": "clarify", "missing": ["width_or_height"], "mock": {"code": 0}})
    rows.append({"id": "no_action_weather", "query": "今天北京天气怎么样", "group_id": "no_action_weather", "split": "final_test", "action": "no_action"})
    rows.append({"id": "negation_background", "query": "不要去背景，改成海边背景并保留人物", "group_id": "negation_background", "split": "validation", "skill": "image_background_replace", "tool": call("replace_backdrop", "不要去背景，改成海边背景并保留人物", {"background": "海边", "keepSubject": True}), "mock": {"code": 0}})
    rows.append({"id": "multi_color_enhance", "query": "先把天空调蓝，再提亮照片", "group_id": "multi_color_enhance", "split": "final_test", "skill": ["image_color_transfer", "image_enhance"], "route_ordered": True, "tools": [call("recolor_region", "先把天空调蓝，再提亮照片", {"targetColor": "蓝色", "region": "sky", "preserveTexture": True}), call("enhance_detail", "先把天空调蓝，再提亮照片", {"enhanceType": "lighting", "strength": 0.8})], "mock": {"code": 0}})
    rows.append({"id": "multi_crop_resize", "query": "裁成 1:1 后把宽度改成 800", "group_id": "multi_crop_resize", "split": "final_test", "skill": ["image_crop", "image_resize"], "route_ordered": True, "tools": [call("crop_frame", "裁成 1:1 后把宽度改成 800", {"aspectRatio": "1:1", "focus": "subject"}), call("resize_frame", "裁成 1:1 后把宽度改成 800", {"width": 800, "keepAspectRatio": True})], "mock": {"code": 0}})
    rows.append({"id": "multi_three_ops", "query": "先裁成 1:1，再调成暖色，最后加胶片滤镜", "group_id": "multi_three_ops", "split": "final_test", "skill": ["image_crop", "image_color_transfer", "image_filter"], "route_ordered": True, "tools": [call("crop_frame", "先裁成 1:1，再调成暖色，最后加胶片滤镜", {"aspectRatio": "1:1", "focus": "subject"}), call("recolor_region", "先裁成 1:1，再调成暖色，最后加胶片滤镜", {"targetColor": "暖色", "region": "entire", "preserveTexture": True}), call("apply_preset", "先裁成 1:1，再调成暖色，最后加胶片滤镜", {"filterName": "film", "strength": 0.5})], "mock": {"code": 0}})
    rows.append({"id": "clarify_object", "query": "帮我移除一个东西", "group_id": "clarify_object", "split": "validation", "skill": "image_object_remove", "action": "clarify", "missing": ["target"], "mock": {"code": 0}})
    rows.append({"id": "clarify_crop", "query": "帮我裁剪一下", "group_id": "clarify_crop", "split": "validation", "skill": "image_crop", "action": "clarify", "missing": ["aspectRatio"], "mock": {"code": 0}})
    rows.append({"id": "clarify_filter", "query": "加个滤镜", "group_id": "clarify_filter", "split": "validation", "skill": "image_filter", "action": "clarify", "missing": ["filterName"], "mock": {"code": 0}})
    rows.append({"id": "clarify_transform", "query": "变换一下图片", "group_id": "clarify_transform", "split": "validation", "skill": "image_transform", "action": "clarify", "missing": ["angle_or_flip"], "mock": {"code": 0}})
    rows.append({"id": "clarify_resize_range", "query": "宽度改成 10000", "group_id": "clarify_resize_range", "split": "validation", "skill": "image_resize", "action": "clarify", "missing": ["width"], "mock": {"code": 0}})
    rows.append({"id": "no_action_unsupported", "query": "把照片做成 3D 动画", "group_id": "no_action_unsupported", "split": "final_test", "action": "no_action"})
    rows.append({"id": "clarify_too_many_operations", "query": "裁剪、换色、提亮、加滤镜、去背景", "group_id": "clarify_too_many_operations", "split": "validation", "action": "clarify", "missing": ["operations"], "mock": {"code": 0}})
    # Explicit expectations are authored independently of the rule adapter.
    scoped = {
        "negation_background": ["改成海边背景并保留人物"],
        "multi_color_enhance": ["先把天空调蓝", "提亮照片"],
        "multi_crop_resize": ["裁成 1:1", "把宽度改成 800"],
        "multi_three_ops": ["先裁成 1:1", "调成暖色", "加胶片滤镜"],
    }
    for row in rows:
        calls = row.get("tools") or ([row["tool"]] if "tool" in row else [])
        for index, tool in enumerate(calls, 1):
            request = tool["arguments"]["request"]
            request.update(totalSteps=len(calls), currentStep=index)
            if row["id"] in scoped:
                request["query"] = scoped[row["id"]][index - 1]

    # Each capability has a drag-source case and a nonzero-result case. These
    # share the optimize group with their source so paraphrases cannot leak.
    for skill, method, query, args in CASES:
        row = make(f"{skill}_drag", "optimize", f"{skill}_opt", skill, method, query, args)
        row["context"] = {"source": "drag"}
        row["tool"]["arguments"]["request"]["source"] = "drag"
        row["tags"].append("drag_source")
        rows.append(row)
        row = make(f"{skill}_failure", "optimize", f"{skill}_opt", skill, method, query, args)
        row["mock"] = {"code": 23, "message": "示例服务暂时不可用"}
        row["tags"].append("business_failure")
        rows.append(row)

    boundaries = [
        ("enhance_zero", "image_enhance", "enhance_detail", "增强画质，强度 0", {"enhanceType": "auto", "strength": 0}),
        ("filter_percent", "image_filter", "apply_preset", "加暖色滤镜，强度 40%", {"filterName": "warm", "strength": 0.4}),
        ("gray_filter", "image_filter", "apply_preset", "整张照片加灰调滤镜", {"filterName": "black_white", "strength": 0.5}),
        ("portrait_explicit", "image_portrait_retouch", "retouch_portrait", "磨皮 0.6，美白 20%", {"smoothSkin": 0.6, "whiten": 0.2}),
        ("resize_min", "image_resize", "resize_frame", "宽度设为 1", {"width": 1, "keepAspectRatio": True}),
        ("resize_max", "image_resize", "resize_frame", "高度设为 8192", {"height": 8192, "keepAspectRatio": True}),
        ("tone_bounds", "image_tone_adjust", "adjust_tone", "亮度提高 100，曝光降低 5", {"brightness": 100, "exposure": -5}),
        ("transform_negative", "image_transform", "transform_frame", "逆时针旋转 180 度", {"angle": -180, "flip": "none"}),
        ("decolor_not_filter", "image_background_decolor", "desaturate_backdrop", "不要整图黑白滤镜，只给背景去色", {}),
        ("filter_not_decolor", "image_filter", "apply_preset", "不要背景去色，改用黑白滤镜", {"filterName": "black_white", "strength": 0.5}),
    ]
    for key, skill, method, query, args in boundaries:
        row = make(key, "final_test", key, skill, method, query, args)
        if key == "decolor_not_filter":
            row["tool"]["arguments"]["request"]["query"] = "只给背景去色"
        elif key == "filter_not_decolor":
            row["tool"]["arguments"]["request"]["query"] = "改用黑白滤镜"
        rows.append(row)
    invalids = [
        ("enhance_over", "image_enhance", "增强画质，强度 120%", ["strength"]),
        ("filter_negative", "image_filter", "胶片滤镜，强度 -0.1", ["strength"]),
        ("portrait_over", "image_portrait_retouch", "磨皮 1.2", ["smoothSkin"]),
        ("crop_unknown", "image_crop", "裁成 5:7", ["aspectRatio"]),
        ("rotate_unsupported", "image_transform", "旋转 45 度", ["angle", "angle_or_flip"]),
        ("resize_zero", "image_resize", "宽度设为 0", ["width"]),
        ("resize_fraction", "image_resize", "宽度设为 320.5", ["width_or_height"]),
        ("tone_over", "image_tone_adjust", "曝光提高 6", ["exposure", "tone_value"]),
        ("tone_missing", "image_tone_adjust", "调一下亮度", ["tone_value"]),
        ("replace_missing", "image_background_replace", "帮我换背景", ["background"]),
    ]
    for key, skill, query, missing in invalids:
        rows.append({"id": key, "query": query, "group_id": key, "split": "validation",
                     "skill": skill, "action": "clarify", "missing": missing})
    rows.append({"id": "blur_not_decolor", "query": "让背景模糊一些", "group_id": "blur_not_decolor",
                 "split": "final_test", "action": "no_action"})
    for key, source in (("source_click", "click"), ("source_empty", "")):
        row = make(key, "validation", key, "image_background_decolor", "desaturate_backdrop", "背景黑白", {})
        row["context"] = {"source": source}
        rows.append(row)
    query = "先让背景黑白，再加胶片滤镜"
    operations = [call("desaturate_backdrop", "先让背景黑白", {}),
                  call("apply_preset", "加胶片滤镜", {"filterName": "film", "strength": 0.5})]
    for index, tool in enumerate(operations, 1):
        tool["arguments"]["request"].update(totalSteps=2, currentStep=index)
    rows.append({"id": "multi_decolor_filter", "query": query, "group_id": "multi_decolor_filter",
                 "split": "final_test", "skill": ["image_background_decolor", "image_filter"],
                 "route_ordered": True, "tools": operations, "mock": {"code": 0}})
    rows.append({"id": "multi_failure_stop", "query": query, "group_id": "multi_decolor_filter",
                 "split": "final_test", "skill": ["image_background_decolor", "image_filter"],
                 "route_ordered": True, "tools": operations[:1],
                 "mock": {"code": 9, "message": "示例输入不可处理"}})
    OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"wrote {len(rows)} cases to {OUT}")

if __name__ == "__main__":
    main()
