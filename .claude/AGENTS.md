# AGENTS.md — Cosmaneura

> IMPORTANT: This application handles PHI. Security and HIPAA compliance are non-negotiable.

This file provides persistent context for AI coding agents. Read this FIRST before any work.

---

## Critical Instructions

### 1. Skills First

Before implementation work, check `.claude/skills/` for relevant domain knowledge:

| Task Domain | Skill to Load |
|-------------|---------------|
| Security review | `security-review/` |
| Audit preparation | `audit-prep/` |
| Code quality | `code-quality/` |
| Creating skills | `skill-creator/` |

### 2. Stack Specifics

| Component | Technology | Key Files |
|-----------|------------|-----------|
| Backend API | FastAPI | `app/routes/` |
| Database models | SQLModel | `app/models/` |
| Background jobs | Celery | `app/jobs/` |
| Frontend | React + TypeScript | `web/app/` |
| Auth | Clerk | `app/routes/internal/auth.py` |
| Migrations | Alembic | `migrations/` |

### 3. Development Workflow

```
Local (just dev) → PR → GitHub Actions → Deploy
```

---

## Safety Boundaries

### Safe to Execute

```bash
# Python
uv run pytest
uv run ruff check
uv run ruff format --check
uv run pyright

# JavaScript
pnpm test
pnpm lint
pnpm typecheck
pnpm build

# Just commands
just lint
just py_test
just js_test
just build

# Git (read-only)
git status
git diff
git log
git branch

# Docker
docker compose up -d
docker compose logs
```

### Ask First

```bash
# Database changes
just db_migrate
just db_upgrade
alembic revision

# Git (write)
git add
git commit
git push
git checkout
git merge

# Package changes
uv add
pnpm add

# Docker destructive
docker compose down
docker system prune
```

### Never Without Explicit User Request

```bash
# Destructive operations
git push --force
git reset --hard
rm -rf

# Production/sensitive
Deploy to production
Modify .env files with real secrets
Access or log PHI data
Disable security middleware
```

---

## Directory Map

```
cosmaneura/
├── .claude/                    # AI agent configuration (YOU ARE HERE)
│   ├── AGENTS.md               # This file
│   ├── skills/                 # Domain knowledge
│   │   ├── security-review/    # Security vulnerability analysis
│   │   ├── audit-prep/         # Pre-audit checklist
│   │   ├── code-quality/       # Code quality standards
│   │   └── skill-creator/      # Create new skills
│   └── knowledgebase/          # Reference materials
├── app/                        # Python backend
│   ├── routes/                 # API endpoints
│   │   ├── internal/           # Authenticated endpoints
│   │   ├── external/           # Public/API key endpoints
│   │   └── chat/               # Chat endpoints
│   ├── models/                 # SQLModel database models
│   ├── jobs/                   # Celery tasks
│   ├── commands/               # Business logic
│   ├── configuration/          # Service config
│   └── utils/                  # Utilities
├── web/                        # React frontend
│   ├── app/                    # Routes and pages
│   └── components/             # UI components
├── migrations/                 # Alembic migrations
├── tests/                      # Python tests
├── infra/                      # Terraform
├── CLAUDE.md                   # Project overview
├── Justfile                    # Task automation
└── .env                        # Environment (NEVER COMMIT)
```

---

## Invariants (Non-Negotiable)

### HIPAA Compliance

| Rule | Enforcement | Status |
|------|-------------|--------|
| Never log PHI | Structured logging without PII | MANDATORY |
| De-identify before AI | Azure Health Deidentification | MANDATORY |
| Access logging | Log all PHI access with user context | MANDATORY |
| Minimum necessary | Only access required PHI | MANDATORY |
| Encryption | HTTPS only, encrypted at rest | MANDATORY |

### Security

| Rule | Enforcement | Status |
|------|-------------|--------|
| Validate all inputs | Pydantic/Zod schemas | MANDATORY |
| Parameterized queries | SQLModel only | MANDATORY |
| No hardcoded secrets | Environment variables | MANDATORY |
| Auth on all endpoints | Clerk middleware | MANDATORY |
| No eval/innerHTML | Static analysis | FORBIDDEN |

### Code Quality

| Rule | Enforcement | Status |
|------|-------------|--------|
| Type hints everywhere | pyright strict | MANDATORY |
| No `any` in TypeScript | ESLint | MANDATORY |
| Tests for new features | pytest/vitest | MANDATORY |
| Delete unused code | Don't comment out | MANDATORY |

---

## Verification Matrix

Before considering any task complete:

### For Python Backend

```bash
uv run ruff check app/           # Linting
uv run ruff format --check app/  # Formatting
uv run pyright app/              # Type checking
uv run pytest                    # Tests pass
```

### For TypeScript Frontend

```bash
pnpm typecheck          # Type checking
pnpm lint               # Linting
pnpm test               # Tests pass
pnpm build              # Build succeeds
```

### For Database Changes

```bash
just db_migrate         # Migration created
just py_test            # Tests pass with migration
```

---

## Operational Strategy

### For Simple Tasks

1. Read the relevant file
2. Make the change
3. Run verification
4. Done

### For Security-Sensitive Changes

1. **Load skill** — `security-review/`
2. **Understand context** — What PHI/PII is involved?
3. **Implement** — Follow security patterns
4. **Review** — Check against vulnerability patterns
5. **Verify** — Full verification matrix

### For New Features

1. **Check skills** — Load relevant domain knowledge
2. **Understand existing** — Read related code
3. **Implement** — Follow existing patterns
4. **Test** — Write tests, run full suite
5. **Verify** — Linting, types, tests pass

---

## Anti-Patterns (FORBIDDEN)

| Pattern | Why Forbidden | Instead |
|---------|---------------|---------|
| Log PHI/PII | HIPAA violation | Use structured logging without sensitive data |
| Hardcode secrets | Security risk | Use environment variables |
| SQL string concat | Injection risk | Use SQLModel/parameterized queries |
| `eval()` / `innerHTML` | XSS risk | Use safe alternatives |
| Bare `except:` | Hides errors | Catch specific exceptions |
| `# type: ignore` | Hides type errors | Fix the type issue |
| Store PHI in localStorage | Security risk | Server-side only |
| Skip input validation | Injection risk | Pydantic/Zod schemas |

---

## Cross-References

- **Architecture**: See `CLAUDE.md` in project root
- **Domain Knowledge**: See `.claude/skills/`
- **Vulnerability Patterns**: See `.claude/knowledgebase/vulnerabilities.md`
