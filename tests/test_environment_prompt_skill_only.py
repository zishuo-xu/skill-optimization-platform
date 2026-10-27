"""The environment contract contains Prompt and Skill runtime settings only."""

from app import db, persistence, services
from tests.conftest import environment_payload


def test_environment_can_publish_without_tool_schema_and_does_not_serialize_one():
    project = persistence.create_project("prompt-skill-only")
    payload = environment_payload()
    payload.pop("tool_schema", None)

    environment = services.create_environment(
        project["id"], payload, status="published", set_current=True)

    assert "tool_schema" not in environment
    assert persistence.get_environment(environment["id"]).get("tool_schema") is None
    columns = {row[1] for row in db.get_connection().execute(
        "PRAGMA table_info(environments)").fetchall()}
    assert "tool_schema_json" not in columns
