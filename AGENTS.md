# AGENTS.md

## Repo state

- MVP implemented: `server/app/` (API, application, domain, infrastructure), `server/alembic/`, `server/tests/`, `server/evals/`, and a Vue/Vite SPA in `client/`. `shared/` remains reserved.
- Shipped sources: `README.md`, `docs/architecture.md`, root Docker files, `server/`, `client/`. `tasks/` is gitignored scratch containing the PRD and approved briefs.
- Docker Compose defines `db`, `app`, and `worker`; the runtime image includes Node for `node --check`. Both services share the `appdata` workspace volume.
- The Azure provider path is implemented but unverified because deployment names and credentials are unavailable. Do not invent them.

## Read first

- `docs/architecture.md` is the Technical Design Document and single source of truth for the stack, contracts, and rules. Do not restate them in `README.md` or the PRD; link to it. The PRD owns user stories/acceptance criteria only.
- Backend layout is fixed: `server/app/{domain,application,infrastructure,interfaces}`; entrypoints are `app.main:create_app()` and `app.worker`. Adapter recipe: TDD §4.4 (`Adding an adapter`).
- JavaScript syntax checks use `node --check`. The API budget policy is still undecided; confirm before choosing (TDD §13.2).

## Commands

From repo root:

```bash
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
curl -fsS http://localhost:8000/health
```

Backend development:

```bash
cd server
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:create_app --factory --reload
python -m app.worker
ruff check . && ruff format --check .
pytest
python -m evals.runner
```

- Run `ruff`, `pytest`, and `python -m evals.runner` from `server/`: the only config is `server/pyproject.toml` (line length 100, py314, `asyncio_mode=auto`, `testpaths=tests`). Put tests in `server/tests/`.
- The worker and local verification need Node available for `node --check`. The runtime Docker image installs it. The evaluation harness lives in `server/evals/`; see TDD §11.2.
- `packages = []` means installs provide dependencies only; run the app from `server/` so `app` is importable.
- No CI, lockfile, or pre-commit hooks exist. The commands above are the verification gate.
- Client: `npm ci && npm run dev` from `client/` (dev on 5173; FastAPI on 8000). Docker builds it in the Node stage and serves it through FastAPI.

## Architecture invariants

- Imports flow `interfaces → application → domain`; `infrastructure` implements application ports and may import domain. Domain imports no FastAPI, SQLAlchemy, SDK, or I/O module. Application imports no infrastructure or interfaces.
- Ports are narrow `typing.Protocol`s: `LLMClient`, `Workspace`, `RunRepository`, `RunQueue`, `EventSink`, `MemoryStore`, `RateLimiter`, `Clock`. Wire adapters only in `infrastructure/container.py` (manual DI, no mutable globals).
- Domain models are plain dataclasses; Pydantic v2 validation lives at HTTP/model/checkpoint boundaries.
- Never let API keys, URLs, or provider SDK types reach domain or application code. Infrastructure resolves credentials and injects ports; missing credentials fail with a masked error naming the setting.
- Providers are OpenAI-compatible adapters under `infrastructure/llm/`. For Azure, the request `model` is the deployment name, not the underlying model name.

## Workflow invariants

- LangGraph order: planner → coder → reviewer. `MAX_CODER_ATTEMPTS=3` means at most three Coder generations per round; increment counters once per Coder invocation before checks.
- A round is unattended: no clarification pause and no human input until `completed` or `awaiting_user`. The third rejection or a valid `escalate` yields `awaiting_user`, never false approval: persist latest files, issues, round, and both counts.
- Resume with guidance keeps the same run and project, increments `round`, resets `attempts_used` only, and starts a new graph thread at the Planner so the whole planner → coder → reviewer flow reruns. Close marks it `failed`.
- One private conversation belongs to each user; every route scopes it through the authenticated owner. A project is context linked to the chat, never the owner of a separate conversation.
- Pre-run intent detection may request manual selection but never chooses or creates a project. Only user confirmation may change chat context or authorize project creation; no selection actions are exposed until their story is implemented.
- Model/schema errors go through `run_end` to `failed`; no model-error retry. `chat_reply` is written by the application layer, not a graph node. Validate the whole state before each checkpoint.
- Reviewer runs deterministic checks before LLM review: every planned file exists, is nonempty, has an allowed type, then every `*.js` passes `node --check`. Approval requires both check stages. Never regex-parse model output; use JSON Schema plus Pydantic.

## Persistence and files

- PostgreSQL 17 only. `alembic upgrade head` creates application tables, never `create_all`. Fixed value sets are native PG ENUMs mirrored as `str` enums.
- Before production, keep one canonical initial migration and reset local databases when it changes. Once production data exists, freeze it and use additive, data-preserving migrations.
- LangGraph checkpoints use `AsyncPostgresSaver`, which owns its tables and Psycopg 3 lifecycle via `setup()`. Do not pass the SQLAlchemy `postgresql+asyncpg` URL to it.
- Workspace layout: latest `data/projects/{project_id}/`, approved snapshots `data/artifacts/{id}/`, served preview `data/preview/{project_id}/`, archive `data/archived/{project_id}/{run_id}/`. Writes are atomic (temp file + `os.replace`) and restricted to current plan paths.
- Coder emits complete files, never patches. Non-plan files stay byte-for-byte unchanged.
- Preview serves copied static files at `/preview/{preview_id}/` from a separate origin, using the unguessable `projects.preview_id` capability URL; the iframe has no top-navigation and the server never executes generated JS.

## Config and docs gotchas

- `.env` is gitignored; `.env.example` holds placeholders only. Never commit populated secrets. Env names are listed in TDD §9.3; do not invent Azure deployment names while they are unset.
- Provider, deployment, model, and credential settings are developer-only environment configuration. Do not add user-facing settings, credential, or model-listing endpoints.
- Run statuses are `pending`, `running`, `awaiting_user`, `completed`, `failed`. Do not reintroduce `paused` or `exhausted`.
- Rate limiting is PostgreSQL-backed and shared across API workers. Trust forwarded client IP headers only from `TRUSTED_PROXY_CIDRS`; never trust them unconditionally.
- `Dockerfile` builds `client/` in a Node stage and serves the static output through FastAPI. The runtime image must include Node for `node --check`. Docker Compose services run migrations explicitly with `alembic upgrade head`.
- PlantUML fences are `puml`. Diagrams that would need Graphviz start with `!pragma layout smetana` inside `@startuml`.
- Do not recreate `docs/plantuml.md` or add tracking/milestone docs; `.vscode/settings.json` is gitignored.
- Do not reintroduce decision/requirement IDs into the TDD; it is a design document, not a decision log.
