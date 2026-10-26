from __future__ import annotations

import pytest

from app import persistence, services
from app.errors import ConflictError, ValidationError


def test_clone_edit_preserves_old_dataset_and_uses_hash_cas():
    project = persistence.create_project("dataset edit")
    cases = [{"case_id": "case_a", "query": "hello", "context": {},
              "expected": {"action": "no_action"}, "fixtures": [], "split": "optimize",
              "group_id": "g1", "tags": ["critical"], "validation": {}}]
    old = persistence.create_dataset(project["id"], cases, set_current=True)
    draft = services.create_dataset_draft(old["id"])
    assert draft["status"] == "draft" and persistence.get_project(project["id"])["current_dataset_id"] == old["id"]
    row = persistence.get_case(draft["id"], "case_a")
    row["query"] = "edited"
    changed = services.update_dataset_case(draft["id"], row, draft["hash"], "case_a")
    assert changed["case"]["query"] == "edited"
    assert persistence.get_case(old["id"], "case_a")["query"] == "hello"


def test_edit_stale_hash_and_published_are_rejected():
    project = persistence.create_project("dataset edit conflict")
    cases = [{"case_id": "case_a", "query": "hello", "context": {}, "expected": {},
              "fixtures": [], "split": "optimize", "group_id": "", "tags": [], "validation": {}}]
    old = persistence.create_dataset(project["id"], cases, set_current=True)
    with pytest.raises(ConflictError):
        services.update_dataset_case(old["id"], cases[0], old["hash"], "case_a")
    draft = services.create_dataset_draft(old["id"])
    with pytest.raises(ConflictError):
        services.update_dataset_case(draft["id"], cases[0], "stale", "case_a")


def test_publish_success_and_pointer_conflict_rolls_back():
    project = persistence.create_project("dataset publish")
    cases = [{"case_id": "case_a", "query": "hello", "context": {}, "expected": {},
              "fixtures": [], "split": "optimize", "group_id": "", "tags": [], "validation": {}}]
    old = persistence.create_dataset(project["id"], cases, set_current=True)
    draft = services.create_dataset_draft(old["id"])
    published = services.publish_dataset_version(draft["id"], {
        "expected_hash": draft["hash"], "expected_current_version": old["id"], "set_current": True})
    assert published["status"] == "published"
    assert persistence.get_project(project["id"])["current_dataset_id"] == draft["id"]

    next_draft = services.create_dataset_draft(draft["id"])
    with pytest.raises(ConflictError):
        services.publish_dataset_version(next_draft["id"], {
            "expected_hash": next_draft["hash"], "expected_current_version": old["id"], "set_current": True})
    assert persistence.get_dataset(next_draft["id"])["status"] == "draft"


def test_ungrouped_cases_can_use_different_splits():
    project = persistence.create_project("dataset groups")
    cases = [
        {"case_id": "a", "query": "a", "context": {}, "expected": {}, "fixtures": [],
         "split": "optimize", "group_id": "", "tags": [], "validation": {}},
        {"case_id": "b", "query": "b", "context": {}, "expected": {}, "fixtures": [],
         "split": "validation", "group_id": "", "tags": [], "validation": {}},
    ]
    old = persistence.create_dataset(project["id"], cases, set_current=True)
    draft = services.create_dataset_draft(old["id"])
    row = persistence.get_case(draft["id"], "a")
    row["query"] = "normalized"
    result = services.update_dataset_case(draft["id"], row, draft["hash"], "a")
    assert result["case"]["query"] == "normalized"
