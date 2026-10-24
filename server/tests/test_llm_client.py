from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import BadRequestError

from app.domain.errors import MissingCredentialsError, ModelError
from app.infrastructure.llm.client import (
    OpenAICompatibleClient,
    ProviderRegistry,
    UnavailableLLMClient,
)
from app.settings import Settings

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def bad_request(code: str, param: str | None) -> BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
    return BadRequestError("bad request", response=response, body={"code": code, "param": param})


class FakeCompletions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        content, usage = outcome if isinstance(outcome, tuple) else (outcome, None)
        usage_result = (
            SimpleNamespace(
                prompt_tokens=usage[0], completion_tokens=usage[1], total_tokens=usage[2]
            )
            if usage is not None
            else None
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=usage_result,
        )


def make_client(completions: FakeCompletions, shared: set[str] | None = None) -> Any:
    client = OpenAICompatibleClient(
        api_key="test",
        model="gpt-5-mini",
        base_url="https://example.test",
        unsupported_temperature=shared,
    )
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client


async def test_temperature_fallback_is_shared_across_clients() -> None:
    shared: set[str] = set()
    first = FakeCompletions([bad_request("unsupported_value", "temperature"), '{"ok": true}'])
    result = await make_client(first, shared).complete("reviewer", [], SCHEMA)
    assert result.content == {"ok": True}
    assert [call["temperature"] for call in first.calls if "temperature" in call] == [0]
    assert "temperature" not in first.calls[1]
    assert shared == {"gpt-5-mini"}

    second = FakeCompletions(['{"ok": true}'])
    await make_client(second, shared).complete("planner", [], SCHEMA)
    assert "temperature" not in second.calls[0]


async def test_other_bad_requests_keep_temperature() -> None:
    shared: set[str] = set()
    completions = FakeCompletions(
        [bad_request("invalid_request_error", None), bad_request("invalid_request_error", None)]
    )

    with pytest.raises(ModelError):
        await make_client(completions, shared).complete("coder", [], SCHEMA)

    assert all(call["temperature"] == 0 for call in completions.calls)
    assert shared == set()


async def test_usage_includes_repaired_responses() -> None:
    completions = FakeCompletions(
        [
            ("not json", (2, 1, 3)),
            ('{"ok": true}', (5, 3, 8)),
        ]
    )

    result = await make_client(completions).complete("coder", [], SCHEMA)

    assert result.content == {"ok": True}
    assert result.model_responses == 2
    assert result.duration_ms >= 0
    assert result.usage_complete is True
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.total_tokens) == (
        7,
        4,
        11,
    )


async def test_failed_repair_preserves_available_usage() -> None:
    completions = FakeCompletions([("bad json", (2, 1, 3)), ("still bad", (5, 3, 8))])

    with pytest.raises(ModelError) as caught:
        await make_client(completions).complete("coder", [], SCHEMA)

    assert caught.value.telemetry["usage"] == {
        "input_tokens": 7,
        "output_tokens": 4,
        "total_tokens": 11,
    }
    assert caught.value.telemetry["model_responses"] == 2


async def test_failed_provider_request_marks_usage_incomplete() -> None:
    completions = FakeCompletions([("bad json", (2, 1, 3)), RuntimeError("unavailable")])

    with pytest.raises(ModelError) as caught:
        await make_client(completions).complete("coder", [], SCHEMA)

    assert caught.value.telemetry["usage"]["total_tokens"] == 3
    assert caught.value.telemetry["usage_complete"] is False


def make_settings(**overrides: Any) -> Any:
    values = {
        "app_env": "development",
        "secret_key": "test-secret",
        "azure_openai_api_key": "super-secret-key",
        "azure_openai_endpoint": "https://example.test/openai/v1/",
        "azure_openai_deployment_planner": "planner-deployment",
        "azure_openai_deployment_coder": "coder-deployment",
        "azure_openai_deployment_reviewer": "reviewer-deployment",
    }
    values.update(overrides)
    return Settings(**values)


def test_registry_uses_azure_deployments_per_role() -> None:
    clients = ProviderRegistry(make_settings()).build()

    assert all(isinstance(client, OpenAICompatibleClient) for client in clients.values())
    assert clients["planner"].model == "planner-deployment"
    assert clients["coder"].model == "coder-deployment"
    assert clients["reviewer"].model == "reviewer-deployment"


@pytest.mark.parametrize(
    ("overrides", "role", "setting"),
    [
        ({"azure_openai_api_key": ""}, "planner", "AZURE_OPENAI_API_KEY"),
        ({"azure_openai_endpoint": ""}, "planner", "AZURE_OPENAI_ENDPOINT"),
        ({"azure_openai_deployment_coder": ""}, "coder", "AZURE_OPENAI_DEPLOYMENT_CODER"),
    ],
    ids=["api-key", "endpoint", "deployment"],
)
async def test_registry_reports_missing_setting_without_exposing_credentials(
    overrides: dict[str, str], role: str, setting: str
) -> None:
    clients = ProviderRegistry(make_settings(**overrides)).build()

    assert isinstance(clients[role], UnavailableLLMClient)
    assert clients[role].setting == setting
    with pytest.raises(MissingCredentialsError, match=setting) as error:
        await clients[role].complete(role, [], SCHEMA)
    assert "super-secret-key" not in str(error.value)
