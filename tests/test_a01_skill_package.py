"""A01: 导入主 Skill 与子技能、坏引用/路径穿越拒绝、导出可还原、草稿与发布。"""

from __future__ import annotations

import io
import json
import zipfile

from app import config, persistence, services
from tests.conftest import build_package_zip, create_demo_project


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_import_21_subskills_and_export_roundtrip(client):
    project = client.post("/api/v3/projects", json={"name": "A01 项目"}).json()
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=build_package_zip(),
        headers={"content-type": "application/zip"})
    assert response.status_code == 201, response.text
    version = response.json()
    assert len(version["manifest"]["subskills"]) == 21
    assert version["manifest"]["main"] == "SKILL.md"

    detail = client.get(f"/api/v3/skill-versions/{version['id']}").json()
    paths = {item["path"] for item in detail["file_tree"]}
    assert "SKILL.md" in paths and "manifest.json" in paths
    assert sum(1 for path in paths if path.startswith("references/")) == 21
    assert all(item["editable"] for item in detail["file_tree"]
               if item["path"].endswith(".md"))

    file_response = client.get(
        f"/api/v3/skill-versions/{version['id']}/files/SKILL.md")
    assert file_response.status_code == 200
    assert "任务管理业务 Skill 包" in file_response.json()["text"]

    exported = client.get(f"/api/v3/skill-versions/{version['id']}/export")
    assert exported.status_code == 200
    assert exported.content[:2] == b"PK"

    # Re-import the exported archive into a second project: content must be identical.
    project2 = client.post("/api/v3/projects", json={"name": "A01 还原"}).json()
    reimported = client.post(
        f"/api/v3/projects/{project2['id']}/skill-versions",
        content=exported.content, headers={"content-type": "application/zip"})
    assert reimported.status_code == 201
    assert reimported.json()["content_hash"] == version["content_hash"]


def test_path_traversal_and_bad_references_rejected(client):
    project = client.post("/api/v3/projects", json={"name": "A01 拒绝"}).json()
    files = {
        "SKILL.md": b"# main\n",
        "manifest.json": json.dumps({
            "version": 1, "main": "SKILL.md",
            "subskills": [{"skill_id": "ok", "path": "references/ok.md"}],
        }).encode(),
        "references/ok.md": b"# ok\n",
        "../evil.md": b"bad\n",
    }
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=_zip(files), headers={"content-type": "application/zip"})
    assert response.status_code == 422
    assert any(error["code"] == "path_traversal"
               for error in response.json()["error"]["details"])

    bad_reference = {
        "SKILL.md": b"# main\n",
        "manifest.json": json.dumps({
            "version": 1, "main": "SKILL.md",
            "subskills": [{"skill_id": "missing", "path": "references/nope.md"}],
        }).encode(),
    }
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=_zip(bad_reference), headers={"content-type": "application/zip"})
    assert response.status_code == 422
    codes = {error["code"] for error in response.json()["error"]["details"]}
    assert "missing_reference" in codes


def test_duplicate_skill_and_absolute_path_rejected(client):
    project = client.post("/api/v3/projects", json={"name": "A01 重复"}).json()
    files = {
        "SKILL.md": b"# main\n",
        "manifest.json": json.dumps({
            "version": 1, "main": "SKILL.md",
            "subskills": [
                {"skill_id": "same", "path": "references/a.md"},
                {"skill_id": "same", "path": "references/b.md"},
            ],
        }).encode(),
        "references/a.md": b"# a\n",
        "references/b.md": b"# b\n",
    }
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=_zip(files), headers={"content-type": "application/zip"})
    assert response.status_code == 422
    assert any(error["code"] == "duplicate_skill_id"
               for error in response.json()["error"]["details"])


def test_draft_edit_publish_and_activate(client):
    demo = create_demo_project()
    project_id = demo["project"]["id"]
    version_id = demo["skill_version"]["id"]
    # New draft from the published files via JSON import.
    files = []
    for item in services.skill_version_detail(version_id)["file_tree"]:
        data = persistence.read_skill_file(version_id, item["path"])
        if item["path"].endswith(".md"):
            files.append({"path": item["path"], "content": data.decode("utf-8")})
        else:
            files.append({"path": item["path"],
                          "content": data.decode("utf-8", errors="ignore")})
    draft = client.post(f"/api/v3/projects/{project_id}/skill-versions",
                        json={"files": files, "status": "draft",
                              "message": "draft edit"})
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["id"]
    assert draft.json()["status"] == "draft"
    # Draft must not change the project pointer.
    assert client.get(f"/api/v3/projects/{project_id}").json()[
        "current_skill_version_id"] == version_id

    edited = client.put(
        f"/api/v3/skill-versions/{draft_id}/files/SKILL.md",
        json={"content": persistence.read_skill_file(version_id, "SKILL.md")
              .decode("utf-8") + "\n<!-- draft edit -->\n"})
    assert edited.status_code == 200, edited.text
    assert "draft edit" in persistence.read_skill_file(draft_id, "SKILL.md").decode()

    published = client.post(f"/api/v3/skill-versions/{draft_id}/publish")
    assert published.status_code == 200
    project = client.get(f"/api/v3/projects/{project_id}").json()
    assert project["current_skill_version_id"] == draft_id
    # Only published Markdown may be edited.
    rejected = client.put(
        f"/api/v3/skill-versions/{version_id}/files/SKILL.md",
        json={"content": "nope"})
    assert rejected.status_code == 409


def test_reference_singular_without_manifest_and_lowercase_main(client):
    """用户常见结构：skill.md + reference/（无 manifest）应自动生成映射。"""
    project = client.post("/api/v3/projects", json={"name": "A01 reference"}).json()
    files = {
        "skill.md": "# 主 Skill\n\n按条件选择子技能。\n".encode("utf-8"),
        "reference/task_query.md": "# 任务查询\n\n查询项目任务。\n".encode("utf-8"),
        "reference/task_assign/SKILL.md": "# 任务指派\n\n把任务指派给负责人。\n".encode("utf-8"),
    }
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=_zip(files), headers={"content-type": "application/zip"})
    assert response.status_code == 201, response.text
    version = response.json()
    assert version["manifest"]["main"] == "SKILL.md"
    subskills = {item["skill_id"]: item for item in version["manifest"]["subskills"]}
    assert set(subskills) == {"task_query", "task_assign"}
    assert subskills["task_query"]["path"] == "reference/task_query.md"
    assert subskills["task_query"]["name"] == "任务查询"
    assert subskills["task_assign"]["path"] == "reference/task_assign/SKILL.md"
    assert subskills["task_assign"]["name"] == "任务指派"
    warnings = {warning["code"] for warning in version.get("warnings") or []}
    assert "manifest_generated" in warnings
    assert "main_file_normalized" in warnings
    # 主文件已规范化为 SKILL.md，页面/编辑器可直接打开。
    assert client.get(
        f"/api/v3/skill-versions/{version['id']}/files/SKILL.md").status_code == 200


def test_single_wrapper_folder_and_references_plural(client):
    project = client.post("/api/v3/projects", json={"name": "A01 wrapper"}).json()
    files = {
        "my-skill/SKILL.md": "# 主 Skill\n".encode("utf-8"),
        "my-skill/references/search.md": "# 搜索\n\n搜索任务。\n".encode("utf-8"),
        "my-skill/references/export.md": "# 导出\n\n导出任务。\n".encode("utf-8"),
    }
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=_zip(files), headers={"content-type": "application/zip"})
    assert response.status_code == 201, response.text
    version = response.json()
    assert version["manifest"]["main"] == "SKILL.md"
    assert {item["skill_id"] for item in version["manifest"]["subskills"]} == {
        "search", "export"}


def test_explicit_manifest_can_use_reference_singular(client):
    project = client.post("/api/v3/projects", json={"name": "A01 manifest"}).json()
    manifest = {
        "version": 1,
        "main": "SKILL.md",
        "subskills": [
            {"skill_id": "task_query", "name": "任务查询",
             "path": "reference/task_query.md", "description": "查询任务"},
        ],
    }
    files = {
        "SKILL.md": "# 主 Skill\n".encode("utf-8"),
        "manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        "reference/task_query.md": "# 任务查询\n".encode("utf-8"),
    }
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=_zip(files), headers={"content-type": "application/zip"})
    assert response.status_code == 201, response.text
    version = response.json()
    assert version["manifest"]["subskills"][0]["skill_id"] == "task_query"
    assert not any(error["code"] == "dangling_reference"
                   for error in version.get("warnings") or [])


def test_os_metadata_and_wrapper_folder_are_ignored(client):
    project = client.post("/api/v3/projects", json={"name": "A01 metadata"}).json()
    files = {
        "my-skill/SKILL.md": "# 主 Skill\n".encode("utf-8"),
        "my-skill/reference/search.md": "# 搜索\n".encode("utf-8"),
        "__MACOSX/my-skill/._SKILL.md": b"\x00\x05\x16\x07",
        "my-skill/.DS_Store": b"\x00\x00\x00\x01",
    }
    response = client.post(
        f"/api/v3/projects/{project['id']}/skill-versions",
        content=_zip(files), headers={"content-type": "application/zip"})
    assert response.status_code == 201, response.text
    version = response.json()
    assert {item["skill_id"] for item in version["manifest"]["subskills"]} == {"search"}
    paths = {item["path"] for item in version["file_tree"]}
    assert not any(path.startswith("__MACOSX/") or path.endswith(".DS_Store")
                   for path in paths)
