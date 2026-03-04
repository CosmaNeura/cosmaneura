---
name: security-review
description: "Security review for FastAPI/React healthcare codebase. Triggers: security, review, vulnerability, audit, pentest, XSS, injection, HIPAA"
---

# Security Review

You are performing a security review of a HIPAA-regulated healthcare application. Your goal is to identify **real, exploitable vulnerabilities** — not style nits or theoretical concerns. Think like an attacker targeting the application.

## When to Activate

- Reviewing code changes for security issues
- Analyzing API endpoints for vulnerabilities
- Checking frontend components for XSS
- Auditing authentication/authorization flows
- Verifying HIPAA compliance in data handling

---

## Review Scope

If invoked with no arguments, review all pending uncommitted changes:
```bash
git diff HEAD
```

If invoked with arguments (file paths or directories), review those specific targets.

---

## Review Process

### Phase 1: Context Gathering

1. Identify what you're reviewing (backend, frontend, both)
2. Understand the component's purpose and data flows
3. Identify trust boundaries — where does untrusted input enter?
4. Check for existing security controls (Clerk auth, Pydantic validation)
5. Read `.claude/knowledgebase/vulnerabilities.md` for patterns
6. Note any PHI handling — triggers HIPAA requirements

### Phase 2: Vulnerability Analysis

---

## Python/FastAPI Backend

### P0 — Critical

**Secrets Exposure**
- Hardcoded API keys, passwords, or tokens in code
- Secrets in log statements (`structlog`, `logging`, `print`)
- Secrets in error messages returned to clients
- PHI/PII in logs or error responses

**Authentication Bypass**
- Endpoints missing `Depends(AuthenticateClerkRequest)`
- Endpoints missing API key validation
- Authorization checks after data access
- Path traversal bypassing auth (`/api/../admin`)

**SQL Injection**
- String interpolation in SQLModel queries
- Raw SQL with f-strings or `.format()`
- Missing parameterization in custom queries

**HIPAA Violations**
- PHI logged without de-identification
- PHI returned in error messages
- PHI stored without encryption
- Missing access logging for PHI

### P1 — High

**Insecure Deserialization**
- `pickle.loads()` on untrusted data
- Missing Pydantic validation on request bodies
- Type coercion vulnerabilities

**SSRF (Server-Side Request Forgery)**
- User-controlled URLs in HTTP requests
- Missing URL validation on outbound requests
- Internal service URLs exposed

**Unsafe Error Handling**
- Bare `except:` clauses hiding errors
- Stack traces returned to clients
- Sensitive data in exception messages

**Celery Task Vulnerabilities**
- Untrusted data in task arguments
- Missing validation in job handlers
- Task result exposure

### P2 — Medium

**Missing Rate Limiting**
- Auth endpoints without throttling
- Expensive operations unprotected
- API endpoints without limits

**Logging Issues**
- PII in logs (email, name, etc.)
- Missing request correlation IDs
- Insufficient audit logging

**Race Conditions**
- TOCTOU in database operations
- Shared state without locks
- Async race conditions

---

## TypeScript/React Frontend

### P0 — Critical

**Secrets in Frontend**
- API keys in client code
- Secrets in `VITE_*` environment variables
- Hardcoded credentials

**XSS (Cross-Site Scripting)**
- `dangerouslySetInnerHTML` with user content
- `innerHTML` assignment
- `eval()`, `Function()`, `setTimeout(string)`
- URL parameters rendered without sanitization

**PHI Exposure**
- PHI in localStorage/sessionStorage
- PHI in URL parameters
- PHI in client-side state without protection

### P1 — High

**Insecure Storage**
- Tokens in localStorage (should use httpOnly cookies)
- Sensitive data in sessionStorage
- PHI cached client-side

**Missing Input Validation**
- Forms without Zod validation
- API responses used without validation
- User input directly in queries

**Unsafe Clerk Integration**
- Missing auth checks on protected routes
- Clerk tokens exposed inappropriately
- Session handling issues

### P2 — Medium

**Missing Security Headers**
- No CSP configuration
- CORS misconfiguration (`*` origins)
- Missing X-Frame-Options

**Dependency Issues**
- Known vulnerable packages
- Outdated dependencies
- Unnecessary dependencies

---

## Clerk Authentication

### P0 — Critical

**Auth Bypass**
- Routes missing `<SignedIn>` / `<SignedOut>` guards
- API calls without Clerk token validation
- Backend endpoints missing auth middleware

### P1 — High

**Session Issues**
- Tokens stored insecurely
- Missing session invalidation
- Token refresh vulnerabilities

**RBAC Bypass**
- Role checks only on frontend
- Missing server-side role validation
- Privilege escalation paths

---

## Phase 3: Report Generation

For each finding, produce:

```markdown
### [SEVERITY] Title

**Location**: `app/routes/internal/patients.py:L42-L55`
**Vulnerability Class**: SQL Injection / XSS / Auth Bypass / etc.

**Description**: What the issue is and why it matters. Who is the attacker,
what do they do, what is the impact?

**Proof of Concept**: Concrete steps or code showing exploitation.

**Remediation**: Specific code changes to fix the issue.

**Severity Rationale**: Why this severity level was assigned.
```

### Phase 4: False Positive Filtering

Before presenting findings, verify:

- **Is this reachable?** Can an external attacker trigger this?
- **Is the impact real?** Would exploitation cause harm?
- **Existing mitigation?** Does other code prevent exploitation?
- **Production code?** Not just tests/examples?

Remove or downgrade findings that fail these checks.

---

## Output Format

```markdown
## Security Review Summary

**Scope**: [files/components reviewed]
**Date**: [current date]
**Findings**: X Critical, Y High, Z Medium

### Key Risks
[1-3 sentence summary of most important findings]

### Findings
[Individual findings in severity order, P0 first]

### Positive Observations
[Security practices that are well-implemented]

### Recommendations
[Systemic improvements beyond individual fixes]
```

---

## Anti-Patterns

| Pattern | Status | Detection |
|---------|--------|-----------|
| PHI in logs | FORBIDDEN | Search for patient data in log calls |
| Hardcoded secrets | FORBIDDEN | Search for API key patterns |
| SQL string concat | FORBIDDEN | Search for f-strings with SQL |
| `dangerouslySetInnerHTML` | FORBIDDEN | Grep for usage |
| Missing auth middleware | FORBIDDEN | Check route decorators |
| Bare `except:` | FORBIDDEN | Search for bare except clauses |
| localStorage for tokens | FORBIDDEN | Search for token storage |
