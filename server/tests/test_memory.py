from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.infrastructure.memory.store import LangGraphMemoryStore


class FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, str, str], str], dict] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def aget(self, namespace, key):
        value = self.items.get((namespace, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def aput(self, namespace, key, value):
        self.items[(namespace, key)] = value

    async def adelete(self, namespace, key):
        self.items.pop((namespace, key), None)


async def test_project_memory_appends_approved_context_and_deletes_by_project(monkeypatch) -> None:
    store = FakeStore()
    monkeypatch.setattr(
        "app.infrastructure.memory.store.AsyncPostgresStore.from_conn_string",
        lambda _url: store,
    )
    memory = LangGraphMemoryStore("postgresql://test")
    project_id = uuid4()

    await memory.save_project_memory(project_id, "Coupon flow uses unique codes.")
    await memory.save_project_memory(project_id, "Added expiry dates to coupon validation.")

    saved = await memory.load_project_memory(project_id)
    assert "Coupon flow uses unique codes." in saved
    assert "Added expiry dates" in saved
    assert len(saved) <= 4000
    await memory.delete_project(project_id)
    assert await memory.load_project_memory(project_id) == ""
