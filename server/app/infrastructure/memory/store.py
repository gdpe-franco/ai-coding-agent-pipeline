from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from langgraph.store.postgres import AsyncPostgresStore

from app.application.ports import MemoryStore


class LangGraphMemoryStore(MemoryStore):
    def __init__(self, psycopg_url: str) -> None:
        self.psycopg_url = psycopg_url
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            await store.setup()
        self._initialized = True

    @staticmethod
    def _user_namespace(owner_id: UUID) -> tuple[str, str, str]:
        return ("user", str(owner_id), "projects")

    @staticmethod
    def _project_namespace(project_id: UUID) -> tuple[str, str, str]:
        return ("project", str(project_id), "memory")

    async def load_project_memory(self, owner_id: UUID, project_id: UUID) -> str:
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            item = await store.aget(self._project_namespace(project_id), "summary")
        if item is None:
            return ""
        value = item.value if hasattr(item, "value") else item
        if isinstance(value, dict):
            return str(value.get("text", ""))
        return str(value)

    async def project_index(self, owner_id: UUID) -> list[dict[str, Any]]:
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            items = await store.asearch(self._user_namespace(owner_id), limit=100)
        result: list[dict[str, Any]] = []
        for item in items:
            value = item.value if hasattr(item, "value") else item
            if isinstance(value, dict):
                result.append(value)
        return result

    async def recent_runs(self, owner_id: UUID, limit: int = 10) -> list[dict[str, Any]]:
        return []

    async def save_round(
        self,
        owner_id: UUID,
        project_id: UUID,
        title: str | None,
        outcome: str,
        summary: str = "",
        last_feature: str = "",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        index_value = {
            "id": str(project_id),
            "title": title or "Untitled project",
            "summary": summary,
            "last_feature": last_feature,
            "last_outcome": outcome,
            "updated_at": now,
        }
        memory_value = {"text": summary or last_feature or "No confirmed project memory yet."}
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            await store.aput(self._user_namespace(owner_id), str(project_id), index_value)
            await store.aput(self._project_namespace(project_id), "summary", memory_value)

    async def delete_project(self, owner_id: UUID, project_id: UUID) -> None:
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            try:
                await store.adelete(self._user_namespace(owner_id), str(project_id))
                await store.adelete(self._project_namespace(project_id), "summary")
            except Exception:  # noqa: BLE001 - absent memory is not an error
                return
