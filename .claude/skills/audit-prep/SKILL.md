---
name: audit-prep
description: "Security audit readiness assessment for HIPAA healthcare application. Triggers: audit, compliance, checklist, readiness, HIPAA, SOC2"
---

# Audit Readiness Assessment

You are preparing a HIPAA-regulated healthcare application for security review. Your goal is to evaluate readiness across all dimensions that security reviewers care about, identify gaps that would waste review time, and produce an actionable remediation plan.

## When to Activate

- Before engaging external security auditors
- Preparing for HIPAA compliance review
- Internal security assessment
- Pre-deployment security checklist

---

## Assessment Process

### Step 1: Scope Discovery

Identify what will be reviewed:

```bash
# Check tech stack
ls -la pyproject.toml package.json

# Find API routes
find app/routes -name "*.py" -type f

# Find frontend components
find web/app -name "*.tsx" -type f | head -20

# Check for environment files
find . -name ".env*" -o -name "config*.json" 2>/dev/null

# Check for PHI handling
grep -r "patient\|diagnosis\|treatment\|medical" app/ --include="*.py" -l
```

### Step 2: Run Checklist

For each category, report status as:
- **PASS** — meets security expectations
- **WARN** — acceptable but could be improved
- **FAIL** — must be fixed before review
- **SKIP** — not applicable

---

## Category 1: Build and Dependencies

```bash
# Python build
uv run ruff check app/
uv run pyright app/

# Python security audit
uv pip audit

# JavaScript build
pnpm build
pnpm audit
```

| Check | Status |
|-------|--------|
| Project builds without errors | |
| No linter warnings (ruff, ESLint) | |
| No type errors (pyright, TypeScript) | |
| No known vulnerabilities in Python deps | |
| No known vulnerabilities in JS deps | |
| Dependencies pinned to specific versions | |
| Lockfiles committed (uv.lock, pnpm-lock.yaml) | |

---

## Category 2: Secrets Management

```bash
# Check for potential secrets in code
grep -rn "password\|secret\|api_key\|apikey\|token\|credential" \
  app/ web/app --include="*.py" --include="*.ts" --include="*.tsx" | \
  grep -v "test\|spec\|mock" | head -20

# Check for .env files in git
git ls-files | grep -i "\.env"

# Check gitignore
cat .gitignore | grep -i "env\|secret\|key"
```

| Check | Status |
|-------|--------|
| No hardcoded secrets in source code | |
| No secrets in committed config files | |
| `.env` files are gitignored | |
| Secret patterns in `.gitignore` | |
| Environment variable documentation exists | |
| Production uses secrets manager (Azure Key Vault) | |

---

## Category 3: Authentication (Clerk)

| Check | Status |
|-------|--------|
| All protected routes use Clerk middleware | |
| Backend validates Clerk tokens on every request | |
| Session tokens use httpOnly cookies | |
| Session expiration configured | |
| Failed auth attempts logged | |
| No custom crypto for auth | |

```bash
# Check for auth middleware usage
grep -rn "AuthenticateClerkRequest\|Depends.*auth" app/routes --include="*.py"

# Check frontend auth guards
grep -rn "SignedIn\|SignedOut\|useAuth" web/app --include="*.tsx"
```

---

## Category 4: Authorization

| Check | Status |
|-------|--------|
| Every protected endpoint checks authorization | |
| Authorization checked server-side | |
| Resource ownership verified before access | |
| Authorization failures logged | |
| No authorization bypass via path manipulation | |

```bash
# Check for authorization patterns
grep -rn "user_id\|owner\|permission\|role" app/routes --include="*.py" | head -20
```

---

## Category 5: Input Validation

```bash
# Check for Pydantic usage
grep -rn "BaseModel\|SQLModel" app/ --include="*.py" -l

# Check for Zod usage in frontend
grep -rn "z\.\|zod" web/app --include="*.ts" --include="*.tsx" -l

# Check for dangerous patterns
grep -rn "eval\|exec\|dangerouslySetInnerHTML" \
  app/ web/app --include="*.py" --include="*.ts" --include="*.tsx"
```

| Check | Status |
|-------|--------|
| All API inputs validated with Pydantic | |
| Input length limits enforced | |
| File uploads validated (type, size) | |
| SQL queries use SQLModel (parameterized) | |
| No `eval()` or `exec()` with user input | |
| Frontend forms use Zod validation | |
| No `dangerouslySetInnerHTML` | |

---

## Category 6: API Security

```bash
# Check for rate limiting
grep -rn "rate.*limit\|throttle\|slowapi" app/ --include="*.py" -l

# Check for CORS configuration
grep -rn "CORSMiddleware\|cors" app/ --include="*.py"
```

| Check | Status |
|-------|--------|
| Rate limiting on public endpoints | |
| Stricter rate limits on auth endpoints | |
| CORS configured restrictively | |
| Request body size limits enforced | |
| Content-Type validation | |
| Security headers configured | |
| Error responses don't leak internals | |

---

## Category 7: HIPAA Compliance

```bash
# Check for PHI handling
grep -rn "patient\|diagnosis\|treatment\|deid\|deidentif" app/ --include="*.py" -l

# Check logging for PHI exposure
grep -rn "structlog\|logging\|logger" app/ --include="*.py" | \
  grep -v "test" | head -20

# Check for PHI in frontend
grep -rn "patient\|diagnosis\|medical" web/app --include="*.tsx" -l
```

| Check | Status |
|-------|--------|
| PHI de-identified before AI processing | |
| No PHI in application logs | |
| All PHI access is audit logged | |
| PHI encrypted at rest (PostgreSQL) | |
| PHI encrypted in transit (HTTPS) | |
| Minimum necessary principle followed | |
| Session timeout configured | |
| No PHI in localStorage/sessionStorage | |
| No PHI in URL parameters | |
| BAA (Business Associate Agreement) in place | |

---

## Category 8: Error Handling & Logging

```bash
# Check for bare except
grep -rn "except:" app/ --include="*.py" | grep -v "except.*:" | head -10

# Check for stack traces in responses
grep -rn "traceback\|stack_trace\|exc_info" app/routes --include="*.py"

# Check structured logging
grep -rn "structlog" app/ --include="*.py" -l
```

| Check | Status |
|-------|--------|
| No bare `except:` clauses | |
| Errors logged with context | |
| No sensitive data in logs | |
| Structured logging used (structlog) | |
| Request correlation IDs present | |
| Client errors don't expose internals | |

---

## Category 9: Testing

```bash
# Run tests
uv run pytest --tb=short
pnpm test

# Check coverage
uv run pytest --cov=app
```

| Check | Status |
|-------|--------|
| All tests pass | |
| Test coverage > 70% | |
| Authentication flows tested | |
| Authorization edge cases tested | |
| Input validation tested with malicious inputs | |
| API error responses tested | |
| PHI handling tested | |

---

## Category 10: Documentation

| Check | Status |
|-------|--------|
| Security assumptions documented | |
| API endpoints documented with auth requirements | |
| Environment variables documented | |
| PHI data flow documented | |
| Incident response contacts documented | |
| HIPAA compliance documentation exists | |

---

## Step 3: Generate Report

```markdown
# Audit Readiness Report

**Project**: Cosmaneura
**Date**: [date]
**Commit**: [hash]
**Tech Stack**: FastAPI, React, PostgreSQL, Clerk, Azure

## Summary

| Category | Pass | Warn | Fail | Skip |
|----------|------|------|------|------|
| Build & Dependencies | | | | |
| Secrets Management | | | | |
| Authentication | | | | |
| Authorization | | | | |
| Input Validation | | | | |
| API Security | | | | |
| HIPAA Compliance | | | | |
| Error Handling | | | | |
| Testing | | | | |
| Documentation | | | | |
| **Total** | | | | |

## Overall Readiness: [READY / NEARLY READY / NOT READY]

## Critical Issues (must fix)
[List all FAIL items, prioritized]

## Recommended Improvements
[List all WARN items, prioritized]

## Detailed Results
[Full checklist by category]
```

---

## Step 4: Prioritize Remediation

1. **Blocking** — Build failures, obvious critical vulnerabilities, HIPAA violations
2. **High-risk** — Auth bypasses, secrets exposure, SQL injection
3. **Medium-risk** — Rate limiting, logging gaps, validation holes
4. **Improvements** — Documentation, additional tests, code quality

---

## Anti-Patterns

| Pattern | Status | Reason |
|---------|--------|--------|
| Skip HIPAA checks | FORBIDDEN | Legal requirement |
| Ignore FAIL items | FORBIDDEN | Security risk |
| Audit without tests passing | FORBIDDEN | Unreliable results |
| Rush remediation | FORBIDDEN | Creates new issues |
