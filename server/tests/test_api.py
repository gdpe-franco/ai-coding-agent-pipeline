from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.application.ports import RateLimitResult
from app.main import create_app
from app.settings import Settings
from tests.conftest import auth_headers, register_and_login

pytestmark = pytest.mark.usefixtures("require_database")


def _app_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_health_reports_components(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["filesystem"] == "ok"
    assert body["worker"] in {"idle", "alive", "unknown"}
    assert body["checks"]["worker"] == body["worker"]


def test_register_login_and_me(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    response = client.get("/api/v1/users/me", headers=auth_headers(token))
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


def test_missing_token_is_unauthorized(client: TestClient) -> None:
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/chat").status_code == 401


def _confirm_new_project(
    client: TestClient, headers: dict[str, str], turn_id: str, version: int, title: str
):
    return client.post(
        f"/api/v1/chat/messages/{turn_id}/selection",
        headers=headers,
        json={
            "context_version": version,
            "choice": "new_project",
            "feature_mode": "new",
            "title": title,
        },
    )


def test_chat_is_persistent_and_owner_scoped(client: TestClient) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")

    alice_chat = client.get("/api/v1/chat", headers=auth_headers(alice)).json()
    repeated_chat = client.get("/api/v1/chat", headers=auth_headers(alice)).json()
    assert repeated_chat["conversation_id"] == alice_chat["conversation_id"]
    bob_chat = client.get("/api/v1/chat", headers=auth_headers(bob)).json()
    assert alice_chat["conversation_id"] != bob_chat["conversation_id"]
    assert alice_chat["turns"] == bob_chat["turns"] == []

    submitted = client.post(
        "/api/v1/chat/messages",
        headers=auth_headers(alice),
        json={"message": "private message"},
    )
    assert submitted.status_code == 202
    turn_id = submitted.json()["turn_id"]
    assert (
        client.post(
            "/api/v1/chat/messages",
            headers=auth_headers(alice),
            json={"message": "another request"},
        ).status_code
        == 409
    )
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    alice_history = client.get("/api/v1/chat", headers=auth_headers(alice)).json()["turns"]
    bob_history = client.get("/api/v1/chat", headers=auth_headers(bob)).json()["turns"]
    alice_state = client.get("/api/v1/chat", headers=auth_headers(alice)).json()
    assert [turn["content"] for turn in alice_history] == ["private message"]
    assert alice_history[0]["id"] == turn_id
    assert alice_history[0]["run_id"] is None
    assert alice_history[0]["request_status"] == "needs_selection"
    assert alice_state["pending_selection"]["turn_id"] == turn_id
    assert alice_state["pending_selection"]["projects"] == []
    assert bob_history == []
    assert client.get("/api/v1/chat?after=1", headers=auth_headers(alice)).json()["turns"] == []


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("", id="empty"),
        pytest.param("  ", id="spaces"),
        pytest.param("\n\t", id="whitespace"),
    ],
)
def test_chat_rejects_blank_message(client: TestClient, message: str) -> None:
    token = register_and_login(client, "alice@example.com")
    response = client.post(
        "/api/v1/chat/messages",
        headers=auth_headers(token),
        json={"message": message},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "title",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("x" * 201, id="over-limit"),
    ],
)
def test_new_project_requires_valid_title(client: TestClient, title: str) -> None:
    token = register_and_login(client, "alice@example.com")
    headers = auth_headers(token)
    turn = client.post("/api/v1/chat/messages", headers=headers, json={"message": "build it"})
    turn_id = turn.json()["turn_id"]
    pending = client.get("/api/v1/chat", headers=headers).json()["pending_selection"]

    invalid = _confirm_new_project(client, headers, turn_id, pending["context_version"], title)

    assert invalid.status_code == 422
    assert client.get("/api/v1/chat", headers=headers).json()["turns"][0]["request_status"] == (
        "needs_selection"
    )
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_confirming_new_project_links_original_request_once(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    headers = auth_headers(token)
    existing = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Old project", "summary": ""}
    ).json()
    posted = client.post(
        "/api/v1/chat/messages", headers=headers, json={"message": "build a new app"}
    )
    turn_id = posted.json()["turn_id"]
    pending = client.get("/api/v1/chat", headers=headers).json()["pending_selection"]
    assert [project["id"] for project in pending["projects"]] == [existing["id"]]
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    confirmed = _confirm_new_project(
        client, headers, turn_id, pending["context_version"], "New app"
    )
    assert confirmed.status_code == 202
    result = confirmed.json()
    chat = client.get("/api/v1/chat", headers=headers).json()
    turn = next(turn for turn in chat["turns"] if turn["id"] == turn_id)
    run = client.get(f"/api/v1/runs/{result['run_id']}", headers=headers).json()
    assert chat["pending_selection"] is None
    assert chat["selected_project_id"] == result["project_id"]
    assert chat["context_version"] == result["context_version"] == 1
    assert turn["content"] == run["message"] == "build a new app"
    assert turn["project_id"] == run["project_id"] == result["project_id"]
    assert turn["run_id"] == result["run_id"]
    assert turn["run_status"] == "pending"
    assert turn["request_status"] == "enqueued"
    assert run["status"] == "pending"

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='running' WHERE id=%s", (result["run_id"],))
        connection.commit()
    running_turn = next(
        turn
        for turn in client.get("/api/v1/chat", headers=headers).json()["turns"]
        if turn["id"] == turn_id
    )
    assert running_turn["run_status"] == "running"

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='completed' WHERE id=%s", (result["run_id"],))
        connection.commit()
    completed_turn = next(
        turn
        for turn in client.get("/api/v1/chat", headers=headers).json()["turns"]
        if turn["id"] == turn_id
    )
    assert completed_turn["run_status"] == "completed"
    with psycopg.connect(Settings().psycopg_url) as connection:
        run_conversation_id = connection.execute(
            "SELECT conversation_id FROM runs WHERE id=%s", (result["run_id"],)
        ).fetchone()[0]
    assert str(run_conversation_id) == chat["conversation_id"]

    retried = _confirm_new_project(client, headers, turn_id, pending["context_version"], "New app")
    assert retried.status_code == 202
    assert retried.json() == result
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_cancel_selection_preserves_turn_and_context(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    headers = auth_headers(token)
    posted = client.post("/api/v1/chat/messages", headers=headers, json={"message": "build it"})
    turn_id = posted.json()["turn_id"]

    cancelled = client.post(f"/api/v1/chat/messages/{turn_id}/cancel", headers=headers)

    assert cancelled.status_code == 204
    chat = client.get("/api/v1/chat", headers=headers).json()
    assert chat["pending_selection"] is None
    assert chat["selected_project_id"] is None
    assert chat["context_version"] == 0
    assert len(chat["turns"]) == 1
    assert chat["turns"][0]["content"] == "build it"
    assert chat["turns"][0]["request_status"] == "cancelled"
    assert (
        client.post(f"/api/v1/chat/messages/{turn_id}/cancel", headers=headers).status_code == 204
    )
    assert _confirm_new_project(client, headers, turn_id, 0, "No project").status_code == 409
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_selection_is_owner_scoped_and_rejects_stale_context(client: TestClient) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    alice_headers = auth_headers(alice)
    bob_headers = auth_headers(bob)
    turn = client.post(
        "/api/v1/chat/messages", headers=alice_headers, json={"message": "build it"}
    ).json()
    turn_id = turn["turn_id"]
    pending = client.get("/api/v1/chat", headers=alice_headers).json()["pending_selection"]

    assert (
        _confirm_new_project(
            client, bob_headers, turn_id, pending["context_version"], "Stolen project"
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/chat/messages/{turn_id}/cancel", headers=bob_headers).status_code
        == 404
    )

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE conversations SET context_version=1 WHERE owner_id=(SELECT id FROM users WHERE email=%s)",
            ("alice@example.com",),
        )
        connection.commit()
    stale = _confirm_new_project(client, alice_headers, turn_id, 0, "Stale project")

    assert stale.status_code == 409
    alice_state = client.get("/api/v1/chat", headers=alice_headers).json()
    assert alice_state["pending_selection"]["turn_id"] == turn_id
    assert alice_state["pending_selection"]["context_version"] == 1
    assert alice_state["turns"][0]["request_status"] == "needs_selection"
    assert client.get("/api/v1/chat", headers=bob_headers).json()["turns"] == []
    refreshed = _confirm_new_project(
        client,
        alice_headers,
        turn_id,
        alice_state["pending_selection"]["context_version"],
        "Fresh project",
    )
    assert refreshed.status_code == 202
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_chat_poll_does_not_create_existing_conversation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = register_and_login(client, "alice@example.com")
    headers = auth_headers(token)
    assert client.get("/api/v1/chat", headers=headers).status_code == 200

    def unexpected(*args, **kwargs):
        raise AssertionError("existing conversation should be read without an upsert")

    monkeypatch.setattr(
        "app.infrastructure.persistence.repository.SqlAlchemyRepository.get_or_create_conversation",
        unexpected,
    )
    assert client.get("/api/v1/chat", headers=headers).status_code == 200


def test_project_lifecycle_and_owner_scoping(client: TestClient) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")

    created = client.post(
        "/api/v1/projects",
        headers=auth_headers(alice),
        json={"title": "Landing", "summary": "A landing page"},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["title"] == "Landing"

    listed = client.get("/api/v1/projects", headers=auth_headers(alice))
    assert [item["id"] for item in listed.json()] == [project["id"]]
    assert client.get("/api/v1/projects", headers=auth_headers(bob)).json() == []

    detail = client.get(f"/api/v1/projects/{project['id']}", headers=auth_headers(alice))
    assert detail.status_code == 200

    assert (
        client.get(f"/api/v1/projects/{project['id']}", headers=auth_headers(bob)).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/projects/{project['id']}/files", headers=auth_headers(bob)).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/projects/{project['id']}/conversation", headers=auth_headers(bob)
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/projects/{project['id']}/preview-url", headers=auth_headers(bob)
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/projects/{project['id']}", headers=auth_headers(bob)).status_code
        == 404
    )

    deleted = client.delete(f"/api/v1/projects/{project['id']}", headers=auth_headers(alice))
    assert deleted.status_code == 204
    assert (
        client.get(f"/api/v1/projects/{project['id']}", headers=auth_headers(alice)).status_code
        == 404
    )


def test_file_read_rejects_traversal(client: TestClient, settings: Settings) -> None:
    token = register_and_login(client, "alice@example.com")
    project = client.post(
        "/api/v1/projects", headers=auth_headers(token), json={"title": "P", "summary": ""}
    ).json()
    response = client.get(
        f"/api/v1/projects/{project['id']}/files/..%2Fsecret.txt",
        headers=auth_headers(token),
    )
    assert response.status_code in {400, 404}


def test_run_creation_conflicts_and_ownership(client: TestClient) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    project = client.post(
        "/api/v1/projects", headers=auth_headers(alice), json={"title": "P", "summary": ""}
    ).json()

    created = client.post(
        "/api/v1/runs",
        headers=auth_headers(alice),
        json={"message": "build it", "project_id": project["id"]},
    )
    assert created.status_code == 202
    body = created.json()
    assert body["project_id"] == project["id"]
    turn = client.get("/api/v1/chat", headers=auth_headers(alice)).json()["turns"][0]
    assert turn["content"] == "build it"
    assert turn["project_id"] == project["id"]
    assert turn["run_id"] == body["run_id"]
    other_project = client.post(
        "/api/v1/projects",
        headers=auth_headers(alice),
        json={"title": "Other project", "summary": ""},
    ).json()
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE conversations SET selected_project_id=%s, context_version=context_version+1 "
            "WHERE owner_id=(SELECT id FROM users WHERE email='alice@example.com')",
            (other_project["id"],),
        )
        connection.commit()
    older_turn = client.get("/api/v1/chat", headers=auth_headers(alice)).json()["turns"][0]
    assert older_turn["project_id"] == project["id"]
    assert older_turn["run_id"] == body["run_id"]

    duplicate = client.post(
        "/api/v1/runs",
        headers=auth_headers(alice),
        json={"message": "again", "project_id": project["id"]},
    )
    assert duplicate.status_code == 409

    foreign = client.post(
        "/api/v1/runs",
        headers=auth_headers(bob),
        json={"message": "steal", "project_id": project["id"]},
    )
    assert foreign.status_code == 404

    run = client.get(f"/api/v1/runs/{body['run_id']}", headers=auth_headers(alice))
    assert run.status_code == 200
    assert run.json()["status"] == "pending"
    assert run.json()["attempts_used"] == 0
    assert run.json()["round"] == 1

    assert (
        client.get(f"/api/v1/runs/{body['run_id']}", headers=auth_headers(bob)).status_code == 404
    )
    assert (
        client.get(f"/api/v1/runs/{body['run_id']}/events", headers=auth_headers(bob)).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/runs/{body['run_id']}/trace", headers=auth_headers(bob)).status_code
        == 404
    )


def test_projectless_run_conflict(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    first = client.post(
        "/api/v1/runs", headers=auth_headers(token), json={"message": "new project"}
    )
    assert first.status_code == 202
    second = client.post(
        "/api/v1/runs", headers=auth_headers(token), json={"message": "another new project"}
    )
    assert second.status_code == 409


def test_run_events_are_ordered_and_trace_is_owner_scoped(client: TestClient) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    headers = auth_headers(alice)
    run = client.post("/api/v1/runs", headers=headers, json={"message": "build"}).json()
    run_id = run["run_id"]
    first_time = datetime(2026, 1, 1, tzinfo=UTC)
    second_time = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    graph_run_id = uuid4()

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "INSERT INTO events (id, run_id, kind, payload, created_at)"
            " VALUES (%s,%s,'coder',%s::jsonb,%s)",
            (str(uuid4()), run_id, json.dumps({"attempt": 1}), second_time),
        )
        connection.execute(
            "INSERT INTO events (id, run_id, kind, payload, created_at)"
            " VALUES (%s,%s,'planner',%s::jsonb,%s)",
            (str(uuid4()), run_id, json.dumps({"summary": "plan"}), first_time),
        )
        connection.execute(
            "INSERT INTO graph_runs (id, run_id, round, thread_id, created_at)"
            " VALUES (%s,%s,1,'thread-1',%s)",
            (str(graph_run_id), run_id, first_time),
        )
        connection.execute(
            "INSERT INTO graph_events (id, graph_run_id, node, payload, created_at)"
            " VALUES (%s,%s,'planner',%s::jsonb,%s)",
            (str(uuid4()), str(graph_run_id), json.dumps({"summary": "plan"}), first_time),
        )
        connection.commit()

    events = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
    assert [event["kind"] for event in events.json()] == ["planner", "coder"]
    after_first = client.get(
        f"/api/v1/runs/{run_id}/events",
        headers=headers,
        params={"after": first_time.isoformat()},
    )
    assert [event["kind"] for event in after_first.json()] == ["coder"]
    trace = client.get(f"/api/v1/runs/{run_id}/trace", headers=headers)
    assert trace.json()["rounds"][0]["events"][0]["node"] == "planner"
    assert client.get(f"/api/v1/runs/{run_id}/events", headers=auth_headers(bob)).status_code == 404
    assert client.get(f"/api/v1/runs/{run_id}/trace", headers=auth_headers(bob)).status_code == 404


def test_resume_and_close_require_awaiting_user(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    headers = auth_headers(token)
    project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Handoff project", "summary": ""}
    ).json()
    run = client.post(
        "/api/v1/runs",
        headers=headers,
        json={"message": "new", "project_id": project["id"]},
    ).json()
    run_id = run["run_id"]

    assert (
        client.post(
            f"/api/v1/runs/{run_id}/resume", headers=auth_headers(token), json={"guidance": "go"}
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/v1/runs/{run_id}/close", headers=auth_headers(token)).status_code == 409
    )

    plan = {"summary": "preserve this plan"}
    code = [{"file_path": "index.html", "content": "<h1>preserved</h1>", "checksum": "abc"}]
    feedback = {"verdict": "reject", "summary": "Needs guidance", "issues": [{"message": "Fix it"}]}
    checks = {"file_structure_ok": True, "js_syntax_ok": True, "issues": []}
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE runs SET status='awaiting_user', attempts_used=3, attempts_total=5, round=2"
            ", plan=%s::jsonb, latest_code=%s::jsonb, feedback=%s::jsonb, checks=%s::jsonb"
            " WHERE id=%s",
            (json.dumps(plan), json.dumps(code), json.dumps(feedback), json.dumps(checks), run_id),
        )
        connection.commit()

    resumed = client.post(
        f"/api/v1/runs/{run_id}/resume",
        headers=headers,
        json={"guidance": "make it bold"},
    )
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "pending"
    assert body["round"] == 3
    assert body["attempts_used"] == 0
    assert body["attempts_total"] == 5
    assert body["project_id"] == project["id"]
    assert body["plan"] == plan
    assert body["latest_code"] == code
    assert body["feedback"] == feedback
    assert body["checks"] == checks
    assert body["user_guidance"] == "make it bold"
    turns = client.get("/api/v1/chat", headers=headers).json()["turns"]
    assert [turn["content"] for turn in turns] == ["new", "make it bold"]
    assert all(turn["run_id"] == run_id for turn in turns)
    assert all(turn["project_id"] == project["id"] for turn in turns)

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='awaiting_user' WHERE id=%s", (run_id,))
        connection.commit()
    closed = client.post(f"/api/v1/runs/{run_id}/close", headers=headers)
    assert closed.status_code == 200
    assert closed.json()["status"] == "failed"
    assert closed.json()["project_id"] == project["id"]
    assert closed.json()["plan"] == plan
    assert closed.json()["latest_code"] == code
    assert closed.json()["feedback"] == feedback
    assert closed.json()["checks"] == checks
    assert closed.json()["round"] == 3
    assert closed.json()["attempts_used"] == 0
    assert closed.json()["attempts_total"] == 5
    turns = client.get("/api/v1/chat", headers=headers).json()["turns"]
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["run_id"] == run_id
    assert turns[0]["run_status"] == "failed"
    kinds = [
        event["kind"]
        for event in client.get(f"/api/v1/runs/{run_id}/events", headers=headers).json()
    ]
    assert "chat_reply" in kinds


@pytest.mark.parametrize("guidance", ["", "   ", "\n\t"], ids=["empty", "spaces", "whitespace"])
def test_resume_requires_nonblank_guidance(client: TestClient, guidance: str) -> None:
    token = register_and_login(client, "alice@example.com")
    run = client.post("/api/v1/runs", headers=auth_headers(token), json={"message": "new"}).json()

    response = client.post(
        f"/api/v1/runs/{run['run_id']}/resume",
        headers=auth_headers(token),
        json={"guidance": guidance},
    )

    assert response.status_code == 422


def test_switching_context_does_not_resume_or_close_handoff(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    headers = auth_headers(token)
    handoff_project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Handoff", "summary": ""}
    ).json()
    other_project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Other", "summary": ""}
    ).json()
    handoff = client.post(
        "/api/v1/runs",
        headers=headers,
        json={"message": "unfinished", "project_id": handoff_project["id"]},
    ).json()
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE conversations SET selected_project_id=%s, active_feature_run_id=%s, context_version=1"
            " WHERE owner_id=(SELECT id FROM users WHERE email='alice@example.com')",
            (handoff_project["id"], handoff["run_id"]),
        )
        connection.execute(
            "UPDATE runs SET status='awaiting_user', round=2, attempts_used=3, attempts_total=6"
            " WHERE id=%s",
            (handoff["run_id"],),
        )
        connection.commit()

    request = client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "Work on the other project", "request_selection": True},
    )
    assert request.status_code == 202
    pending = client.get("/api/v1/chat", headers=headers).json()["pending_selection"]
    switched = client.post(
        f"/api/v1/chat/messages/{request.json()['turn_id']}/selection",
        headers=headers,
        json={
            "context_version": pending["context_version"],
            "choice": "existing_project",
            "feature_mode": "continue",
            "project_id": other_project["id"],
        },
    )

    assert switched.status_code == 202
    unchanged = client.get(f"/api/v1/runs/{handoff['run_id']}", headers=headers).json()
    assert unchanged["status"] == "awaiting_user"
    assert unchanged["project_id"] == handoff_project["id"]
    assert unchanged["round"] == 2
    assert unchanged["attempts_used"] == 3
    assert unchanged["attempts_total"] == 6


def test_delete_terminal_run(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    run = client.post("/api/v1/runs", headers=auth_headers(token), json={"message": "new"}).json()
    run_id = run["run_id"]

    assert client.delete(f"/api/v1/runs/{run_id}", headers=auth_headers(token)).status_code == 409
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='completed' WHERE id=%s", (run_id,))
        connection.commit()
    assert client.delete(f"/api/v1/runs/{run_id}", headers=auth_headers(token)).status_code == 204
    assert client.get(f"/api/v1/runs/{run_id}", headers=auth_headers(token)).status_code == 404


def test_artifact_content_matches_checksum(client: TestClient, settings: Settings) -> None:
    token = register_and_login(client, "alice@example.com")
    other_token = register_and_login(client, "bob@example.com")
    project = client.post(
        "/api/v1/projects", headers=auth_headers(token), json={"title": "P", "summary": ""}
    ).json()
    run = client.post(
        "/api/v1/runs",
        headers=auth_headers(token),
        json={"message": "build", "project_id": project["id"]},
    ).json()
    artifact_id = uuid4()
    content = "<h1>artifact</h1>"
    digest = hashlib.sha256(content.encode()).hexdigest()
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "INSERT INTO artifacts (id, run_id, file_path, file_type, checksum)"
            " VALUES (%s, %s, 'index.html', 'html', %s)",
            (str(artifact_id), run["run_id"], digest),
        )
        connection.commit()
    target = settings.data_dir / "artifacts" / str(artifact_id) / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text(content)

    response = client.get(
        f"/api/v1/runs/{run['run_id']}/artifacts/{artifact_id}", headers=auth_headers(token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == content
    assert body["checksum"] == digest
    assert (
        client.get(
            f"/api/v1/runs/{run['run_id']}/artifacts/{artifact_id}",
            headers=auth_headers(other_token),
        ).status_code
        == 404
    )


def test_preview_url_and_preview_route(client: TestClient, settings: Settings) -> None:
    token = register_and_login(client, "alice@example.com")
    project = client.post(
        "/api/v1/projects", headers=auth_headers(token), json={"title": "P", "summary": ""}
    ).json()

    url = client.get(
        f"/api/v1/projects/{project['id']}/preview-url", headers=auth_headers(token)
    ).json()["url"]
    assert project["preview_id"] in url

    missing = client.get(f"/preview/{project['preview_id']}/index.html")
    assert missing.status_code == 404
    assert (
        client.get(
            f"/api/v1/projects/{project['id']}/preview-url", headers=auth_headers("bad")
        ).status_code
        == 401
    )


def test_rate_limiter_returns_429(tmp_path) -> None:
    settings = Settings(
        secret_key="test-secret-key-that-is-long-enough-32",
        rate_limit_enabled=True,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
    )
    with _app_client(settings) as client:
        statuses = []
        for index in range(6):
            statuses.append(
                client.post(
                    "/api/v1/auth/register",
                    json={"email": f"user{index}@example.com", "password": "password123"},
                ).status_code
            )
        assert statuses.count(429) == 1
        limited = client.post(
            "/api/v1/auth/register",
            json={"email": "late@example.com", "password": "password123"},
        )
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers


def test_selection_confirmation_uses_write_and_run_create_buckets(tmp_path) -> None:
    settings = Settings(
        secret_key="test-secret-key-that-is-long-enough-32",
        rate_limit_enabled=True,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
    )
    app = create_app(settings)
    buckets = []

    async def allow(bucket: str, key: str) -> RateLimitResult:
        buckets.append(bucket)
        return RateLimitResult(True, 10, 9, datetime.now(UTC), 60)

    app.state.container.rate_limiter.consume = allow
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/messages/00000000-0000-0000-0000-000000000001/selection",
            json={
                "context_version": 0,
                "choice": "new_project",
                "feature_mode": "new",
                "title": "Project",
            },
        )
    assert response.status_code == 401
    assert buckets == ["write", "run_create"]


def test_rate_limiter_fails_closed(tmp_path) -> None:
    from app.infrastructure.rate_limit.postgres import RateLimitUnavailable

    settings = Settings(
        secret_key="test-secret-key-that-is-long-enough-32",
        rate_limit_enabled=True,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
    )
    app = create_app(settings)

    async def broken(bucket: str, key: str):
        raise RateLimitUnavailable("down")

    app.state.container.rate_limiter.consume = broken
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        response = client.get("/api/v1/projects")
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
