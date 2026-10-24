from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cached_property

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.graph import CodingGraph
from app.infrastructure.llm.client import ProviderRegistry
from app.infrastructure.memory.store import LangGraphMemoryStore
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.infrastructure.rate_limit.postgres import PostgresRateLimiter
from app.infrastructure.workspace.web import WebWorkspace
from app.settings import Settings


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.workspace = WebWorkspace(
            settings.data_dir,
            node_binary=settings.node_binary,
            timeout=settings.js_check_timeout_seconds,
        )
        self.memory = LangGraphMemoryStore(settings.psycopg_url)
        self.rate_limiter = PostgresRateLimiter(self.session_factory, settings.secret_key)
        self.llm_clients = ProviderRegistry(settings).build()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)

    def repository(self, session) -> SqlAlchemyRepository:
        return SqlAlchemyRepository(session)

    @asynccontextmanager
    async def repository_context(self) -> AsyncIterator[SqlAlchemyRepository]:
        async with self.session_factory() as session:
            yield self.repository(session)

    @cached_property
    def graph_without_checkpointer(self) -> CodingGraph:
        return self.build_graph(None)

    def build_graph(self, checkpointer) -> CodingGraph:
        return CodingGraph(
            llm_clients=self.llm_clients,
            workspace=self.workspace,
            memory=self.memory,
            repo_factory=self.repository_context,
            max_coder_generations=self.settings.max_coder_generations,
            checkpointer=checkpointer,
        )

    async def close(self) -> None:
        await self.engine.dispose()
