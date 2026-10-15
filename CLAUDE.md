# CLAUDE.md — cosmaneura

> Architecture decisions for this system live in the `cosmaneura-architecture` repo, in `ARCHITECTURE.md`. Read it before making structural changes. If a change conflicts with it, stop and resolve the conflict rather than proceeding.

That repo also contains the ADR index (`adr/README.md`) and ratified contracts. Read those before moving code between repos. Do not treat historical audit notes as current policy.

## Qué es

Repo principal de **cosmaneura.com**: un SaaS de salud orientado a HIPAA. Graba/sube
visitas médico-paciente (integraciones con LiveSwitch, Zoom y Recall), transcribe el
audio, de-identifica PHI y genera notas clínicas con IA. Incluye además un flujo de
chat de intake de pacientes y un motor de scoring de "patient engagement".

⚠️ **PHI (Protected Health Information) es una preocupación de primer orden.** La
de-identificación usa la API de De-identification de Azure. **Nunca almacenar PHI en
máquinas de desarrollo.**

Generado desde `python-starter-template` (github.com/iloveitaly/python-starter-template),
sincronizado vía copier (`.copier-answers.yml`).

## Stack

- **Backend** (Python 3.13, gestionado con `uv`): FastAPI + Uvicorn, SQLModel/SQLAlchemy 2.x
  + Alembic (PostgreSQL vía `psycopg`), capa custom `activemodel` (semántica tipo Rails) e
  IDs `typeid-python`. Celery (worker + redbeat beat) sobre Redis. Clerk (auth),
  OpenAI + AssemblyAI (transcripción/LLM), Azure (blob storage, identity, health de-id),
  PostHog, Sentry, structlog. CLI con Typer.
- **Frontend** (`web/`, Node 22 / pnpm 9): React 19 + React Router 7 (framework mode, SSR),
  Vite 6, TypeScript 5.8, TailwindCSS 4, shadcn/ui + Radix, TanStack Query, react-hook-form +
  zod, assistant-ui (chat), Clerk React. El cliente de API se **auto-genera desde el spec
  OpenAPI del backend** con `@hey-api/openapi-ts` (`web/client/`).
- **Tooling:** `just` (task runner, interfaz principal), `mise`/`.tool-versions`, `direnv`,
  Docker Compose (Postgres/Redis), Nixpacks (deploy), lefthook, commitlint.

## Estructura

**Backend `app/`:** `routes/` (routers FastAPI: `internal.py` es el que alimenta el cliente JS,
`api.py`, `chat.py`, `webhooks.py`, `zoom_auth.py`, `recall_callbacks.py`, `admin.py`),
`models/` (SQLModel), `jobs/` (tareas Celery: transcribe, summarize, anonymize,
score_engagement, sync_*), `services/` (recall, zoom, usage), `configuration/` (setup por
servicio), `commands/` + `cli/` (Typer), `engagement/`, `templates/`, `utils/`.

**Frontend `web/app/`:** `routes/` + `routes.ts`, `layouts/`, `components/`, `hooks/`, `lib/`,
`configuration/`, `root.tsx`.

**Otros:** `migrations/` (Alembic + `seed.py`), `tests/`, `just/` (recetas modulares),
`infra/` + `infra-staging/` (Terraform/Azure), `docs/`.

## Comandos (todo vía `just` — corre `just --list`)

```bash
just setup        # setup local completo (deps, db up/seed, js_setup)
just dev          # arranca todos los servicios (Procfile.dev)
just build        # build completo
just lint         # js_lint + py_lint + db_lint
just py_lint_fix  # ruff --fix + format + djlint
just js_lint-fix  # prettier --write + eslint --fix

just py_test      # pytest (no-integration, luego tests/integration con Playwright)
just js_test      # vitest run

# Base de datos (Alembic, tipo Rails)
just db_migrate
just db_generate_migration "<name>"
just db_seed
just db_reset

# OpenAPI / codegen — CORRER tras cambiar rutas o modelos del backend
just js_generate-openapi
```

## Notas de arquitectura

- **`main.py`** es el entrypoint de producción (detectado por Nixpacks; **no renombrar**) →
  `app.server:api_app`.
- **`app/__init__.py`** ejecuta `setup()` como efecto de import: toda la config de servicios
  ocurre al importar (fail-fast en el arranque).
- **Los nombres de métodos de las rutas importan**: se vuelven operation IDs de OpenAPI → nombres
  de métodos del cliente JS generado.
- **`internal_api_app`** es la app cuyo spec OpenAPI alimenta el frontend.
- **Migraciones Alembic: nunca inventes revision IDs a mano** (ya causó una colisión y un ciclo
  en el grafo). Genera migraciones con `just db_generate_migration`; si escribes una a mano,
  el ID sale de `python -c 'import secrets; print(secrets.token_hex(6))'`.
  `tests/migrations_graph_test.py` verifica el grafo (IDs únicos, sin ciclos, un solo head).
- Worker y scheduler corren en **contenedores separados** a propósito.
- Env se gestiona **exclusivamente vía direnv** (`.envrc` + `.env.*`), no en el Justfile.
- `master` es el tronco. Staging y producción son entornos que se promueven a un SHA de `master`
  explícito y revisado; hacer merge nunca despliega. La rama vestigial `staging` ya no tiene
  ninguna ruta de despliegue y está en proceso de retirarse. Los despliegues de ambos entornos
  requieren una ejecución manual iniciada por el fundador: producción con
  `Build & Publish (manual)` sobre `master`, y staging con `Deploy Staging Candidate`, que recibe
  un `candidate_sha` contenido en `master`.
- Aliases de paths frontend: `~/*` y `@/*` → `web/app/*`.

## Config

Python: `pyproject.toml` (deps, ruff, pyright, djlint, pytest), `uv.lock`, `alembic.ini`.
JS: `web/tsconfig.json`, `web/eslint.config.js`, `web/vite.config.ts`, `web/vitest.config.ts`,
`web/react-router.config.ts`, `web/openapi-ts.config.ts`, `web/components.json`. Prettier
inline en `web/package.json` (sin semicolons, orden de imports custom).
Runtimes en `.tool-versions` (uv 0.7.15, pnpm 9.15.9, python 3.13.2, node 22.13.0).

## QA bots

QA runs through GitHub issues labeled `qa-handoff`, one issue per QA bot.

- **QA Tester Bot** uses label `qa-bot-1` and owns cases QA-100 through QA-199.
  QA-200 and above are reserved for a future bot 2.

The coordinator posts `HANDOFF` pinned to a build/commit verified against the
staging health/version endpoint. The bot posts `ACK`, runs its cases, and posts
`RESULT` (`PASS`, `FAIL`, or `BLOCKED`) with uploaded evidence.

Each bot has its own login and its own test practice. A bot never touches
another bot's practice or the shared demo site. Shared tools (auth, settings
export) are owned by one bot at a time and must be `RELEASED` in the issue
when that bot is done.

Target `https://staging-app.cosmaneura.com` only. On production
`app.cosmaneura.com`, a page-load check is the only allowed action: no login
and no further actions.

Hard rules:

- No production logins and no production deploys.
- Never run `Deploy Staging Candidate` or any deploy or rollback workflow.
- QA bots do not comment on, approve, or merge pull requests. Results are
  posted only on handoff issues.
- Synthetic data only. No PHI.
- Stripe test mode only.
- No real clinician Zoom.
- Never contact clinicians or MCD.
- Sign-in and Zoom-join results are status-only and are never called "fixed".

Known limits:

- CI is off.
- Stripe checkout may fail because of a Clerk permission issue.
- Recording-start timing needs a real iPhone and is out of scope.
