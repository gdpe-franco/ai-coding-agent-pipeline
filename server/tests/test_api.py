from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.conftest import auth_headers, register_and_login

pytestmark = pytest.mark.usefixtures("require_database")


def _create_chat(client: TestClient, token: str, message: str, key=None):
    key = key or uuid4()
    response = client.post(
        "/api/v1/chats",
        headers=auth_headers(token) | {"Idempotency-Key": str(key)},
        json={"message": message},
    )
    return response, key


def test_health_reports_components(client: TestClient) -> None:
    response = client.get("/health")
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["filesystem"] == "ok"
    assert body["worker"] in {"unavailable", "alive", "unknown"}
    assert body["status"] == ("ok" if body["worker"] == "alive" else "degraded")
    assert response.status_code == (200 if body["status"] == "ok" else 503)


def test_register_login_and_me(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    response = client.get("/api/v1/users/me", headers=auth_headers(token))
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


def test_missing_token_is_unauthorized(client: TestClient) -> None:
    assert client.get("/api/v1/chats").status_code == 401
    assert client.post("/api/v1/chats").status_code == 401


def test_first_message_atomically_creates_owned_chat_project_and_run(client: TestClient) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")

    created, key = _create_chat(client, alice, "Build a coupon validator")
    assert created.status_code == 201
    assert created.headers["location"] == f"/api/v1/chats/{created.json()['chat_id']}"
    body = created.json()
    repeated = client.post(
        "/api/v1/chats",
        headers=auth_headers(alice) | {"Idempotency-Key": str(key)},
        json={"message": "Build a coupon validator"},
    )
    assert repeated.status_code == 200
    assert repeated.json() == body
    conflict = client.post(
        "/api/v1/chats",
        headers=auth_headers(alice) | {"Idempotency-Key": str(key)},
        json={"message": "A different request"},
    )
    assert conflict.status_code == 409

    chat = client.get(f"/api/v1/chats/{body['chat_id']}", headers=auth_headers(alice))
    assert chat.status_code == 200
    assert chat.json()["project_id"] == body["project_id"]
    assert chat.json()["title"] == "Build a coupon validator"
    assert (
        client.get(f"/api/v1/chats/{body['chat_id']}", headers=auth_headers(bob)).status_code == 404
    )
    assert (
        client.get(f"/api/v1/projects/{body['project_id']}", headers=auth_headers(bob)).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/runs/{body['run_id']}", headers=auth_headers(bob)).status_code == 404
    )

    messages = client.get(
        f"/api/v1/chats/{body['chat_id']}/messages", headers=auth_headers(alice)
    ).json()
    assert [(message["role"], message["content"], message["run_id"]) for message in messages] == [
        ("user", "Build a coupon validator", body["run_id"])
    ]
    runs = client.get(f"/api/v1/chats/{body['chat_id']}/runs", headers=auth_headers(alice)).json()
    assert [(run["id"], run["chat_id"], run["project_id"]) for run in runs] == [
        (body["run_id"], body["chat_id"], body["project_id"])
    ]
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_each_chat_keeps_its_project_and_idempotent_messages(client: TestClient) -> None:
    token = register_and_login(client, "chat-projects@example.com")
    first, _ = _create_chat(client, token, "Build a coupon validator")
    first_data = first.json()
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE runs SET status='completed' WHERE id=%s", (first_data["run_id"],)
        )
        connection.commit()

    key = uuid4()
    headers = auth_headers(token) | {"Idempotency-Key": str(key)}
    submitted = client.post(
        f"/api/v1/chats/{first_data['chat_id']}/messages",
        headers=headers,
        json={"message": "Add an expiry date"},
    )
    assert submitted.status_code == 202
    duplicate = client.post(
        f"/api/v1/chats/{first_data['chat_id']}/messages",
        headers=headers,
        json={"message": "Add an expiry date"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == submitted.json()
    conflict = client.post(
        f"/api/v1/chats/{first_data['chat_id']}/messages",
        headers=headers,
        json={"message": "A different expiry date"},
    )
    assert conflict.status_code == 409

    second, _ = _create_chat(client, token, "Build Flappy Bird")
    second_data = second.json()
    assert second_data["chat_id"] != first_data["chat_id"]
    assert second_data["project_id"] != first_data["project_id"]
    with psycopg.connect(Settings().psycopg_url) as connection:
        run_rows = connection.execute(
            "SELECT id,chat_id FROM runs ORDER BY created_at,id"
        ).fetchall()
        chat_rows = connection.execute(
            "SELECT id,project_id FROM chats ORDER BY created_at,id"
        ).fetchall()
    assert {str(chat_id) for _, chat_id in run_rows} == {
        first_data["chat_id"],
        second_data["chat_id"],
    }
    assert {str(project_id) for _, project_id in chat_rows} == {
        first_data["project_id"],
        second_data["project_id"],
    }
    assert client.get("/api/v1/chats", headers=auth_headers(token)).status_code == 200


def test_busy_chat_conflict_does_not_create_an_extra_message_or_run(client: TestClient) -> None:
    token = register_and_login(client, "busy-chat@example.com")
    created, _ = _create_chat(client, token, "Build the app")
    chat_id = created.json()["chat_id"]
    response = client.post(
        f"/api/v1/chats/{chat_id}/messages",
        headers=auth_headers(token) | {"Idempotency-Key": str(uuid4())},
        json={"message": "Another run"},
    )
    assert response.status_code == 409
    assert (
        len(client.get(f"/api/v1/chats/{chat_id}/messages", headers=auth_headers(token)).json())
        == 1
    )
    assert len(client.get(f"/api/v1/chats/{chat_id}/runs", headers=auth_headers(token)).json()) == 1


def test_rename_chat_renames_its_project(client: TestClient) -> None:
    token = register_and_login(client, "rename-chat@example.com")
    created, _ = _create_chat(client, token, "Build a small app")
    chat_id = created.json()["chat_id"]
    renamed = client.patch(
        f"/api/v1/chats/{chat_id}",
        headers=auth_headers(token),
        json={"title": "Coupon validator"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Coupon validator"
    assert (
        client.get("/api/v1/chats", headers=auth_headers(token)).json()[0]["title"]
        == "Coupon validator"
    )


def test_run_events_are_ordered_and_owner_scoped(client: TestClient) -> None:
    alice = register_and_login(client, "events-alice@example.com")
    bob = register_and_login(client, "events-bob@example.com")
    created, _ = _create_chat(client, alice, "Build a page")
    run_id = created.json()["run_id"]
    execution_id = client.get(f"/api/v1/runs/{run_id}", headers=auth_headers(alice)).json()[
        "execution_id"
    ]
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "INSERT INTO run_events (id,run_id,execution_id,sequence,kind,payload)"
            " VALUES (%s,%s,%s,50,'step_completed','{}'::jsonb),"
            "(%s,%s,%s,51,'run_completed','{}'::jsonb)",
            (str(uuid4()), run_id, execution_id, str(uuid4()), run_id, execution_id),
        )
        connection.commit()
    events = client.get(f"/api/v1/runs/{run_id}/events?after=49", headers=auth_headers(alice))
    assert events.status_code == 200
    assert [event["sequence"] for event in events.json()] == [50, 51]
    assert client.get(f"/api/v1/runs/{run_id}/events", headers=auth_headers(bob)).status_code == 404


def test_run_lists_file_snapshots_for_each_execution(client: TestClient) -> None:
    token = register_and_login(client, "artifact-history@example.com")
    created, _ = _create_chat(client, token, "Build a page")
    run_id = created.json()["run_id"]
    execution_ids = [uuid4(), uuid4()]
    artifact_ids = [uuid4(), uuid4()]
    with psycopg.connect(Settings().psycopg_url) as connection:
        for index, (artifact_id, execution_id) in enumerate(zip(artifact_ids, execution_ids)):
            connection.execute(
                "INSERT INTO artifacts (id,run_id,execution_id,file_path,file_type,checksum)"
                " VALUES (%s,%s,%s,'index.html','html',%s)",
                (str(artifact_id), run_id, str(execution_id), str(index) * 64),
            )
        connection.commit()

    response = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=auth_headers(token))
    assert response.status_code == 200
    assert {item["execution_id"] for item in response.json()} == {
        str(execution_id) for execution_id in execution_ids
    }


def test_continuation_keeps_run_chat_and_project_and_changes_execution(client: TestClient) -> None:
    token = register_and_login(client, "continue-run@example.com")
    created, _ = _create_chat(client, token, "Build an app")
    chat_id, run_id = created.json()["chat_id"], created.json()["run_id"]
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='awaiting_user' WHERE id=%s", (run_id,))
        connection.commit()
    before = client.get(f"/api/v1/runs/{run_id}", headers=auth_headers(token)).json()
    key = uuid4()
    headers = auth_headers(token) | {"Idempotency-Key": str(key)}
    continued = client.post(
        f"/api/v1/runs/{run_id}/continuations",
        headers=headers,
        json={"guidance": "Keep the current design and fix the button"},
    )
    assert continued.status_code == 202
    after = continued.json()
    assert after["status"] == "pending"
    assert after["id"] == run_id and after["chat_id"] == chat_id
    assert after["project_id"] == before["project_id"]
    assert after["execution_id"] != before["execution_id"]
    repeated = client.post(
        f"/api/v1/runs/{run_id}/continuations",
        headers=headers,
        json={"guidance": "Keep the current design and fix the button"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["execution_id"] == after["execution_id"]
    messages = client.get(f"/api/v1/chats/{chat_id}/messages", headers=auth_headers(token)).json()
    assert [(item["role"], item["content"], item["run_id"]) for item in messages][-1] == (
        "user",
        "Keep the current design and fix the button",
        run_id,
    )
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='awaiting_user' WHERE id=%s", (run_id,))
        connection.commit()
    closed = client.post(f"/api/v1/runs/{run_id}/close", headers=auth_headers(token))
    assert closed.status_code == 200 and closed.json()["status"] == "failed"
    messages = client.get(f"/api/v1/chats/{chat_id}/messages", headers=auth_headers(token)).json()
    assert messages[-1]["role"] == "assistant" and messages[-1]["run_id"] == run_id


def test_delete_terminal_run_keeps_project_chat_and_message_history(client: TestClient) -> None:
    token = register_and_login(client, "delete-run@example.com")
    created, _ = _create_chat(client, token, "Build a page")
    chat_id, run_id, project_id = (
        created.json()["chat_id"],
        created.json()["run_id"],
        created.json()["project_id"],
    )
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='completed' WHERE id=%s", (run_id,))
        connection.commit()
    deleted = client.delete(f"/api/v1/runs/{run_id}", headers=auth_headers(token))
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/runs/{run_id}", headers=auth_headers(token)).status_code == 404
    assert (
        client.get(f"/api/v1/projects/{project_id}", headers=auth_headers(token)).status_code == 200
    )
    messages = client.get(f"/api/v1/chats/{chat_id}/messages", headers=auth_headers(token)).json()
    assert len(messages) == 1
    assert messages[0]["content"] == "Build a page" and messages[0]["run_id"] is None


@pytest.mark.parametrize("run_status", ["pending", "running", "awaiting_user"])
def test_chat_and_project_delete_reject_nonterminal_runs(
    client: TestClient, settings: Settings, run_status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = register_and_login(client, f"delete-{run_status}@example.com")
    created, _ = _create_chat(client, token, "Build a page")
    chat_id, project_id = created.json()["chat_id"], created.json()["project_id"]
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE runs SET status=%s WHERE id=%s", (run_status, created.json()["run_id"])
        )
        connection.commit()
    source = settings.data_dir / "projects" / project_id / "index.html"
    source.parent.mkdir(parents=True)
    source.write_text("keep", encoding="utf-8")
    removed_memory: list[str] = []

    async def delete_memory(project) -> None:
        removed_memory.append(str(project))

    monkeypatch.setattr(client.app.state.container.memory, "delete_project", delete_memory)
    assert client.delete(f"/api/v1/chats/{chat_id}", headers=auth_headers(token)).status_code == 409
    assert (
        client.delete(f"/api/v1/projects/{project_id}", headers=auth_headers(token)).status_code
        == 409
    )
    assert source.read_text(encoding="utf-8") == "keep"
    assert removed_memory == []


def test_delete_chat_removes_project_resources_but_has_no_interface_concept(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = register_and_login(client, "delete-chat@example.com")
    created, _ = _create_chat(client, token, "Build a page")
    chat_id, project_id = created.json()["chat_id"], created.json()["project_id"]
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE runs SET status='completed' WHERE id=%s", (created.json()["run_id"],)
        )
        connection.commit()
    source = settings.data_dir / "projects" / project_id / "index.html"
    source.parent.mkdir(parents=True)
    source.write_text("project", encoding="utf-8")
    deleted_memory: list[str] = []

    async def delete_memory(project) -> None:
        deleted_memory.append(str(project))

    monkeypatch.setattr(client.app.state.container.memory, "delete_project", delete_memory)
    assert client.delete(f"/api/v1/chats/{chat_id}", headers=auth_headers(token)).status_code == 204
    assert client.get(f"/api/v1/chats/{chat_id}", headers=auth_headers(token)).status_code == 404
    assert (
        client.get(f"/api/v1/projects/{project_id}", headers=auth_headers(token)).status_code == 404
    )
    assert not source.exists()
    assert deleted_memory == [project_id]


def test_file_read_rejects_traversal(client: TestClient, settings: Settings) -> None:
    token = register_and_login(client, "traversal@example.com")
    created, _ = _create_chat(client, token, "Build a page")
    project_id = created.json()["project_id"]
    response = client.get(
        f"/api/v1/projects/{project_id}/files/..%2Fsecret.txt", headers=auth_headers(token)
    )
    assert response.status_code in {400, 404}


def test_preview_url_and_preview_route(client: TestClient, settings: Settings) -> None:
    token = register_and_login(client, "preview@example.com")
    created, _ = _create_chat(client, token, "Build a preview")
    project = client.get(
        f"/api/v1/projects/{created.json()['project_id']}", headers=auth_headers(token)
    ).json()
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE projects SET preview_ready=true WHERE id=%s", (project["id"],))
        connection.commit()
    preview_file = settings.data_dir / "preview" / project["id"] / "index.html"
    preview_file.parent.mkdir(parents=True)
    preview_file.write_text("<h1>Approved</h1>", encoding="utf-8")
    url = client.get(
        f"/api/v1/projects/{project['id']}/preview-url", headers=auth_headers(token)
    ).json()["url"]
    response = client.get(
        f"/preview/{project['preview_id']}/index.html",
        headers={"Host": "preview.localhost"},
    )
    assert url.endswith(f"/preview/{project['preview_id']}/")
    assert response.text == "<h1>Approved</h1>"


def test_preview_url_is_not_issued_before_an_approved_run(client: TestClient) -> None:
    token = register_and_login(client, "no-preview@example.com")
    created, _ = _create_chat(client, token, "Build an app")
    project_id = created.json()["project_id"]
    project = client.get(f"/api/v1/projects/{project_id}", headers=auth_headers(token)).json()
    assert project["preview_ready"] is False
    assert (
        client.get(
            f"/api/v1/projects/{project_id}/preview-url", headers=auth_headers(token)
        ).status_code
        == 404
    )
    assert client.get(f"/preview/{project['preview_id']}/index.html").status_code == 404


def test_rate_limiter_returns_429(tmp_path) -> None:
    from app.application.ports import RateLimitResult

    class FakeLimiter:
        async def consume(self, bucket: str, key: str) -> RateLimitResult:
            if bucket == "auth":
                return RateLimitResult(True, 5, 4, datetime.now(UTC), 0)
            return RateLimitResult(False, 1, 0, datetime.now(UTC), 10)

    settings = Settings(
        app_env="development",
        secret_key="test-secret-key-that-is-long-enough-32",
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
    )
    app = create_app(settings)
    app.state.container.rate_limiter = FakeLimiter()
    with TestClient(app, base_url="http://localhost") as test_client:
        token = register_and_login(test_client, "limited@example.com")
        response = test_client.get("/api/v1/chats", headers=auth_headers(token))
        assert response.status_code == 429
        assert response.headers["retry-after"] == "10"


def test_rate_limiter_fails_closed(tmp_path) -> None:
    from app.application.ports import RateLimitResult
    from app.infrastructure.rate_limit.postgres import RateLimitUnavailable

    class BrokenLimiter:
        async def consume(self, bucket: str, key: str) -> RateLimitResult:
            if bucket == "auth":
                return RateLimitResult(True, 5, 4, datetime.now(UTC), 0)
            raise RateLimitUnavailable("unavailable")

    settings = Settings(
        app_env="development",
        secret_key="test-secret-key-that-is-long-enough-32",
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
    )
    app = create_app(settings)
    app.state.container.rate_limiter = BrokenLimiter()
    with TestClient(app, base_url="http://localhost") as test_client:
        token = register_and_login(test_client, "closed@example.com")
        assert test_client.get("/api/v1/chats", headers=auth_headers(token)).status_code == 503
