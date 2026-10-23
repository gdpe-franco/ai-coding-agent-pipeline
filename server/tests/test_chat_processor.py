from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.application.chat import ChatRequestProcessor, bounded_recent_turns
from app.domain.enums import ChatIntentAction
from app.domain.models import ChatIntentRequest


def test_bounded_recent_turns_keeps_only_newest_ten_within_character_limit() -> None:
    turns = [{"role": "user", "content": f"{index}:" + "x" * 600} for index in range(12)]

    result = bounded_recent_turns(turns)

    assert len(result) == 7
    assert sum(len(turn["content"]) for turn in result) <= 4000
    remaining = 4000 - sum(len(turn["content"]) for turn in turns[-6:])
    assert result[0]["content"] == turns[5]["content"][-remaining:]
    assert result[-1]["content"].endswith("x" * 600)


class FakeRepository:
    def __init__(self, request: ChatIntentRequest) -> None:
        self.request = request
        self.claimed = False
        self.resolution = None

    async def claim_chat_request(self, worker_id: str, stale_after_seconds: int):
        if self.claimed:
            return None
        self.claimed = True
        return self.request

    async def heartbeat_chat_request(self, request_id, worker_id):
        pass

    async def heartbeat(self, worker_id: str, run_id=None):
        pass

    async def resolve_chat_request(self, *args, **kwargs):
        self.resolution = (args, kwargs)


class FakeLLM:
    def __init__(self, response) -> None:
        self.response = response
        self.call = None

    async def complete(self, role, messages, schema):
        self.call = (role, messages, schema)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def intent_request() -> ChatIntentRequest:
    return ChatIntentRequest(
        id=uuid4(),
        owner_id=uuid4(),
        message="Add a settings screen",
        context_version=2,
        project_id=uuid4(),
        active_feature_run_id=uuid4(),
        project_title="Sample app",
        feature_title="Build a dashboard",
        recent_turns=[{"role": "user", "content": "Create an app"}],
    )


def _processor(repo, llm) -> ChatRequestProcessor:
    @asynccontextmanager
    async def repo_factory():
        yield repo

    return ChatRequestProcessor(
        repo_factory=repo_factory,
        llm=llm,
        worker_id="test-worker",
        heartbeat_seconds=3600,
        stale_seconds=60,
        max_runs_per_project=100,
    )


@pytest.mark.asyncio
async def test_intent_detector_uses_planner_schema_without_project_targets(intent_request) -> None:
    repo = FakeRepository(intent_request)
    llm = FakeLLM({"action": "continue_current", "reason": "This extends the dashboard."})

    assert await _processor(repo, llm).process_one()

    role, messages, schema = llm.call
    assert role == "planner"
    assert set(schema["properties"]) == {"action", "reason"}
    assert str(intent_request.project_id) not in str(messages)
    assert repo.resolution[0][3] == ChatIntentAction.CONTINUE_CURRENT


@pytest.mark.asyncio
async def test_invalid_intent_output_falls_back_to_manual_selection(intent_request) -> None:
    repo = FakeRepository(intent_request)
    llm = FakeLLM({"action": "choose_project", "reason": ""})

    assert await _processor(repo, llm).process_one()

    args, kwargs = repo.resolution
    assert args[3] == ChatIntentAction.REQUEST_SELECTION
    assert "choose" in args[4].lower()
    assert kwargs["intent_error"] is True


@pytest.mark.asyncio
async def test_missing_project_context_skips_intent_model(intent_request) -> None:
    intent_request.project_id = None
    intent_request.project_title = None
    repo = FakeRepository(intent_request)
    llm = FakeLLM({"action": "continue_current", "reason": "ignored"})

    assert await _processor(repo, llm).process_one()

    assert llm.call is None
    assert repo.resolution[0][3] == ChatIntentAction.REQUEST_SELECTION
