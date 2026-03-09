# Audit Readiness Report

**Project**: Cosmaneura
**Date**: 2026-03-09
**Commit**: 82bdedb7cffd3e422d5d1fea7cc0ea74e2e63dda
**Tech Stack**: FastAPI, React, PostgreSQL, Clerk, Azure

---

## Summary

| Category | Pass | Warn | Fail | Skip |
|----------|------|------|------|------|
| Build & Dependencies | 1 | 3 | 2 | 1 |
| Secrets Management | 2 | 1 | 2 | 1 |
| Authentication | 4 | 1 | 1 | 0 |
| Authorization | 4 | 1 | 0 | 0 |
| Input Validation | 5 | 1 | 1 | 0 |
| API Security | 1 | 1 | 3 | 2 |
| HIPAA Compliance | 5 | 2 | 2 | 1 |
| Error Handling | 4 | 1 | 1 | 0 |
| Testing | 0 | 1 | 2 | 4 |
| Documentation | 0 | 0 | 1 | 5 |
| **Total** | **26** | **12** | **15** | **14** |

## Overall Readiness: NOT READY

The application has fundamental security issues in infrastructure configuration (database exposed to internet, CORS wildcards) and secrets management (.env files in git) that must be resolved before engaging external auditors. Code-level security is generally good, but infrastructure issues would immediately fail any security audit.

---

## Critical Issues (Must Fix)

### 1. PostgreSQL Database Publicly Accessible
**Category**: Infrastructure / HIPAA
**Impact**: PHI database exposed to the internet

The production PostgreSQL database has:
- `public_network_access_enabled = true`
- Firewall rule allowing all IPs (0.0.0.0/0)

**Remediation**:
```hcl
# infra/main.tf
resource "azurerm_postgresql_flexible_server" "main" {
  # Change this:
  public_network_access_enabled = false
}

# Delete this resource:
# resource "azurerm_postgresql_flexible_server_firewall_rule" "all" { ... }
```

---

### 2. Environment Files in Git
**Category**: Secrets Management
**Impact**: Potential credential exposure

Multiple `.env` files tracked:
- `.env.production.backend`
- `.env.production.frontend`
- `.env.shared`

**Remediation**:
1. Audit files for secrets
2. Use BFG Repo-Cleaner to remove from history
3. Update `.gitignore`:
```gitignore
.env*
!.env.example
```

---

### 3. Blob Storage CORS Wildcard
**Category**: Infrastructure / API Security
**Impact**: Any domain can make requests to storage

```hcl
allowed_origins = ["*"]  # VULNERABLE
```

**Remediation**:
```hcl
allowed_origins = ["https://app.cosmaneura.com", "https://cosmaneura.com"]
```

---

### 4. No Rate Limiting
**Category**: API Security
**Impact**: Brute force, DoS, enumeration attacks

No rate limiting on any endpoint.

**Remediation**:
Add SlowAPI or similar rate limiting middleware.

---

### 5. No JS Lockfile
**Category**: Build & Dependencies
**Impact**: Non-reproducible builds, supply chain risk

No `pnpm-lock.yaml` committed.

**Remediation**:
```bash
cd web && pnpm install && git add pnpm-lock.yaml
```

---

### 6. Tests Cannot Run
**Category**: Testing
**Impact**: Cannot verify security test coverage

Tests fail immediately due to missing `PYTHON_ENV` environment variable.

**Remediation**:
Fix `tests/conftest.py` to handle missing env vars gracefully, or document required test setup.

---

## Detailed Results

### Category 1: Build and Dependencies

| Check | Status | Notes |
|-------|--------|-------|
| Project builds without errors | **WARN** | JS build untested (pnpm version mismatch) |
| No linter warnings (ruff, ESLint) | **WARN** | 2 ruff import ordering errors |
| No type errors (pyright, TypeScript) | **SKIP** | Could not verify |
| No known vulnerabilities in Python deps | **FAIL** | `uv pip audit` not available |
| No known vulnerabilities in JS deps | **FAIL** | No lockfile to audit |
| Dependencies pinned to specific versions | **PASS** | `uv.lock` exists for Python |
| Lockfiles committed (uv.lock, pnpm-lock.yaml) | **WARN** | Python yes, JS no |

**Remediation**:
1. Fix ruff errors: `uv run ruff check --fix app/`
2. Generate JS lockfile: `pnpm install`
3. Set up pip-audit: Use `pip-audit` package directly

---

### Category 2: Secrets Management

| Check | Status | Notes |
|-------|--------|-------|
| No hardcoded secrets in source code | **PASS** | Uses `decouple.config()` |
| No secrets in committed config files | **FAIL** | `.env.production.*` in git |
| `.env` files are gitignored | **FAIL** | Only `*.env.*local` ignored |
| Secret patterns in `.gitignore` | **WARN** | Incomplete coverage |
| Environment variable documentation exists | **SKIP** | Not verified |
| Production uses secrets manager (Azure Key Vault) | **PASS** | 1Password integration via Terraform |

**Remediation**:
1. Remove `.env.production.*` from git tracking
2. Update `.gitignore` to exclude all env files
3. Document required environment variables

---

### Category 3: Authentication (Clerk)

| Check | Status | Notes |
|-------|--------|-------|
| All protected routes use Clerk middleware | **PASS** | `internal_api_app` has `AuthenticateClerkRequest` |
| Backend validates Clerk tokens on every request | **PASS** | Via dependency injection |
| Session tokens use httpOnly cookies | **PASS** | Clerk default + `SessionMiddleware` |
| Session expiration configured | **WARN** | Not explicitly configured |
| Failed auth attempts logged | **PASS** | HTTPException raised |
| No custom crypto for auth | **PASS** | Uses Clerk SDK |
| Webhook signature validation | **FAIL** | No Svix verification found |

**Remediation**:
1. Configure session timeout in Clerk dashboard
2. Implement Svix webhook signature validation if webhooks are used

---

### Category 4: Authorization

| Check | Status | Notes |
|-------|--------|-------|
| Every protected endpoint checks authorization | **PASS** | Checks `doctor.id` ownership |
| Authorization checked server-side | **PASS** | All in Python routes |
| Resource ownership verified before access | **PASS** | e.g., `Appointment.doctor_id == doctor.id` |
| Authorization failures logged | **WARN** | HTTPException raised, not explicitly logged |
| No authorization bypass via path manipulation | **PASS** | TypeID validation prevents traversal |

**Remediation**:
1. Add explicit logging for authorization failures

---

### Category 5: Input Validation

| Check | Status | Notes |
|-------|--------|-------|
| All API inputs validated with Pydantic | **PASS** | All routes use Pydantic models |
| Input length limits enforced | **WARN** | Not explicitly set on string fields |
| File uploads validated (type, size) | **PASS** | Handled via Azure SAS tokens |
| SQL queries use SQLModel (parameterized) | **PASS** | No raw SQL found |
| No `eval()` or `exec()` with user input | **PASS** | None found |
| Frontend forms use Zod validation | **FAIL** | Only 2 files use Zod |
| No `dangerouslySetInnerHTML` | **PASS** | None found |

**Remediation**:
1. Add `max_length` constraints to Pydantic string fields
2. Expand Zod validation to all frontend forms

---

### Category 6: API Security

| Check | Status | Notes |
|-------|--------|-------|
| Rate limiting on public endpoints | **FAIL** | No rate limiting |
| Stricter rate limits on auth endpoints | **FAIL** | No rate limiting |
| CORS configured restrictively | **FAIL** | Blob storage allows `*` |
| Request body size limits enforced | **SKIP** | Framework defaults |
| Content-Type validation | **SKIP** | Framework defaults |
| Security headers configured | **WARN** | TrustedHostMiddleware only |
| Error responses don't leak internals | **PASS** | Custom error handling |

**Remediation**:
1. Add SlowAPI rate limiting
2. Fix CORS on blob storage
3. Add security headers (CSP, X-Frame-Options, etc.)

---

### Category 7: HIPAA Compliance

| Check | Status | Notes |
|-------|--------|-------|
| PHI de-identified before AI processing | **PASS** | Azure Health Deidentification |
| No PHI in application logs | **WARN** | Doctor objects logged, need audit |
| All PHI access is audit logged | **FAIL** | No explicit audit logging |
| PHI encrypted at rest (PostgreSQL) | **PASS** | Azure default encryption |
| PHI encrypted in transit (HTTPS) | **PASS** | `https_only=True` |
| Minimum necessary principle followed | **PASS** | Routes filter by doctor_id |
| Session timeout configured | **WARN** | Not explicitly set |
| No PHI in localStorage/sessionStorage | **PASS** | None found |
| No PHI in URL parameters | **PASS** | Uses path parameters only |
| BAA (Business Associate Agreement) in place | **SKIP** | Legal/business concern |

**Remediation**:
1. Implement audit logging for PHI access
2. Audit all log statements for PHI exposure
3. Configure explicit session timeout

---

### Category 8: Error Handling & Logging

| Check | Status | Notes |
|-------|--------|-------|
| No bare `except:` clauses | **PASS** | None found in app/ |
| Errors logged with context | **PASS** | structlog used throughout |
| No sensitive data in logs | **WARN** | Doctor objects logged |
| Structured logging used (structlog) | **PASS** | Yes |
| Request correlation IDs present | **PASS** | Via logging_context middleware |
| Client errors don't expose internals | **FAIL** | Debug console.log in AdminBar |

**Remediation**:
1. Remove console.log from AdminBar
2. Audit log statements for sensitive data

---

### Category 9: Testing

| Check | Status | Notes |
|-------|--------|-------|
| All tests pass | **FAIL** | Tests fail to run (missing env var) |
| Test coverage > 70% | **SKIP** | Cannot verify |
| Authentication flows tested | **SKIP** | Cannot verify |
| Authorization edge cases tested | **SKIP** | Cannot verify |
| Input validation tested with malicious inputs | **SKIP** | Cannot verify |
| API error responses tested | **WARN** | Test structure exists |
| PHI handling tested | **FAIL** | Cannot verify |

**Remediation**:
1. Fix test configuration (`PYTHON_ENV` handling)
2. Run tests and verify coverage
3. Add security-focused test cases

---

### Category 10: Documentation

| Check | Status | Notes |
|-------|--------|-------|
| Security assumptions documented | **SKIP** | Not found |
| API endpoints documented with auth requirements | **SKIP** | OpenAPI may exist |
| Environment variables documented | **SKIP** | Not found |
| PHI data flow documented | **SKIP** | Not found |
| Incident response contacts documented | **SKIP** | Not found |
| HIPAA compliance documentation exists | **FAIL** | Not found |

**Remediation**:
1. Create security documentation covering auth model, data flow
2. Document environment variables
3. Create incident response runbook
4. Document HIPAA compliance measures

---

## Remediation Priority

### Blocking (Must Fix Before Audit)

| Issue | Effort | Risk |
|-------|--------|------|
| PostgreSQL firewall 0.0.0.0/0 | Low | Critical |
| Blob storage CORS wildcard | Low | Critical |
| .env files in git history | Medium | High |
| Generate JS lockfile | Low | Medium |
| Fix test configuration | Low | Medium |

### High-Risk (Fix Within 1 Week)

| Issue | Effort | Risk |
|-------|--------|------|
| Add rate limiting | Medium | High |
| Implement webhook signature validation | Medium | High |
| Remove debug console.log | Low | Medium |
| Audit log statements | Medium | Medium |

### Medium-Risk (Fix Within 2 Weeks)

| Issue | Effort | Risk |
|-------|--------|------|
| Expand Zod validation | Medium | Low |
| Add security headers | Low | Low |
| Fix ruff linter errors | Low | Low |
| Configure session timeout | Low | Low |
| Add HIPAA audit logging | High | Medium |

### Improvements (Before Audit)

| Issue | Effort | Risk |
|-------|--------|------|
| Create security documentation | High | Low |
| Add authorization failure logging | Low | Low |
| Document environment variables | Medium | Low |
| Add max_length to Pydantic fields | Medium | Low |

---

## Checklist for Audit Day

Before engaging external auditors, verify:

- [ ] PostgreSQL firewall restricted to known IPs
- [ ] `public_network_access_enabled = false` on database
- [ ] Blob storage CORS limited to application domains
- [ ] All `.env` files removed from git history
- [ ] `.gitignore` excludes all env files
- [ ] `pnpm-lock.yaml` committed
- [ ] All tests passing
- [ ] Rate limiting enabled on all endpoints
- [ ] No console.log in production code
- [ ] Webhook signature validation implemented (if using webhooks)
- [ ] HIPAA audit logging implemented
- [ ] Security documentation complete

---

## Commands Reference

```bash
# Check Python dependencies
pip-audit

# Check JS dependencies (after generating lockfile)
cd web && pnpm audit

# Run linter
uv run ruff check app/

# Run tests (after fixing config)
PYTHON_ENV=test uv run pytest

# Check for secrets in git
git log --all --full-history -- "*.env*"
trufflehog git file://. --only-verified

# Terraform security scan
tfsec infra/
```
