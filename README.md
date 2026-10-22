# AI Multi-Agent Coding Platform

Describe a small web app in plain language. The platform plans the files, generates them, reviews the result,
and returns a preview or a handoff with the open issues.

> **Status:** MVP implemented. The backend, worker, SPA, tests, migration, and evaluation harness run against
> PostgreSQL. The Azure provider path is implemented but unverified because deployment names and
> credentials were not available; the missing-credentials failure path is verified.

## How it works

```puml
@startuml
!pragma layout smetana
skinparam backgroundColor white
skinparam shadowing false
actor User
node "App\nSPA + FastAPI :8000" as app
node "Worker" as worker
database "PostgreSQL 17" as db
folder "Workspace volume\ndata/projects, artifacts, preview, archive" as files
node "Preview origin\npreview.localhost" as preview

User --> app : REST /api/v1
User --> preview : capability URL
app --> db : enqueue run
app --> files : read / serve
worker --> db : claim + checkpoints
worker --> files : atomic writes + snapshots
preview --> files : copied static files
@enduml
```

After login, the user lands in one persistent private chat; it restores the user's ordered conversation history
and is the only item in the main navigation. A separate worker claims coding runs from PostgreSQL and executes
the LangGraph workflow (planner → coder → reviewer). Message submission and project-selection actions are added
by the follow-on chat stories.

The [technical design document](docs/architecture.md) is the source of truth for the stack, contracts, security
rules, schema, trade-offs, and the presentation guide. The PRD, in the ignored `tasks/` directory, owns user
stories and acceptance criteria.

## Run the local stack

1. Copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`, `SECRET_KEY`, and the Azure key and deployments.
   See the [environment table](docs/architecture.md#93-environment-variables).
2. Build and start:

   ```bash
   docker compose build
   docker compose up -d
   docker compose exec app alembic upgrade head
   curl -fsS http://localhost:8000/health
   ```

3. Open:

   - App `http://localhost:8000`
   - API `http://localhost:8000/api/v1`
   - Preview `http://preview.localhost:8000`

Manual development from `server/`:

```bash
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:create_app --factory --reload
python -m app.worker
ruff check . && ruff format --check . && pytest
python -m evals.runner
```

Run the SPA with `npm ci && npm run dev` from `client/`; Vite proxies `/api` to port 8000. See the
[serving contract](docs/architecture.md#92-serving-and-topology).

## Security

- JWT authentication; projects, runs, files, and traces are owner-scoped.
- Per-IP and per-user rate limits; over-limit requests return `429` with `Retry-After`.
- Only the developer configures providers, credentials, and models; users have no settings surface.
- Credentials are resolved in infrastructure and never reach domain or application code.
- Preview is a separate origin served from a capability URL. Generated JavaScript is syntax-checked with
  `node --check` and never executed by the server.

See [API design](docs/architecture.md#6-api-design) and the
[security model](docs/architecture.md#7-security-and-abuse-prevention).

## Repository layout

- `client/` — Vue 3 / TypeScript SPA built with Vite and Tailwind.
- `server/` — FastAPI / LangGraph backend, worker, Alembic migration, tests, and evaluation harness.
- `shared/` — reserved for shared contracts.
- `docs/` — architecture.
- `tasks/` — ignored working briefs and the PRD.
- `Dockerfile`, `docker-compose.yml`, `.env.example` — local stack.

## Design notes

### What I would change with more time

Add a dedicated tester agent, so a plausible review is not treated as proof the app works, and grow the
evaluation dataset from real requests. Move generated-app execution from the static iframe to isolated
containers or Git-backed workspaces, and move the worker to a durable external queue with spend limits and
team RBAC.

### Where other models are worth trying

The model layer is a port, so providers change by developer configuration, never by end users. The Reviewer is
the best first experiment: a wrong review is recoverable, while a wrong Coder output consumes an attempt.
Planner and Coder are poor candidates for small models because multi-file coherence, long context, and strict
structured output degrade first. See [providers and secrets](docs/architecture.md#43-providers-and-secrets).

### How Coder and Reviewer disagreement is handled

The review loop is bounded to three Coder attempts and runs without human input. An unresolved disagreement is
never shown as success: the run stops in `awaiting_user` with the latest files, open issues, and both counts.
New guidance reruns the whole planner → coder → reviewer flow with a fresh attempt budget. See the
[state contract](docs/architecture.md#52-state-contract).

## Docs

- [Technical design document](docs/architecture.md) — contracts, diagrams, schema, decisions and trade-offs,
  verification, and the presentation guide, including the
  [adapter recipe](docs/architecture.md#44-adding-an-adapter).
