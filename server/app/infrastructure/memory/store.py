from __future__ import annotations

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
    def _project_namespace(project_id: UUID) -> tuple[str, str, str]:
        return ("project", str(project_id), "memory")

    async def load_project_memory(self, project_id: UUID) -> str:
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            item = await store.aget(self._project_namespace(project_id), "summary")
        if item is None:
            return ""
        value = item.value if hasattr(item, "value") else item
        if isinstance(value, dict):
            return str(value.get("text", ""))
        return str(value)

    async def save_project_memory(self, project_id: UUID, summary: str) -> None:
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            item = await store.aget(self._project_namespace(project_id), "summary")
            value = item.value if item is not None and hasattr(item, "value") else item
            previous = str(value.get("text", "")) if isinstance(value, dict) else ""
            text = summary.strip()
            if previous and text not in previous:
                text = f"{previous}\n\n{text}"
            # shortcut: keep the latest 4000 chars; upgrade to structured memory for selective retention.
            memory_value = {"text": text[-4000:] or "No confirmed project memory yet."}
            await store.aput(self._project_namespace(project_id), "summary", memory_value)

    async def delete_project(self, project_id: UUID) -> None:
        async with AsyncPostgresStore.from_conn_string(self.psycopg_url) as store:
            try:
                await store.adelete(self._project_namespace(project_id), "summary")
            except Exception:  # noqa: BLE001 - absent memory is not an error
                return
