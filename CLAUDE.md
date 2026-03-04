# Cosmaneura

> HIPAA-compliant healthcare application with AI-powered clinical workflows

## Agent Context

**For AI Coding Agents**: Read `.claude/AGENTS.md` FIRST for critical instructions, safety boundaries, and operational context.

**Skill Files**: Domain-specific knowledge is in `.claude/skills/`. Load relevant skills before implementation work.

---

## Project Overview

Cosmaneura is a full-stack healthcare application providing AI-powered clinical documentation, patient management, and workflow automation. The application handles Protected Health Information (PHI) and must maintain HIPAA compliance.

### Key Concepts

- **PHI (Protected Health Information)**: Patient data requiring HIPAA-compliant handling
- **De-identification**: Azure Health Deidentification API for masking sensitive data
- **Clerk Authentication**: Third-party auth service for user management
- **TypeID API Keys**: Prefixed identifiers for external API access

---

## Architecture

### Monorepo Structure

```
cosmaneura/
├── .claude/                    # AI agent configuration
│   ├── AGENTS.md               # Agent constraints (READ FIRST)
│   ├── skills/                 # Domain knowledge
│   └── knowledgebase/          # Reference materials
├── app/                        # Python backend (FastAPI)
│   ├── routes/                 # API endpoints
│   ├── models/                 # SQLModel database models
│   ├── jobs/                   # Celery background tasks
│   ├── commands/               # Business logic
│   └── configuration/          # Service configuration
├── web/                        # React frontend (TypeScript)
│   ├── app/                    # Routes and pages
│   └── components/             # Reusable UI components
├── migrations/                 # Alembic database migrations
├── infra/                      # Terraform infrastructure
├── tests/                      # Python backend tests
├── CLAUDE.md                   # This file
└── Justfile                    # Task automation
```

### Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Backend | FastAPI + Python 3.13 | Async REST API |
| ORM | SQLModel (SQLAlchemy + Pydantic) | Type-safe database access |
| Database | PostgreSQL 16 | Primary data store |
| Cache | Redis | Sessions, Celery broker |
| Jobs | Celery + Redis | Background task processing |
| Frontend | React 19 + TypeScript | User interface |
| Build | Vite | Frontend bundling |
| Styling | Tailwind CSS | Utility-first CSS |
| Auth | Clerk | User authentication |
| Cloud | Azure | Infrastructure |
| IaC | Terraform | Infrastructure provisioning |

---

## Security-First Development Standards

This project handles PHI and must maintain HIPAA compliance. All code must follow these security standards.

### General Principles

| Principle | Implementation |
|-----------|----------------|
| Defense in Depth | Layer validations throughout the codebase |
| Fail Secure | Deny access by default on errors |
| Least Privilege | Request only permissions needed |
| Input Validation | Validate at all system boundaries |
| PHI Protection | Never log or expose patient data |

### Python Backend Security

| Rule | Enforcement | Status |
|------|-------------|--------|
| No hardcoded secrets | Use environment variables | MANDATORY |
| Validate all inputs | Pydantic/SQLModel schemas | MANDATORY |
| Parameterized queries | SQLModel/SQLAlchemy only | MANDATORY |
| No PHI in logs | Structured logging without PII | MANDATORY |
| Handle errors properly | No bare except, no `.unwrap()` patterns | MANDATORY |
| Dependency audit | `uv pip audit` regularly | MANDATORY |

### TypeScript Frontend Security

| Rule | Enforcement | Status |
|------|-------------|--------|
| No secrets in bundle | Server-side env vars only | MANDATORY |
| XSS prevention | No `dangerouslySetInnerHTML` | MANDATORY |
| Input validation | Zod schemas on forms | MANDATORY |
| Secure storage | httpOnly cookies, not localStorage | MANDATORY |
| Dependency audit | `pnpm audit` regularly | MANDATORY |

### HIPAA Compliance

| Rule | Enforcement | Status |
|------|-------------|--------|
| PHI de-identification | Azure Health Deidentification API | MANDATORY |
| Access logging | All PHI access must be logged | MANDATORY |
| Minimum necessary | Only access PHI required for task | MANDATORY |
| Encryption in transit | HTTPS only | MANDATORY |
| Session timeouts | Auto-logout on inactivity | MANDATORY |

---

## Coding Standards

### Code Philosophy

| Principle | Implementation |
|-----------|----------------|
| Least code wins | Minimum code to achieve the goal |
| Exceptional organization | Clear structure, logical grouping |
| No code bloat | Delete anything unused |
| No over-engineering | Build exactly what's requested |
| No premature abstraction | Wait for rule of 3 |

### Python

- **Python 3.13+** with type hints everywhere
- **Pydantic models** for request/response validation
- **SQLModel** for database models
- **Async/await** for I/O operations
- **Structlog** for structured logging

### TypeScript

- **Strict mode always** (`"strict": true`)
- **No `any`** — use `unknown` and type guards
- **Zod schemas** for runtime validation
- **React Query** for data fetching

### Git Conventions

- **Conventional commits**: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`
- **Branch naming**: `feat/feature-name`, `fix/bug-description`

---

## Development Commands

```bash
# Development
just dev                # Start all services
just py_test            # Run Python tests
just js_test            # Run frontend tests
just lint               # Run all linters

# Database
just db_migrate         # Run migrations
just db_upgrade         # Create new migration

# Build
just build              # Production build

# Security
uv pip audit            # Check Python dependencies
pnpm audit              # Check JS dependencies
```

---

## Skill Files

Domain-specific knowledge in `.claude/skills/`:

| Skill | Purpose | Triggers |
|-------|---------|----------|
| `security-review/` | Security vulnerability analysis | security, review, vulnerability, audit |
| `audit-prep/` | Pre-audit readiness assessment | audit, compliance, checklist |
| `code-quality/` | Minimal code philosophy | quality, refactor, clean |
| `skill-creator/` | Create new skills | create skill, new skill |

**Usage**: Load relevant skills before implementing features in that domain.

For agent operational constraints, see `.claude/AGENTS.md`.
