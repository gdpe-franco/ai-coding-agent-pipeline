from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.application.chat import ChatRequestProcessor
from app.domain.enums import ChatRequestStatus, ConversationSender, RunStatus
from app.infrastructure.persistence.models import Conversation, ConversationTurn, Run
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.settings import Settings
from tests.conftest import auth_headers, create_test_user, register_and_login

pytestmark = pytest.mark.usefixtures("require_database")


class FakeIntentLLM:
    def __init__(self, response) -> None:
        self.response = response

    async def complete(self, role, messages, schema):
        assert role == "planner"
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


async def _request_context(session_factory, repo_factory, email: str):
    async with session_factory() as session:
        user = await create_test_user(session, email)
        repo = SqlAlchemyRepository(session)
        project = await repo.create_project(user.id, "Current project")
        feature = await repo.create_run(user.id, "Original feature", project.id)
        await repo.update_run(feature.id, status=RunStatus.COMPLETED)
        conversation = await repo.get_conversation(user.id)
        conversation.selected_project_id = project.id
        conversation.active_feature_run_id = feature.id
        conversation.context_version = 3
        await repo.commit()
        await repo.add_conversation_turn(
            user.id, "Earlier conversation", role=ConversationSender.USER, project_id=project.id
        )
        await repo.commit()
        request = await repo.create_chat_request(user.id, "Add a settings screen")
        await repo.commit()
    return user, project, feature, request


@pytest.mark.asyncio
async def test_project_history_excludes_other_projects_and_unassigned_turns(
    session_factory,
) -> None:
    async with session_factory() as session:
        user = await create_test_user(session, "project-history@example.com")
        repo = SqlAlchemyRepository(session)
        older_project = await repo.create_project(user.id, "Older")
        other_project = await repo.create_project(user.id, "Other")
        await repo.add_conversation_turn(
            user.id,
            "Older project discussion",
            ConversationSender.USER,
            project_id=older_project.id,
        )
        await repo.add_conversation_turn(
            user.id,
            "Other project discussion",
            ConversationSender.USER,
            project_id=other_project.id,
        )
        await repo.add_conversation_turn(user.id, "Unassigned request", ConversationSender.USER)
        await repo.commit()

        history = await repo.list_conversation_turns(user.id, older_project.id)

    assert [turn.content for turn in history] == ["Older project discussion"]


@pytest.mark.asyncio
async def test_worker_claims_and_enqueues_clear_continuation(session_factory, repo_factory) -> None:
    _, project, feature, request = await _request_context(
        session_factory, repo_factory, "continue@example.com"
    )
    processor = ChatRequestProcessor(
        repo_factory,
        FakeIntentLLM({"action": "continue_current", "reason": "Adds to the current app."}),
        "intent-worker",
        heartbeat_seconds=3600,
        stale_seconds=60,
        max_runs_per_project=100,
    )

    assert await processor.process_one()
    assert not await processor.process_one()

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, request.id)
        conversation = await session.get(Conversation, turn.conversation_id)
        run = await session.get(Run, turn.run_id)
    assert turn.request_status == ChatRequestStatus.ENQUEUED
    assert turn.project_id == project.id
    assert run.message == request.content
    assert run.status == RunStatus.PENDING
    assert run.project_id == project.id
    assert conversation.context_version == 3
    assert conversation.active_feature_run_id == feature.id


@pytest.mark.asyncio
async def test_worker_reclaims_stale_classification(session_factory, repo_factory) -> None:
    _, _, _, request = await _request_context(session_factory, repo_factory, "reclaim@example.com")
    async with session_factory() as session:
        await session.execute(
            update(ConversationTurn)
            .where(ConversationTurn.id == request.id)
            .values(
                request_status=ChatRequestStatus.CLASSIFYING,
                locked_by="dead-worker",
                heartbeat_at=datetime.now(UTC) - timedelta(seconds=61),
            )
        )
        await session.commit()
        claimed = await SqlAlchemyRepository(session).claim_chat_request("live-worker", 60)
        saved = await session.get(ConversationTurn, request.id)
    assert claimed.id == request.id
    assert saved.locked_by == "live-worker"
    assert saved.request_status == ChatRequestStatus.CLASSIFYING


@pytest.mark.parametrize(
    ("response", "intent_error"),
    [
        ({"action": "request_selection", "reason": "This may be a new feature."}, False),
        (RuntimeError("provider unavailable"), True),
    ],
)
@pytest.mark.asyncio
async def test_uncertain_or_failed_intent_preserves_context_and_requests_selection(
    session_factory, repo_factory, response, intent_error
) -> None:
    _, project, feature, request = await _request_context(
        session_factory, repo_factory, f"selection-{intent_error}@example.com"
    )
    processor = ChatRequestProcessor(
        repo_factory,
        FakeIntentLLM(response),
        "intent-worker",
        heartbeat_seconds=3600,
        stale_seconds=60,
        max_runs_per_project=100,
    )

    await processor.process_one()

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, request.id)
        conversation = await session.get(Conversation, turn.conversation_id)
        runs = list(
            (await session.execute(select(Run).where(Run.project_id == project.id))).scalars()
        )
    assert turn.request_status == ChatRequestStatus.NEEDS_SELECTION
    assert turn.intent_result["action"] == "request_selection"
    assert (turn.intent_result.get("tag") == "intent_error") is intent_error
    assert conversation.context_version == 3
    assert conversation.selected_project_id == project.id
    assert conversation.active_feature_run_id == feature.id
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_context_change_during_detection_does_not_enqueue(
    session_factory, repo_factory
) -> None:
    _, project, feature, request = await _request_context(
        session_factory, repo_factory, "stale-intent@example.com"
    )
    async with session_factory() as session:
        conversation = await session.get(Conversation, request.conversation_id)
        conversation.context_version += 1
        await session.commit()
    processor = ChatRequestProcessor(
        repo_factory,
        FakeIntentLLM({"action": "continue_current", "reason": "Continue."}),
        "intent-worker",
        heartbeat_seconds=3600,
        stale_seconds=60,
        max_runs_per_project=100,
    )

    await processor.process_one()

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, request.id)
        conversation = await session.get(Conversation, request.conversation_id)
        runs = list(
            (await session.execute(select(Run).where(Run.project_id == project.id))).scalars()
        )
    assert turn.request_status == ChatRequestStatus.NEEDS_SELECTION
    assert turn.intent_result["action"] == "request_selection"
    assert turn.intent_result["reason"]
    assert conversation.context_version == 4
    assert conversation.active_feature_run_id == feature.id
    assert len(runs) == 1


def _manual_selection(
    client: TestClient, headers: dict[str, str], turn_id: str, version: int, **body
):
    return client.post(
        f"/api/v1/chat/messages/{turn_id}/selection",
        headers=headers,
        json={"context_version": version, **body},
    )


def test_manual_selection_changes_project_and_feature_only_on_confirmation(
    client: TestClient,
) -> None:
    token = register_and_login(client, "selection@example.com")
    headers = auth_headers(token)
    current = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Current", "summary": ""}
    ).json()
    seed = client.post(
        "/api/v1/runs",
        headers=headers,
        json={"project_id": current["id"], "message": "Start feature"},
    ).json()
    other = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Other", "summary": ""}
    ).json()
    other_seed = client.post(
        "/api/v1/runs",
        headers=headers,
        json={"project_id": other["id"], "message": "Other feature"},
    ).json()
    client.get("/api/v1/chat", headers=headers)
    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE runs SET status='completed' WHERE id IN (%s, %s)",
            (seed["run_id"], other_seed["run_id"]),
        )
        connection.execute(
            "UPDATE runs SET plan=%s::jsonb, latest_code=%s::jsonb, feedback=%s::jsonb, checks=%s::jsonb WHERE id=%s",
            (
                json.dumps({"summary": "current project plan"}),
                json.dumps([{"file_path": "current.html", "content": "current"}]),
                json.dumps({"summary": "current feedback"}),
                json.dumps({"ok": "current"}),
                seed["run_id"],
            ),
        )
        connection.execute(
            "UPDATE runs SET plan=%s::jsonb, latest_code=%s::jsonb, feedback=%s::jsonb, checks=%s::jsonb WHERE id=%s",
            (
                json.dumps({"summary": "older project plan"}),
                json.dumps([{"file_path": "older.html", "content": "older"}]),
                json.dumps({"summary": "older feedback"}),
                json.dumps({"ok": "older"}),
                other_seed["run_id"],
            ),
        )
        connection.execute(
            "UPDATE conversations SET selected_project_id=%s, active_feature_run_id=%s, context_version=3",
            (current["id"], seed["run_id"]),
        )
        connection.commit()

    posted = client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "Add settings", "request_selection": True},
    )
    turn_id = posted.json()["turn_id"]
    pending = client.get("/api/v1/chat", headers=headers).json()["pending_selection"]
    assert posted.status_code == 202
    assert pending["reason"]
    assert {project["id"] for project in pending["projects"]} == {current["id"], other["id"]}

    new_feature = _manual_selection(
        client,
        headers,
        turn_id,
        3,
        choice="existing_project",
        feature_mode="new",
        project_id=current["id"],
    )
    assert new_feature.status_code == 202
    first = new_feature.json()
    chat = client.get("/api/v1/chat", headers=headers).json()
    assert chat["selected_project_id"] == current["id"]
    assert chat["active_feature_run_id"] == first["run_id"]
    assert chat["context_version"] == first["context_version"] == 4
    first_run = client.get(f"/api/v1/runs/{first['run_id']}", headers=headers).json()
    assert first_run["plan"] is None
    assert first_run["latest_code"] == []

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute("UPDATE runs SET status='completed' WHERE id=%s", (first["run_id"],))
        connection.commit()
    next_turn = client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "Continue this feature", "request_selection": True},
    ).json()
    current_selection = _manual_selection(
        client,
        headers,
        next_turn["turn_id"],
        4,
        choice="continue_current",
        feature_mode="continue",
    )
    assert current_selection.status_code == 202
    assert current_selection.json()["context_version"] == 4
    chat = client.get("/api/v1/chat", headers=headers).json()
    assert chat["selected_project_id"] == current["id"]
    assert chat["active_feature_run_id"] == first["run_id"]

    with psycopg.connect(Settings().psycopg_url) as connection:
        connection.execute(
            "UPDATE runs SET status='completed' WHERE id=%s", (current_selection.json()["run_id"],)
        )
        connection.commit()
    switch_turn = client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "Work on the other project", "request_selection": True},
    ).json()
    switched = _manual_selection(
        client,
        headers,
        switch_turn["turn_id"],
        4,
        choice="existing_project",
        feature_mode="continue",
        project_id=other["id"],
    )
    assert switched.status_code == 202
    switched_chat = client.get("/api/v1/chat", headers=headers).json()
    assert switched_chat["selected_project_id"] == other["id"]
    assert switched_chat["active_feature_run_id"] == other_seed["run_id"]
    assert switched_chat["context_version"] == 5
    switched_run = client.get(f"/api/v1/runs/{switched.json()['run_id']}", headers=headers).json()
    assert switched_run["plan"] == {"summary": "older project plan"}
    assert switched_run["latest_code"] == [{"file_path": "older.html", "content": "older"}]
    assert switched_run["feedback"] == {"summary": "older feedback"}
    assert switched_run["checks"] == {"ok": "older"}

    blocked_turn = client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "Queue another run", "request_selection": True},
    ).json()
    with psycopg.connect(Settings().psycopg_url) as connection:
        runs_before_conflict = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    blocked = _manual_selection(
        client,
        headers,
        blocked_turn["turn_id"],
        5,
        choice="continue_current",
        feature_mode="continue",
    )
    assert blocked.status_code == 409
    blocked_chat = client.get("/api/v1/chat", headers=headers).json()
    assert blocked_chat["pending_selection"]["turn_id"] == blocked_turn["turn_id"]
    assert blocked_chat["pending_selection"]["reason"] == "A run is already active for this project"
    assert blocked_chat["context_version"] == 5
    with psycopg.connect(Settings().psycopg_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == runs_before_conflict


def test_foreign_project_selection_keeps_pending_request(client: TestClient) -> None:
    alice = register_and_login(client, "alice-selection@example.com")
    bob = register_and_login(client, "bob-selection@example.com")
    alice_headers = auth_headers(alice)
    bob_headers = auth_headers(bob)
    foreign = client.post(
        "/api/v1/projects", headers=bob_headers, json={"title": "Private", "summary": ""}
    ).json()
    client.get("/api/v1/chat", headers=alice_headers)
    posted = client.post(
        "/api/v1/chat/messages",
        headers=alice_headers,
        json={"message": "Use that project", "request_selection": True},
    ).json()
    pending = client.get("/api/v1/chat", headers=alice_headers).json()["pending_selection"]

    response = _manual_selection(
        client,
        alice_headers,
        posted["turn_id"],
        pending["context_version"],
        choice="existing_project",
        feature_mode="continue",
        project_id=foreign["id"],
    )

    assert response.status_code == 404
    chat = client.get("/api/v1/chat", headers=alice_headers).json()
    assert chat["pending_selection"]["turn_id"] == posted["turn_id"]
    assert chat["selected_project_id"] is None
