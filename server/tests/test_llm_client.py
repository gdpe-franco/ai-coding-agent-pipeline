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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])


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
    assert await make_client(first, shared).complete("reviewer", [], SCHEMA) == {"ok": True}
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


def make_settings(**overrides: Any) -> Any:
    values = {
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
