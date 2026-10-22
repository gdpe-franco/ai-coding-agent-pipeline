from __future__ import annotations

import hashlib
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

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
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    alice_history = client.get("/api/v1/chat", headers=auth_headers(alice)).json()["turns"]
    bob_history = client.get("/api/v1/chat", headers=auth_headers(bob)).json()["turns"]
    assert [turn["content"] for turn in alice_history] == ["private message"]
    assert alice_history[0]["id"] == turn_id
    assert alice_history[0]["run_id"] is None
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


def test_resume_and_close_require_awaiting_user(client: TestClient) -> None:
    token = register_and_login(client, "alice@example.com")
    run = client.post("/api/v1/runs", headers=auth_headers(token), json={"message": "new"}).json()
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

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE runs SET status='awaiting_user', attempts_used=3, attempts_total=5, round=2"
            " WHERE id=%s",
            (run_id,),
        )
        connection.commit()

    resumed = client.post(
        f"/api/v1/runs/{run_id}/resume",
        headers=auth_headers(token),
        json={"guidance": "make it bold"},
    )
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "pending"
    assert body["round"] == 3
    assert body["attempts_used"] == 0
    assert body["attempts_total"] == 5
    assert body["user_guidance"] == "make it bold"
    turns = client.get("/api/v1/chat", headers=auth_headers(token)).json()["turns"]
    assert [turn["content"] for turn in turns] == ["new", "make it bold"]
    assert all(turn["run_id"] == run_id for turn in turns)

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='awaiting_user' WHERE id=%s", (run_id,))
        connection.commit()
    closed = client.post(f"/api/v1/runs/{run_id}/close", headers=auth_headers(token))
    assert closed.status_code == 200
    assert closed.json()["status"] == "failed"
    turns = client.get("/api/v1/chat", headers=auth_headers(token)).json()["turns"]
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["run_id"] == run_id


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
