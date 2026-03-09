# Security Review Report

**Project**: Cosmaneura
**Date**: 2026-03-09
**Commit**: 82bdedb7cffd3e422d5d1fea7cc0ea74e2e63dda
**Branch**: security-check
**Scope**: Full codebase review (backend, frontend, infrastructure)

---

## Summary

| Severity | Count |
|----------|-------|
| **P0 - Critical** | 3 |
| **P1 - High** | 5 |
| **P2 - Medium** | 5 |
| **P3 - Informational** | 4 |

### Extended Testing Summary

| Test Category | Status |
|---------------|--------|
| Git History Secrets | **CLEAR** - No hardcoded secrets |
| SQL Injection | **CLEAR** - All ORM queries |
| Command Injection | **CLEAR** - Safe subprocess usage |
| XSS/Template Injection | **CLEAR** - Autoescape enabled |
| Insecure Deserialization | **CLEAR** - No pickle/yaml.load |
| Open Redirect | **CLEAR** - No user-controlled redirects |
| Weak Cryptography | **CLEAR** - No md5/sha1 |
| Debug Mode | **CLEAR** - Gated by env vars |
| Exception Handling | **CLEAR** - Safe patterns |

### Key Risks

The most critical findings are: (1) PostgreSQL database is exposed to the entire internet with firewall rules allowing all IPs, (2) multiple `.env` files containing configuration are tracked in git, and (3) blob storage CORS is configured with wildcard origins allowing any domain to make requests. Additionally, there is no webhook signature validation for Clerk webhooks, and the chat API endpoint is unauthenticated by design but handles intake data.

---

## Findings

### [P0] PostgreSQL Firewall Allows All IPs (0.0.0.0/0)

**Location**: `infra/main.tf:461-473`
**Vulnerability Class**: Network Security Misconfiguration

**Description**: The PostgreSQL flexible server has a firewall rule that allows connections from any IP address on the internet (`0.0.0.0` to `255.255.255.255`). Combined with `public_network_access_enabled = true`, this exposes the database directly to the internet. An attacker who obtains database credentials could connect from anywhere.

**Proof of Concept**:
```hcl
resource "azurerm_postgresql_flexible_server_firewall_rule" "all" {
  name             = "allow-all-ips"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "255.255.255.255"
}
```

**Remediation**:
1. Remove the `allow-all-ips` firewall rule
2. Set `public_network_access_enabled = false`
3. Use Azure Private Link or VNet integration for database access
4. If public access is required, restrict to specific IP ranges (office IPs, VPN egress)

```hcl
resource "azurerm_postgresql_flexible_server" "main" {
  # ...
  public_network_access_enabled = false
}

# Remove this resource entirely:
# resource "azurerm_postgresql_flexible_server_firewall_rule" "all" { ... }
```

**Severity Rationale**: P0 because the production database containing PHI is directly accessible from the internet with only password authentication as a barrier.

---

### [P0] Blob Storage CORS Allows Wildcard Origins

**Location**: `infra/main.tf:161-168`
**Vulnerability Class**: CORS Misconfiguration

**Description**: The Azure Storage account for uploads has CORS configured with `allowed_origins = ["*"]`, allowing any website to make cross-origin requests to the storage account. This could enable data exfiltration if combined with other vulnerabilities.

**Proof of Concept**:
```hcl
cors_rule {
  max_age_in_seconds = 86400
  # TODO we should explicitly limit origins, but leaving wide open for development
  allowed_origins = ["*"]
  allowed_headers = ["*"]
  allowed_methods = ["PUT"]
  exposed_headers = ["*"]
}
```

**Remediation**:
```hcl
cors_rule {
  max_age_in_seconds = 86400
  allowed_origins = ["https://app.cosmaneura.com", "https://cosmaneura.com"]
  allowed_headers = ["Content-Type", "Authorization", "x-ms-blob-type"]
  allowed_methods = ["PUT"]
  exposed_headers = ["ETag"]
}
```

**Severity Rationale**: P0 because this affects production storage containing patient audio recordings (PHI).

---

### [P0] Environment Files Tracked in Git

**Location**: Repository root
**Vulnerability Class**: Secrets Exposure

**Description**: Multiple `.env` files are tracked in git, including `.env.production.backend` and `.env.production.frontend`. While the main `.env` file appears to contain only non-sensitive Python configuration, the presence of production env files in git history is a significant risk.

**Files tracked**:
- `.env`
- `.env.dev`
- `.env.dev.local-example`
- `.env.local-example`
- `.env.production.backend`
- `.env.production.frontend`
- `.env.shared`
- `.env.test`
- `.envrc`

**Remediation**:
1. Audit all `.env` files for secrets
2. Remove sensitive files from git history using `git filter-branch` or BFG Repo-Cleaner
3. Update `.gitignore` to exclude all `.env*` files:

```gitignore
# Exclude all env files
.env*
!.env.example
```

4. Rotate any credentials that may have been committed

**Severity Rationale**: P0 because production environment files may contain secrets, and git history preserves all versions.

---

### [P1] No Webhook Signature Validation for Clerk

**Location**: `app/` (missing)
**Vulnerability Class**: Webhook Forgery

**Description**: The codebase uses Clerk for authentication, but there is no webhook endpoint with Svix signature validation. If webhooks are configured in Clerk (e.g., for `user.created`, `user.deleted` events), they could be forged by attackers to create unauthorized accounts or trigger malicious actions.

**Detection**: Searched for `webhook`, `svix`, `verify` in app/ - no matches found.

**Remediation**:
If using Clerk webhooks, implement signature validation:

```python
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
    except Exception:
        raise HTTPException(400, "Invalid signature")

    # Process verified event...
```

**Severity Rationale**: P1 because webhook forgery could lead to unauthorized account creation or privilege escalation, but requires webhooks to be configured in Clerk.

---

### [P1] Chat API Endpoint is Unauthenticated

**Location**: `app/routes/chat.py:21-27`
**Vulnerability Class**: Missing Authentication

**Description**: The `/external/chat` endpoint has no authentication middleware. While it uses intake IDs as a form of authorization (must know the ID to access), this is security through obscurity. The intake flow processes patient health information.

**Proof of Concept**:
```python
chat_api_app = APIRouter(
    prefix="/external/chat",
    dependencies=[
        Depends(aglobal_session),
        # NO AuthenticateClerkRequest here
    ],
)
```

**Remediation**:
Consider one of:
1. Add rate limiting to prevent enumeration
2. Use signed/encrypted intake tokens instead of predictable TypeIDs
3. Add CAPTCHA or proof-of-work for intake flow
4. Require email verification before intake access

**Severity Rationale**: P1 because the intake flow handles patient health information, and intake IDs (TypeIDs) may be guessable or enumerable.

---

### [P1] Container Registry Uses Admin Credentials

**Location**: `infra/main.tf:276-289`
**Vulnerability Class**: Identity & Access Management

**Description**: The Azure Container Registry has `admin_enabled = true`, using shared admin credentials instead of managed identity authentication. This is less secure and harder to audit.

**Remediation**:
1. Upgrade to Standard or Premium SKU
2. Disable admin credentials
3. Use managed identity for Container Apps to pull images

```hcl
resource "azurerm_container_registry" "main" {
  # ...
  sku                 = "Standard"
  admin_enabled       = false
}

# Grant Container Apps managed identity access
resource "azurerm_role_assignment" "container_app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app.app.identity[0].principal_id
}
```

**Severity Rationale**: P1 because shared credentials are harder to rotate and audit than managed identities.

---

### [P1] Debug Logging in AdminBar Component

**Location**: `web/app/components/AdminBar.tsx:29-33`
**Vulnerability Class**: Information Disclosure

**Description**: The AdminBar component logs user data to the browser console in production:

```typescript
// Debug logging
console.log("AdminBar render:", { data, error, hasData: !!data, hasError: !!error })
```

This exposes user information (clerk_id, email, user ID) to anyone who opens browser DevTools.

**Remediation**:
Remove debug logging or wrap in development check:

```typescript
if (import.meta.env.DEV) {
  console.log("AdminBar render:", { data, error })
}
```

**Severity Rationale**: P1 because it exposes user identifiers in production, which could aid targeted attacks.

---

### [P1] Missing Rate Limiting

**Location**: `app/routes/` (all endpoints)
**Vulnerability Class**: Denial of Service / Brute Force

**Description**: No rate limiting is configured on any API endpoints. This allows:
- Brute force attacks on the API key authentication
- Denial of service via expensive operations (transcription, AI summarization)
- Enumeration attacks on intake IDs

**Detection**: Searched for `rate.*limit`, `throttle`, `slowapi` - only found a comment in celery.py.

**Remediation**:
Add SlowAPI or similar rate limiting:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@external_api_app.post("/intake")
@limiter.limit("10/minute")
def create_intake(request: Request, data: AppointmentRequest):
    ...
```

**Severity Rationale**: P1 because lack of rate limiting enables brute force and DoS attacks.

---

### [P2] innerHTML Usage in CopyButton

**Location**: `web/app/components/shared/CopyButton.tsx:33`
**Vulnerability Class**: Potential XSS

**Description**: The CopyButton component accesses `innerHTML`:

```typescript
const htmlContent = html || htmlRef?.current?.innerHTML || ""
```

While this reads content rather than setting it, the HTML is then passed to TurndownService and copied to clipboard. If the source HTML contains malicious content, it could be propagated.

**Remediation**: This is low risk since the content comes from refs to known elements, not user input. Consider using `textContent` where possible.

**Severity Rationale**: P2 because the innerHTML is read (not set) and comes from controlled sources.

---

### [P2] Limited Zod Validation in Frontend

**Location**: `web/app/`
**Vulnerability Class**: Input Validation Gap

**Description**: Zod validation is only used in 2 files (`settings.tsx`, `UploadRecordingButton.tsx`). Many forms may lack client-side validation, relying entirely on backend Pydantic validation.

**Remediation**: Add Zod schemas for all form inputs to provide immediate user feedback and reduce invalid API calls.

**Severity Rationale**: P2 because backend validation exists, but client-side validation improves UX and defense-in-depth.

---

### [P2] No JavaScript Lockfile

**Location**: `web/`
**Vulnerability Class**: Supply Chain Security

**Description**: No lockfile (`pnpm-lock.yaml`, `yarn.lock`, `package-lock.json`) was found in the `web/` directory. This means dependency versions are not pinned, leading to:
- Non-reproducible builds
- Potential installation of compromised package versions

**Remediation**: Run `pnpm install` to generate `pnpm-lock.yaml` and commit it to git.

**Severity Rationale**: P2 because without a lockfile, builds are non-deterministic and vulnerable to dependency confusion.

---

### [P2] Log Statements May Include Sensitive Context

**Location**: `app/routes/admin.py:75`, `app/jobs/sync_clerk.py:24,30,34`
**Vulnerability Class**: Information Disclosure in Logs

**Description**: Several log statements include user objects or identifiers that may include more context than necessary:

```python
log.info("login_as_user", login_as_user=login_as_user)
log.warning("no clerk user found for doctor", doctor=doctor)
```

While structured logging is good, dumping entire objects may inadvertently log sensitive fields.

**Remediation**: Log only IDs:
```python
log.info("login_as_user", login_as_user_id=login_as_user.id)
```

**Severity Rationale**: P2 because the logs use structured logging which typically serializes objects safely, but explicit ID logging is safer.

---

### [P2] Example Notes May Contain PHI

**Location**: `app/models/doctor_note_example.py:40`
**Vulnerability Class**: HIPAA Compliance

**Description**: A TODO comment indicates that example notes stored in the database may contain PHI that should be de-identified:

```python
# TODO the field examples need to be deidentified with surrogates
```

Example notes are used as templates for AI summarization and may contain actual patient data.

**Remediation**:
1. Run existing example notes through Azure Health Deidentification
2. Add de-identification step when saving new example notes
3. Audit existing data for PHI exposure

**Severity Rationale**: P2 because the data is stored server-side and only accessed by authenticated providers, but HIPAA requires de-identification for data used in training/examples.

---

## Positive Observations

1. **Clerk Authentication Properly Implemented**: The `AuthenticateClerkRequest` middleware validates tokens correctly and is applied to internal API routes.

2. **Authorization Checks**: Routes properly check `doctor.id` ownership before returning patient data (e.g., `internal.py:129`).

3. **Pydantic Validation**: All API inputs use Pydantic models with `extra="forbid"`, preventing mass assignment.

4. **PHI De-identification**: Azure Health Deidentification is implemented for AI processing (`app/jobs/anonymize.py`).

5. **Secure Session Configuration**: Sessions use `https_only=True` and `same_site="Lax"` (`middleware/__init__.py:100-106`).

6. **No SQL Injection**: All database queries use SQLModel ORM - no raw SQL string interpolation found.

7. **No eval/exec**: No dangerous code execution patterns found.

8. **No localStorage for Tokens**: No localStorage usage for sensitive data found.

9. **Storage Containers Private**: All Azure storage containers use `container_access_type = "private"`.

10. **TLS Configuration**: Redis requires TLS 1.2+, SSL-only ports enabled.

---

## Recommendations

### Immediate (Before Next Deploy)

1. **Fix PostgreSQL Firewall**: Remove `allow-all-ips` rule, disable public access
2. **Fix Blob CORS**: Replace wildcard with specific origins
3. **Audit .env Files**: Remove secrets from git history, update .gitignore
4. **Remove Debug Logging**: Remove console.log from AdminBar

### Short-Term (Within 2 Weeks)

1. **Add Rate Limiting**: Implement SlowAPI on all endpoints
2. **Implement Webhook Validation**: Add Svix signature verification if using Clerk webhooks
3. **Generate JS Lockfile**: Commit pnpm-lock.yaml
4. **Review Log Statements**: Ensure no sensitive data in log context

### Medium-Term (Within 1 Month)

1. **Migrate to Managed Identity**: Disable ACR admin credentials
2. **Add Zod Validation**: Expand client-side validation
3. **Secure Chat Endpoint**: Add rate limiting and token signing for intake IDs
4. **Network Segmentation**: Consider VNet integration for all Azure resources

---

## Extended Testing Results

### Git History Secrets Scan

**Command**: `git log --all -p | grep -E "sk_live|sk_test|pk_live|pk_test"`

**Findings**:
- Found example keys in documentation: `sk_live_abc123xyz` (not real secrets)
- Found API key prefix definitions: `API_KEY_PREFIX = "sk_live"` (configuration, not secrets)
- Found migration references for TypeID prefixes (not secrets)
- **No actual secrets found in git history**

The `.env.production.*` files use 1Password references (`op://...`) rather than hardcoded secrets, which is the correct pattern.

---

### TODO/FIXME Security Items

**Command**: `grep -rn "TODO|FIXME" app/ infra/`

**Security-Relevant TODOs Found**:

| Location | TODO | Risk |
|----------|------|------|
| `infra/main.tf:163` | "TODO we should explicitly limit origins" | **P0** - CORS wildcard |
| `infra/main.tf:384` | "TODO for development, we should lock this down" | **P0** - Public DB access |
| `app/routes/dependencies/login_as.py:16` | "TODO we shouldn't hard code this" | P2 - Hardcoded path |
| `app/routes/internal.py:126,171` | "TODO error handling for bad IDs" | P2 - Error handling |
| `app/models/doctor_note_example.py:40` | "TODO the field examples need to be deidentified" | P2 - PHI concern |

---

### Exception Handling Analysis

**Command**: `grep -rn "except Exception|except:" app/`

**Findings**:

| Location | Pattern | Assessment |
|----------|---------|------------|
| `app/jobs/anonymize.py:48` | `except Exception as e:` | **SAFE** - Re-raises after logging |
| `app/jobs/transcribe.py:195` | `except Exception as e:` | **SAFE** - Re-raises ValueError |
| `app/jobs/sync_clerk.py:23` | `except Exception:` | **ACCEPTABLE** - Background job continues |
| `app/utils/debug.py:129` | `except Exception as e:` | **SAFE** - Development debug only |

No unsafe exception swallowing found.

---

### Template Injection (SSTI)

**Command**: `grep -rn "jinja|template|render" app/`

**Findings**:
- Jinja2 templates used in `app/templates.py`
- **SAFE**: `autoescape=select_autoescape(["html", "xml"])` is enabled
- Templates only used for email rendering, not user-facing content

---

### Subprocess/Command Execution

**Command**: `grep -rn "subprocess|os.system|os.popen" app/`

**Findings**:
- `app/jobs/transcribe.py:160` uses `subprocess.run()`

**Analysis**:
```python
subprocess.run([
    "ffmpeg",
    "-i", video_path,  # From temp file with UUID prefix
    "-vn", "-acodec", "pcm_s16le", "-f", "wav",
    output_audio_path,
], check=True)
```
- **SAFE**: Uses list arguments (not shell=True)
- Path has UUID prefix preventing traversal
- No user input directly in command

---

### Debug Mode Check

**Command**: `grep -rn "DEBUG.*True|debug.*True" app/`

**Findings**:
- `app/routes/middleware/__init__.py:108-114`: Debug middleware gated by:
  - `is_development()` AND
  - `config("FASTAPI_DEBUG", cast=bool, default=False)`
- **SAFE**: Cannot be enabled in production

---

### Redirect/Open Redirect Analysis

**Command**: `grep -rn "redirect|location.href|window.location" web/`

**Findings**:

| Location | Usage | Assessment |
|----------|-------|------------|
| `AdminBar.tsx:42,48` | `window.location.replace(href("/"))` | **SAFE** - Hardcoded path |
| `routes/index.tsx:13` | Domain check for intake redirect | **SAFE** - Controlled logic |
| `clerk.tsx:46` | `clerk.redirectToSignIn()` | **SAFE** - Clerk SDK |
| `intake.tsx:79` | `window.location.reload()` | **SAFE** - Same page |

No open redirect vulnerabilities found.

---

### Insecure Deserialization

**Command**: `grep -rn "pickle|yaml.load|marshal" app/`

**Result**: No matches. **SAFE**

---

### Weak Cryptography

**Command**: `grep -rn "md5|sha1" app/`

**Result**: No matches. **SAFE**

---

### XML External Entity (XXE)

**Command**: `grep -rn "xml.etree|lxml|parseString" app/`

**Result**: No matches. **SAFE**

---

### Regex Denial of Service (ReDoS)

**Command**: `grep -rn "re.compile|re.match|re.search" app/`

**Result**: No matches in application code. **SAFE**

---

### Assert Statements in Routes

**Command**: `grep -rn "assert" app/routes/`

**Findings**:
- `app/routes/internal.py:178`: `assert appointment`
- `app/routes/chat.py:75`: `assert chat`

**Risk**: If Python runs with `-O` flag, assertions are disabled.

**Recommendation**: Replace with explicit checks:
```python
if not appointment:
    raise HTTPException(404, "Appointment not found")
```

---

### Security Headers

**Command**: `grep -rn "Content-Security-Policy|X-Frame-Options" app/`

**Result**: No security headers configured.

**Recommendation**: Add security headers middleware:
```python
from starlette.middleware import Middleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

# Add headers via middleware or reverse proxy
```

---

### Timing Attack Vectors

**Command**: `grep -rn "==.*password|==.*secret|==.*token" app/`

**Result**: No direct secret comparisons found. API key validation uses TypeID lookup (database timing, not string comparison). **SAFE**

---

### SSL/TLS Verification

**Command**: `grep -rn "verify=False" app/`

**Result**: No matches. **SAFE** - All HTTPS verification enabled.

---

## Additional P3 (Informational) Findings

### 1. Assert Statements in Production Code

**Location**: `app/routes/internal.py:178`, `app/routes/chat.py:75`
**Risk**: Low - assertions disabled with `-O` flag
**Recommendation**: Replace with explicit HTTPException

### 2. Missing Security Headers

**Location**: `app/routes/middleware/__init__.py`
**Risk**: Low - Browser security features not enforced
**Recommendation**: Add CSP, X-Frame-Options, X-Content-Type-Options

### 3. Hardcoded Admin Path

**Location**: `app/routes/dependencies/login_as.py:17`
```python
if request.url.path.startswith("/internal/v1/admin"):
```
**Risk**: Low - Maintenance burden
**Recommendation**: Use constant or config

### 4. PHI in Example Notes Not De-identified

**Location**: `app/models/doctor_note_example.py:40`
**Risk**: Medium - Example notes may contain PHI
**Recommendation**: Implement de-identification for stored examples

---

## Methodology

This review examined:
- All Python backend code in `app/`
- All TypeScript frontend code in `web/app/`
- Terraform infrastructure in `infra/`
- Git-tracked files and .gitignore
- Route authentication and authorization patterns
- Logging statements for PHI exposure
- Dependency management and build configuration

### Extended Testing Performed

| Test | Tool/Method | Result |
|------|-------------|--------|
| Git history secrets | `git log --all -p \| grep` | No secrets found |
| TODO security items | `grep -rn "TODO\|FIXME"` | 2 P0, 3 P2 items |
| Exception handling | Pattern analysis | Safe patterns |
| Template injection | Jinja2 config check | Autoescape enabled |
| Command injection | subprocess analysis | List args, safe |
| Debug mode | Config check | Gated by env vars |
| Open redirect | URL handling analysis | No vulnerabilities |
| Deserialization | pickle/yaml search | Not used |
| Weak crypto | md5/sha1 search | Not used |
| XXE | XML parser search | Not used |
| ReDoS | Regex pattern search | No complex regex |
| Timing attacks | Secret comparison search | Safe patterns |
| SSL verification | verify=False search | Not disabled |

### Tools Used

- grep/ripgrep for pattern matching
- glob for file discovery
- git for history analysis
- Manual code review for logic flaws

### Tools Recommended But Not Available

- `bandit` - Python SAST (not installed)
- `tfsec` - Terraform security scanner (not installed)
- `semgrep` - Multi-language SAST (not installed)
- `pip-audit` - Dependency vulnerability scanner (not available via uv)
