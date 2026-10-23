from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.errors import WorkspaceError
from app.infrastructure.workspace.web import WebWorkspace


@pytest.fixture
def workspace(tmp_path):
    return WebWorkspace(tmp_path, node_binary="definitely-missing-node")


async def test_write_read_and_list_are_scoped(workspace: WebWorkspace) -> None:
    project_id = uuid4()
    await workspace.write_file(project_id, "index.html", "<h1>hi</h1>", {"index.html"})
    assert await workspace.read_file(project_id, "index.html") == "<h1>hi</h1>"
    assert await workspace.list_files(project_id) == ["index.html"]
    assert await workspace.read_all(project_id) == {"index.html": "<h1>hi</h1>"}


async def test_project_workspaces_keep_files_isolated(workspace: WebWorkspace) -> None:
    older_project, other_project = uuid4(), uuid4()
    await workspace.write_file(older_project, "index.html", "older", {"index.html"})
    await workspace.write_file(other_project, "index.html", "other", {"index.html"})

    assert await workspace.read_all(older_project) == {"index.html": "older"}
    assert await workspace.read_all(other_project) == {"index.html": "other"}


async def test_project_files_survive_workspace_adapter_recreation(workspace: WebWorkspace) -> None:
    project_id = uuid4()
    await workspace.write_file(project_id, "index.html", "accepted", {"index.html"})

    restarted = WebWorkspace(workspace.root, node_binary="definitely-missing-node")

    assert await restarted.read_all(project_id) == {"index.html": "accepted"}


async def test_write_outside_plan_is_refused(workspace: WebWorkspace) -> None:
    project_id = uuid4()
    with pytest.raises(WorkspaceError):
        await workspace.write_file(project_id, "other.txt", "x", {"index.html"})


async def test_non_plan_files_stay_unchanged(workspace: WebWorkspace) -> None:
    project_id = uuid4()
    await workspace.write_file(project_id, "keep.css", "a{}", {"keep.css"})
    await workspace.write_file(project_id, "index.html", "<p>1</p>", {"keep.css", "index.html"})
    await workspace.write_file(project_id, "index.html", "<p>2</p>", {"keep.css", "index.html"})
    assert await workspace.read_file(project_id, "keep.css") == "a{}"
    assert await workspace.read_file(project_id, "index.html") == "<p>2</p>"


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("../escape.txt", id="parent"),
        pytest.param("/absolute.txt", id="absolute"),
        pytest.param("a/../../escape.txt", id="nested-parent"),
    ],
)
async def test_path_traversal_is_rejected(workspace: WebWorkspace, path: str) -> None:
    with pytest.raises(WorkspaceError):
        await workspace.read_file(uuid4(), path)


async def test_check_readable_reports_missing_empty_and_types(workspace: WebWorkspace) -> None:
    project_id = uuid4()
    paths = {"index.html", "missing.css", "empty.css", "bad.exe"}
    await workspace.write_file(project_id, "index.html", "<h1>x</h1>", paths)
    await workspace.write_file(project_id, "empty.css", "", paths)
    await workspace.write_file(project_id, "bad.exe", "not allowed", paths)
    report = await workspace.check_readable(
        project_id, ["index.html", "missing.css", "empty.css", "bad.exe"]
    )

    assert not report.ok
    assert report.files_checked == 1
    assert report.issues == [
        "missing planned file: missing.css",
        "empty planned file: empty.css",
        "file type not allowed: bad.exe",
    ]


async def test_js_syntax_check_fails_when_node_is_absent(workspace: WebWorkspace) -> None:
    project_id = uuid4()
    await workspace.write_file(project_id, "app.js", "const a = 1;", {"app.js"})
    report = await workspace.check_readable(project_id, ["app.js"])
    assert not report.js_syntax_ok
    assert any("node binary" in issue for issue in report.issues)


async def test_publish_and_artifacts_round_trip(workspace: WebWorkspace) -> None:
    project_id = uuid4()
    artifact_id = uuid4()
    await workspace.write_file(project_id, "index.html", "<h1>x</h1>", {"index.html"})
    await workspace.publish(project_id, {"index.html": "<h1>x</h1>"}, {"index.html": artifact_id})
    assert await workspace.read_preview(project_id, "index.html") == b"<h1>x</h1>"
    assert await workspace.read_artifact(artifact_id, "index.html") == "<h1>x</h1>"
    await workspace.delete_artifact(artifact_id)
    await workspace.delete_project(project_id)
    assert await workspace.list_files(project_id) == []
