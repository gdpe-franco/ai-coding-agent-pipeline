from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from app.application.chat import ChatRequestProcessor
from app.application.processor import RunProcessor
from app.infrastructure.container import Container
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.settings import Settings

logger = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    container = Container(settings)
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    logger.info("worker %s starting", worker_id)
    try:
        async with AsyncPostgresSaver.from_conn_string(settings.psycopg_url) as saver:
            await saver.setup()
            async with AsyncPostgresStore.from_conn_string(settings.psycopg_url) as store:
                await store.setup()
                graph = container.build_graph(saver)
                processor = RunProcessor(
                    repo_factory=container.repository_context,
                    graph=graph,
                    workspace=container.workspace,
                    memory=container.memory,
                    settings=settings,
                    worker_id=worker_id,
                )
                chat_processor = ChatRequestProcessor(
                    repo_factory=container.repository_context,
                    llm=container.llm_clients["planner"],
                    worker_id=worker_id,
                    heartbeat_seconds=settings.worker_heartbeat_seconds,
                    stale_seconds=int(settings.worker_stale_seconds),
                    max_runs_per_project=settings.max_runs_per_project,
                )
                while True:
                    if await chat_processor.process_one():
                        continue
                    async with container.session_factory() as session:
                        repo = SqlAlchemyRepository(session)
                        run = await repo.claim(
                            worker_id, stale_after_seconds=int(settings.worker_stale_seconds)
                        )
                        await repo.heartbeat(worker_id, run.id if run else None)
                    if run is None:
                        await asyncio.sleep(settings.worker_poll_seconds)
                        continue
                    logger.info("worker %s claimed run %s", worker_id, run.id)
                    await processor.process(run)
    finally:
        await container.close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_worker(Settings()))


if __name__ == "__main__":
    main()
