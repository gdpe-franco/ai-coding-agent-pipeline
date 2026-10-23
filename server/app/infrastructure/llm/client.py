from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI, BadRequestError
from openai.types.chat import ChatCompletion

from app.application.ports import LLMClient
from app.domain.errors import MissingCredentialsError, ModelError


def _rejects_temperature(error: BadRequestError) -> bool:
    body = error.body if isinstance(error.body, dict) else {}
    return body.get("code") == "unsupported_value" and body.get("param") == "temperature"


class OpenAICompatibleClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 120.0,
        unsupported_temperature: set[str] | None = None,
    ) -> None:
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._unsupported_temperature = (
            unsupported_temperature if unsupported_temperature is not None else set()
        )

    async def _completion(
        self, role: str, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> ChatCompletion:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{role}_response",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if self.model not in self._unsupported_temperature:
            kwargs["temperature"] = 0
        try:
            return await self.client.chat.completions.create(**kwargs)
        except BadRequestError as exc:
            if "temperature" not in kwargs or not _rejects_temperature(exc):
                raise
            self._unsupported_temperature.add(self.model)
            kwargs.pop("temperature")
            return await self.client.chat.completions.create(**kwargs)

    async def complete(
        self, role: str, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                request_messages = list(messages)
                if attempt == 1:
                    request_messages.append(
                        {
                            "role": "user",
                            "content": "Return only valid JSON matching the supplied schema.",
                        }
                    )
                response = await self._completion(role, request_messages, schema)
                content = response.choices[0].message.content
                if not content:
                    raise ModelError(f"{role} returned an empty response", tag=f"{role}_error")
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ModelError(f"{role} returned non-object JSON", tag=f"{role}_error")
                return parsed
            except Exception as exc:  # noqa: BLE001 - normalized as a model error
                last_error = exc
        raise ModelError(
            f"{role} model call failed: {last_error}", tag=f"{role}_error"
        ) from last_error


class UnavailableLLMClient(LLMClient):
    def __init__(self, setting: str) -> None:
        self.setting = setting

    async def complete(
        self, role: str, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]:
        raise MissingCredentialsError(self.setting)


class ProviderRegistry:
    def __init__(self, settings) -> None:
        self.settings = settings

    def _deployment(self, role: str) -> str:
        return str(getattr(self.settings, f"azure_openai_deployment_{role}", "") or "").strip()

    def _missing_setting(self, role: str) -> str | None:
        if not self.settings.azure_openai_api_key:
            return "AZURE_OPENAI_API_KEY"
        if not self.settings.azure_openai_endpoint:
            return "AZURE_OPENAI_ENDPOINT"
        if not self._deployment(role):
            return f"AZURE_OPENAI_DEPLOYMENT_{role.upper()}"
        return None

    def build(self) -> dict[str, LLMClient]:
        clients: dict[str, LLMClient] = {}
        unsupported_temperature: set[str] = set()
        for role in ("planner", "coder", "reviewer", "resolver"):
            deployment_role = "planner" if role == "resolver" else role
            missing = self._missing_setting(deployment_role)
            if missing is not None:
                clients[role] = UnavailableLLMClient(missing)
                continue
            clients[role] = OpenAICompatibleClient(
                api_key=self.settings.azure_openai_api_key,
                base_url=self.settings.azure_openai_endpoint,
                model=self._deployment(deployment_role),
                unsupported_temperature=unsupported_temperature,
            )
        return clients
