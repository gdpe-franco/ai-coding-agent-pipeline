from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.application.ports import LLMClient
from app.domain.errors import MissingCredentialsError, ModelError


class OpenAICompatibleClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

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
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=request_messages,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": f"{role}_response",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    temperature=0,
                )
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

    def _provider(self) -> str:
        provider = self.settings.model_provider.strip().lower()
        if provider:
            return provider
        if self._azure_ready("planner"):
            return "azure_openai"
        if self.settings.openai_api_key:
            return "openai"
        return "unavailable"

    def _azure_deployment(self, role: str) -> str:
        return str(getattr(self.settings, f"azure_openai_deployment_{role}", "") or "").strip()

    def _azure_ready(self, role: str) -> bool:
        return bool(
            self.settings.azure_openai_api_key
            and self.settings.azure_openai_endpoint
            and self._azure_deployment(role)
        )

    def build(self) -> dict[str, LLMClient]:
        clients: dict[str, LLMClient] = {}
        provider = self._provider()
        for role in ("planner", "coder", "reviewer", "resolver"):
            if provider == "azure_openai":
                deployment_role = "planner" if role == "resolver" else role
                deployment = self._azure_deployment(deployment_role)
                if not (
                    self.settings.azure_openai_api_key
                    and self.settings.azure_openai_endpoint
                    and deployment
                ):
                    clients[role] = UnavailableLLMClient(
                        f"AZURE_OPENAI_DEPLOYMENT_{deployment_role.upper()}"
                    )
                    continue
                clients[role] = OpenAICompatibleClient(
                    api_key=self.settings.azure_openai_api_key,
                    base_url=self.settings.azure_openai_endpoint,
                    model=deployment,
                )
            elif provider == "openai":
                if not self.settings.openai_api_key:
                    clients[role] = UnavailableLLMClient("OPENAI_API_KEY")
                    continue
                clients[role] = OpenAICompatibleClient(
                    api_key=self.settings.openai_api_key,
                    model=self.settings.model_name or "gpt-4o-mini",
                )
            else:
                clients[role] = UnavailableLLMClient("AZURE_OPENAI_API_KEY or OPENAI_API_KEY")
        return clients
