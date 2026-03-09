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
- Backend endpoints missing `Depends(AuthenticateClerkRequest)`
- Public routes exposing sensitive data

**Webhook Forgery**
- Missing Svix signature verification on `/webhooks/clerk`
- Processing webhook payload before signature validation
- Webhook secret hardcoded or missing from environment

### P1 — High

**Session Issues**
- Tokens stored in localStorage (should use httpOnly cookies)
- Missing session invalidation on logout
- Token refresh vulnerabilities
- Session timeout not configured

**RBAC Bypass**
- Role checks only on frontend (client-side easily bypassed)
- Missing server-side role validation
- `user.publicMetadata` or `user.privateMetadata` not validated server-side
- Privilege escalation via metadata manipulation

**Redirect URL Validation**
- `redirectUrl` or `afterSignInUrl` not validated
- Open redirect via Clerk sign-in flow
- Missing allowlist for redirect domains

### P2 — Medium

**Multi-Tenant Boundary Issues**
- Organization ID not validated on resource access
- Cross-organization data leakage
- Missing organization membership checks

**Token Handling**
- JWT payload trusted without verification
- Claims not validated (exp, iss, aud)
- Missing token revocation checks

### Clerk Detection Commands

```bash
# Check for missing auth middleware on routes
grep -rn "@router\." app/routes --include="*.py" | grep -v "Depends.*Clerk\|Depends.*auth"

# Check for webhook endpoints
grep -rn "webhook" app/routes --include="*.py" -A 20

# Check for Svix usage (webhook verification)
grep -rn "svix\|Webhook\|verify" app/ --include="*.py"

# Check frontend auth guards
grep -rn "SignedIn\|SignedOut\|useAuth\|useUser" web/ --include="*.tsx"

# Check for redirect URL handling
grep -rn "redirectUrl\|afterSignIn\|afterSignUp" web/ --include="*.tsx"

# Check for role/permission checks
grep -rn "role\|permission\|isAdmin\|publicMetadata" app/ web/ --include="*.py" --include="*.tsx"

# Check for organization boundary enforcement
grep -rn "org_id\|organization\|orgId" app/ --include="*.py"
```

### Clerk Security Checklist

| Check | Location | Verification |
|-------|----------|--------------|
| Webhook signature validation | `/webhooks/clerk` endpoint | Uses `svix.Webhook.verify()` |
| Auth middleware on all protected routes | `app/routes/*.py` | `Depends(AuthenticateClerkRequest)` |
| Frontend route protection | `web/app/routes/*.tsx` | `<SignedIn>` guards |
| Session cookies httpOnly | Clerk config | Clerk handles by default |
| Redirect URL allowlist | Clerk Dashboard | Only trusted domains |
| JWT validation | `AuthenticateClerkRequest` | Verifies with Clerk SDK |
| Role checks server-side | Route handlers | Not trusting client claims |
| Organization isolation | Data access layer | Validates `org_id` on queries |

### Clerk Webhook Implementation Pattern

```python
# CORRECT: Validate signature before any processing
from svix.webhooks import Webhook

CLERK_WEBHOOK_SECRET = os.environ["CLERK_WEBHOOK_SECRET"]

@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request):
    payload = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }

    try:
        wh = Webhook(CLERK_WEBHOOK_SECRET)
        event = wh.verify(payload, headers)
    except Exception as e:
        logger.warning("Webhook signature verification failed", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Safe to process after verification
    event_type = event.get("type")
    if event_type == "user.created":
        await handle_user_created(event["data"])
    elif event_type == "user.deleted":
        await handle_user_deleted(event["data"])

    return {"status": "ok"}
```

### Frontend Auth Pattern

```typescript
// CORRECT: Protected route with server-side data fetching
import { SignedIn, SignedOut, RedirectToSignIn } from "@clerk/remix";

export default function ProtectedPage() {
  return (
    <>
      <SignedIn>
        <ProtectedContent />
      </SignedIn>
      <SignedOut>
        <RedirectToSignIn />
      </SignedOut>
    </>
  );
}

// CORRECT: API call includes auth token (handled by Clerk)
const { data } = useQuery({
  queryKey: ['patients'],
  queryFn: async () => {
    const res = await fetch('/api/patients', {
      credentials: 'include',  // Includes Clerk session cookie
    });
    if (!res.ok) throw new Error('Unauthorized');
    return res.json();
  },
});
```

---

## Terraform/Azure Infrastructure

### P0 — Critical

**Secrets in Terraform**
- Hardcoded credentials in `.tf` files
- Secrets in `terraform.tfvars` committed to git
- State file containing secrets accessible publicly
- Sensitive outputs without `sensitive = true`

**Overly Permissive Network Access**
- Database `public_network_access_enabled = true` with open firewall
- Firewall rules allowing `0.0.0.0/0` (all IPs)
- Storage accounts with public blob access
- Redis with `public_network_access_enabled = true`

**Insecure Storage Configuration**
- Storage containers with `container_access_type = "blob"` or `"container"`
- Missing encryption at rest
- Blob CORS allowing `*` origins

### P1 — High

**Container Security**
- Container registry with `admin_enabled = true` (prefer managed identity)
- Images pulled from untrusted registries
- No image scanning configured
- Running containers as root

**Identity & Access**
- Overly broad role assignments (`Owner`, `Contributor` at subscription level)
- Missing managed identities (using shared keys instead)
- Service principals with excessive permissions
- No separation between environments (dev/staging/prod)

**Encryption & TLS**
- `minimum_tls_version` below 1.2
- `non_ssl_port_enabled = true` on Redis
- Missing HTTPS enforcement on ingress
- Database connections without SSL

### P2 — Medium

**Logging & Monitoring Gaps**
- No Log Analytics workspace configured
- Missing diagnostic settings
- Insufficient retention period (< 90 days for HIPAA)
- No alerts for security events

**Resource Configuration**
- Missing resource locks on critical resources
- No backup configuration or insufficient retention
- Missing tags for cost allocation and ownership
- Auto-scaling not configured (DoS risk)

### Infrastructure Detection Commands

```bash
# Check for public network access
grep -rn "public_network_access_enabled.*true" infra/

# Check for overly permissive firewall rules
grep -rn "0.0.0.0" infra/*.tf

# Check for CORS wildcards
grep -rn 'allowed_origins.*\*' infra/

# Check for admin credentials enabled
grep -rn "admin_enabled.*true" infra/

# Check for missing TLS settings
grep -rn "minimum_tls_version\|non_ssl_port" infra/

# Check for public storage
grep -rn "container_access_type" infra/ | grep -v "private"

# Check for sensitive outputs
grep -rn "^output" infra/*.tf -A 5 | grep -v "sensitive.*true"

# Check for hardcoded secrets (high entropy strings)
grep -rn "password\|secret\|key.*=" infra/*.tf | grep -v "var\.\|local\.\|data\."
```

### Azure-Specific Checks

| Resource | Security Check | Detection |
|----------|---------------|-----------|
| PostgreSQL | Public access disabled | `public_network_access_enabled = false` |
| PostgreSQL | Firewall not 0.0.0.0/0 | No `start_ip_address = "0.0.0.0"` with `end = "255.255.255.255"` |
| PostgreSQL | SSL enforced | `ssl_enforcement_enabled = true` |
| Redis | SSL only | `non_ssl_port_enabled = false` |
| Redis | TLS 1.2+ | `minimum_tls_version = "1.2"` |
| Storage | Private containers | `container_access_type = "private"` |
| Storage | CORS restricted | No `allowed_origins = ["*"]` |
| Container Registry | Managed identity auth | `admin_enabled = false` |
| Container Apps | HTTPS only | Ingress configured with HTTPS |
| Key Vault | Network rules | Private endpoint or service endpoint |

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
| Open redirect | FORBIDDEN | Unvalidated redirect URLs |
| Mass assignment | FORBIDDEN | `**kwargs` with user input |
| Webhook without signature | FORBIDDEN | Missing Svix verification |
| DB firewall 0.0.0.0/0 | FORBIDDEN | Public database access |
| CORS `*` origins | FORBIDDEN | Wildcard CORS in production |
| Container admin credentials | FORBIDDEN | Use managed identity instead |
