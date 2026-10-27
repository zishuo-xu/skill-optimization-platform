import json

import pytest

from app import persistence, services
from app.errors import ValidationError


def case(name, split=None):
    row = {"case_id": name, "query": name, "expected": {"action": "no_action"}}
    if split is not None:
        row["split"] = split
    return row


@pytest.mark.parametrize("source,target", [("validation", "optimize"), ("optimize", "validation")])
def test_explicit_purpose_preflight_matches_publication(client, source, target):
    project = persistence.create_project("purpose")
    payload = {"filename": "cases.json", "content": json.dumps([case("a", source), case("b")]),
               "import_purpose": target,
               "split_config": {"optimize": 0.5, "validation": 0.5, "final_test": 0}}
    report = services.preflight_dataset(project["id"], payload)
    published = services.create_dataset(project["id"], payload)
    assert {c["split"] for c in report["cases"]} == {target}
    assert {c["case_id"]: c["split"] for c in report["cases"]} == {
        c["case_id"]: c["split"] for c in persistence.all_cases(published["id"])}
    assert published["split_config"] == {k: int(k == target) for k in ("optimize", "validation", "final_test")}


def test_preserve_and_legacy_keep_file_splits(client):
    payload = {"cases": [case("a", "validation"), case("b", "final_test")],
               "split_config": {"optimize": 1, "validation": 0, "final_test": 0}}
    for purpose in (None, "preserve"):
        data = dict(payload)
        if purpose:
            data["import_purpose"] = purpose
        report = services.preflight_dataset(None, data)
        assert [c["split"] for c in report["cases"]] == ["validation", "final_test"]


@pytest.mark.parametrize("purpose", ["optimize", "validation"])
def test_final_test_requires_preserve_in_both_paths(client, purpose):
    project = persistence.create_project("protected")
    payload = {"cases": [case("a", "final_test")], "import_purpose": purpose, "exclude_invalid": True}
    for invoke in (lambda: services.preflight_dataset(project["id"], payload),
                   lambda: services.create_dataset(project["id"], payload)):
        with pytest.raises(ValidationError, match="保留文件划分") as error:
            invoke()
        assert error.value.code == "final_test_requires_preserve"


@pytest.mark.parametrize("purpose", ["bad", None, [], 1])
def test_invalid_purpose(client, purpose):
    with pytest.raises(ValidationError) as error:
        services.preflight_dataset(None, {"cases": [case("a")], "import_purpose": purpose})
    assert error.value.field_path == "import_purpose"


def test_bad_rows_remain_reported(client):
    report = services.preflight_dataset(None, {"filename": "cases.jsonl", "content":
        json.dumps(case("a", "validation")) + '\n{broken\n', "import_purpose": "optimize"})
    assert report["cases"][0]["split"] == "optimize"
    assert report["errors"][0]["code"] == "parse_error"
    report = services.preflight_dataset(None, {"cases": [case("a"), 42], "import_purpose": "optimize"})
    assert report["errors"][0]["code"] == "invalid_case"
