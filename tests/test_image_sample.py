"""Second demo project: progressive image-editing Skill package and compact data."""

from __future__ import annotations

import json
from pathlib import Path

from app import dataset
from app.adapters.registry import build_adapter


def _samples() -> Path:
    return Path(__file__).resolve().parent.parent / "app" / "samples"


def test_image_sample_dataset_is_valid_compact_input():
    samples = _samples()
    items, fmt = dataset.parse_payload(
        (samples / "image_dataset.jsonl").read_bytes(), "image_dataset.jsonl")
    manifest = json.loads(
        (samples / "image_skill_package" / "manifest.json").read_text(encoding="utf-8"))
    tools = json.loads((samples / "image_tool_schema.json").read_text(encoding="utf-8"))
    result = dataset.validate_and_prepare(
        items, manifest=manifest, tool_schema=tools,
        split_config={"optimize": 0.7, "validation": 0.2, "final_test": 0.1},
        seed=42)
    assert fmt == "jsonl"
    assert result["errors"] == []
    assert result["stats"]["valid"] == 15
    assert result["stats"]["splits"] == {
        "optimize": 9, "validation": 4, "final_test": 2}
    actions = {case["expected"]["action"] for case in result["cases"]}
    assert actions == {"tool_call", "clarify"}
    assert {case["expected"]["routes"][0][0] for case in result["cases"]} == {
        "image_one_word_fix", "image_color_transfer",
        "image_background_remove", "image_enhance",
    }


def test_image_model_config_builds_image_demo_adapter():
    adapter = build_adapter({"provider": "demo_scripted", "model": "demo-image-v1",
                             "domain": "image"})
    assert type(adapter).__name__ == "DemoImageScriptedAdapter"
    task_adapter = build_adapter({"provider": "demo_scripted"})
    assert type(task_adapter).__name__ == "DemoScriptedAdapter"
