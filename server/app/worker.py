from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from app.application.chat import ChatRequestProcessor
from app.application.processor import RunProcessor
from app.domain.models import RunState
from app.infrastructure.container import Container
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.settings import Settings

logger = logging.getLogger(__name__)
RUN_CONCURRENCY = 2
SHUTDOWN_GRACE_SECONDS = 10.0


async def _run_claim_loop(
    chat_processor: ChatRequestProcessor,
    processor: RunProcessor,
    claim_run: Callable[[], Awaitable[RunState | None]],
    release_run: Callable[[UUID], Awaitable[None]],
    stop_event: asyncio.Event,
    poll_seconds: float,
    *,
    concurrency: int = RUN_CONCURRENCY,
    shutdown_grace_seconds: float = SHUTDOWN_GRACE_SECONDS,
) -> None:
    active_runs: set[asyncio.Task[None]] = set()
    chat_task: asyncio.Task[bool] | None = None
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        while not stop_event.is_set():
            finished = {task for task in active_runs if task.done()}
            active_runs.difference_update(finished)
            for task in finished:
                task.result()

            chat_idle = False
            if chat_task is not None and chat_task.done():
                if chat_task.result():
                    chat_task = None
                    continue
                chat_task = None
                chat_idle = True

            if chat_task is None and not chat_idle and len(active_runs) < concurrency:
                chat_task = asyncio.create_task(chat_processor.process_one())

            if len(active_runs) < concurrency:
                run = await claim_run()
                if run is not None:
                    if stop_event.is_set():
                        await release_run(run.id)
                        break
                    active_runs.add(asyncio.create_task(processor.process(run)))
                    continue

            waiting: set[asyncio.Task] = {stop_task, *active_runs}
            if chat_task is not None:
                waiting.add(chat_task)
            await asyncio.wait(waiting, timeout=poll_seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        outstanding = [*active_runs]
        if chat_task is not None:
            outstanding.append(chat_task)
        if outstanding:
            _, pending = await asyncio.wait(outstanding, timeout=shutdown_grace_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*outstanding, return_exceptions=True)


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

                async def claim_run() -> RunState | None:
                    async with container.session_factory() as session:
                        repo = SqlAlchemyRepository(session)
                        run = await repo.claim(
                            worker_id, stale_after_seconds=int(settings.worker_stale_seconds)
                        )
                        await repo.heartbeat(worker_id, run.id if run else None)
                    if run is not None:
                        logger.info("worker %s claimed run %s", worker_id, run.id)
                    return run

                async def release_run(run_id: UUID) -> None:
                    async with container.session_factory() as session:
                        await SqlAlchemyRepository(session).release(run_id)

                loop = asyncio.get_running_loop()
                stop_event = asyncio.Event()
                handled_signals: list[signal.Signals] = []
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, stop_event.set)
                    except NotImplementedError, RuntimeError:
                        continue
                    handled_signals.append(sig)
                try:
                    await _run_claim_loop(
                        chat_processor,
                        processor,
                        claim_run,
                        release_run,
                        stop_event,
                        settings.worker_poll_seconds,
                    )
                finally:
                    for sig in handled_signals:
                        loop.remove_signal_handler(sig)
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
