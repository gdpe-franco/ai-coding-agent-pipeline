# AGENTS.md

## Project memory

- Read `docs/architecture.md` (TDD) for stack, contracts, schema, and rationale. Keep contributor conventions here; update existing sections when decisions change. Link rather than duplicate architecture in README/PRD.
- `tasks/` is ignored scratch for PRDs and briefs, not durable memory. The PRD owns user stories/acceptance criteria only. `shared/` is reserved.
- Backend: `server/app/{domain,application,infrastructure,interfaces}`; tests: `server/tests/`; evals: `server/evals/`; SPA: `client/`. Entrypoints: `app.main:create_app()` and `app.worker`.
- Azure is the only provider and has been live-smoke-tested with local credentials; never commit them or invent deployment names. API budget policy remains undecided (TDD §13.2).

## Commands and verification

From repo root:

```bash
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
curl -fsS http://localhost:8000/health
```

From `server/`:

```bash
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:create_app --factory --reload
python -m app.worker
ruff check . && ruff format --check .
pytest
python -m evals.runner
```

- Run backend checks from `server/`; `server/pyproject.toml` is the sole config (Python 3.14, line length 100, `asyncio_mode=auto`). `packages = []` installs dependencies only; the working directory makes `app` importable.
- Ruff, pytest, and evals are the verification gate; no CI or pre-commit hooks exist. Skipped PostgreSQL tests do not satisfy it. Deterministic tests never call live models.
- Client: `npm ci && npm run dev` from `client/` (5173; API 8000). Docker builds the SPA and serves it through FastAPI; runtime Node is required for `node --check`. Compose has `db`, `app`, `worker`, with a shared `appdata` workspace volume; run migrations explicitly.

## Code and tests

- Apply Ponytail's smallest-complete-change principle even without its plugin/skills: reuse existing code or the standard library; add dependencies, abstractions, and configuration only for current requirements.
- Use built-in generics, `X | None`, `StrEnum`, and typed boundaries. Ruff owns formatting/linting; avoid competing tooling.
- Shared fixtures belong in `server/tests/conftest.py`; module-only fixtures stay local. Parametrize matching setup/assertion shapes; avoid giant scenario tests and helpers that hide behavior. No explicit `@pytest.mark.asyncio` is needed.
- Test application policy with fake ports and persistence/API behavior with PostgreSQL. Assert observable outcomes, ownership, and failure preservation—not implementation details (TDD §11.1).

## Architecture boundaries

- Dependencies point inward: interfaces → application → domain; infrastructure implements application ports. Application imports no infrastructure/interfaces; domain imports no frameworks, SDKs, or I/O.
- Use narrow `typing.Protocol` ports and manual adapter wiring in `infrastructure/container.py`; no mutable globals. Domain models are dataclasses; Pydantic v2 validates HTTP/model/checkpoint boundaries. Adapter recipe: TDD §4.4.
- Credentials, provider URLs, and SDK types stay in infrastructure. Missing credentials produce masked setting-specific errors. Azure OpenAI is the only provider in the MVP; `model` means deployment name. Deployment/credential configuration is developer-only; no user settings or model-listing endpoints.

## Workflow guardrails

- One owner-scoped private chat per user; projects are chat context, not separate conversations. Intent detection may request selection but never chooses/creates a project. Only explicit user confirmation changes context or authorizes creation.
- Keep request routing (`conversation_turns.request_status`) separate from coding execution (`runs.status`). Snapshots/version prevent stale context; `intent_result` explains routing; locks/heartbeats enable recovery. Do not add overlapping flags/statuses (TDD §5.5, §8.1).
- Detector errors preserve context and open `needs_selection` with `intent_error`. Request `failed` is reserved for failures that cannot provide a usable menu. Coding-run model/schema errors go through `run_end` to `failed`, without model-error retry.
- Graph order: planner → coder → reviewer. At most three Coder generations per round; increment attempts once per invocation before checks. No human input mid-round. Third rejection or valid `escalate` yields `awaiting_user`, preserving files, issues, round, and both counts.
- Resume keeps run/project, increments `round`, resets only `attempts_used`, and starts a new graph thread at Planner. Close marks `failed`. Run statuses: `pending`, `running`, `awaiting_user`, `completed`, `failed`; never `paused`/`exhausted`.
- Reviewer checks planned files exist, are nonempty/allowlisted, then runs `node --check` on generated JS before LLM review. Approval requires both stages. Use JSON Schema + Pydantic, never regex parsing; validate whole checkpoint state. Application writes `chat_reply`, not graph nodes.

## Persistence, security, and docs

- PostgreSQL 17; Alembic creates app tables, never `create_all`. Fixed value sets use native PG ENUMs mirrored by `StrEnum`. Before production, keep one canonical initial migration and reset local DBs when it changes; afterward use additive, data-preserving migrations.
- LangGraph owns its checkpoint/store tables and Psycopg 3 lifecycle via `setup()`; never pass the SQLAlchemy `postgresql+asyncpg` URL to its saver.
- Workspace: latest `data/projects/{project_id}/`, snapshots `data/artifacts/{id}/`, preview `data/preview/{project_id}/`, archive `data/archived/{project_id}/{run_id}/`. Writes use temp file + `os.replace`, restricted to plan paths. Coder emits complete files; non-plan files remain byte-for-byte unchanged.
- Preview serves copied static files on a separate origin at `/preview/{preview_id}/`, using an unguessable capability. No iframe top-navigation or server execution of generated JS. Rate limiting is PostgreSQL-backed; trust forwarded IPs only from `TRUSTED_PROXY_CIDRS`.
- `.env` is ignored; `.env.example` contains placeholders only. Never commit secrets; setting names are in TDD §9.3.
- PlantUML fences use `puml`; Graphviz-dependent diagrams start with `!pragma layout smetana` inside `@startuml`. Do not recreate `docs/plantuml.md`, add tracking/milestone docs, or introduce decision/requirement IDs into the TDD. `.vscode/settings.json` is ignored.
