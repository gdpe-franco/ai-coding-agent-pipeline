from __future__ import annotations

import asyncio
from uuid import uuid4

from app.domain.enums import RunStatus
from app.domain.models import RunState
from app.worker import _run_claim_loop


def run_state() -> RunState:
    return RunState(
        id=uuid4(),
        owner_id=uuid4(),
        conversation_id=uuid4(),
        project_id=uuid4(),
        message="build",
        status=RunStatus.RUNNING,
    )


class IdleChatProcessor:
    async def process_one(self) -> bool:
        await asyncio.sleep(0)
        return False


class PausedRunProcessor:
    def __init__(self, finish: asyncio.Event | None = None) -> None:
        self.finish = finish if finish is not None else asyncio.Event()
        self.started = asyncio.Event()
        self.two_started = asyncio.Event()
        self.active = 0
        self.max_active = 0
        self.finished: list[RunState] = []
        self.cancelled: list[RunState] = []

    async def process(self, run: RunState) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        if self.active == 2:
            self.two_started.set()
        try:
            await self.finish.wait()
            self.finished.append(run)
        except asyncio.CancelledError:
            self.cancelled.append(run)
            raise
        finally:
            self.active -= 1


async def test_worker_runs_two_projects_concurrently_and_stops_claiming() -> None:
    runs = [run_state(), run_state()]
    claimed: list[RunState] = []
    processor = PausedRunProcessor()
    stop = asyncio.Event()

    async def claim() -> RunState | None:
        if not runs:
            return None
        run = runs.pop(0)
        claimed.append(run)
        return run

    async def release(_run_id) -> None:
        raise AssertionError("already-claimed work should finish during the shutdown grace period")

    worker = asyncio.create_task(
        _run_claim_loop(
            IdleChatProcessor(),
            processor,
            claim,
            release,
            stop,
            poll_seconds=0.01,
            shutdown_grace_seconds=1,
        )
    )
    await asyncio.wait_for(processor.two_started.wait(), timeout=1)
    stop.set()
    await asyncio.sleep(0.01)
    processor.finish.set()
    await asyncio.wait_for(worker, timeout=1)

    assert len(claimed) == 2
    assert claimed[0].project_id != claimed[1].project_id
    assert processor.max_active == 2
    assert len(processor.finished) == 2
    assert processor.cancelled == []


async def test_worker_releases_a_run_claimed_as_shutdown_starts() -> None:
    run = run_state()
    stop = asyncio.Event()
    released: list = []
    processor = PausedRunProcessor()

    async def claim() -> RunState:
        stop.set()
        return run

    async def release(run_id) -> None:
        released.append(run_id)

    await _run_claim_loop(
        IdleChatProcessor(),
        processor,
        claim,
        release,
        stop,
        poll_seconds=0.01,
        shutdown_grace_seconds=0.1,
    )

    assert released == [run.id]
    assert processor.active == 0


async def test_worker_cancels_remaining_work_after_shutdown_grace() -> None:
    run = run_state()
    processor = PausedRunProcessor()
    stop = asyncio.Event()
    claimed = False

    async def claim() -> RunState | None:
        nonlocal claimed
        if claimed:
            return None
        claimed = True
        return run

    async def release(_run_id) -> None:
        raise AssertionError("the processor should release its own in-flight run")

    worker = asyncio.create_task(
        _run_claim_loop(
            IdleChatProcessor(),
            processor,
            claim,
            release,
            stop,
            poll_seconds=0.01,
            shutdown_grace_seconds=0.01,
        )
    )
    await asyncio.wait_for(processor.started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(worker, timeout=1)

    assert processor.cancelled == [run]
    assert processor.active == 0
