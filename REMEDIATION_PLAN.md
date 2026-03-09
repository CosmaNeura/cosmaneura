# Security Remediation Plan

**Project**: Cosmaneura
**Date**: 2026-03-09
**Based on**: SECURITY_REVIEW.md, AUDIT_PREP.md

---

## Executive Summary

This document provides step-by-step remediation for all security findings. Items are organized by priority and include specific commands, code changes, and verification steps.

| Priority | Items | Estimated Effort |
|----------|-------|------------------|
| **CRITICAL (P0)** | 3 | 2-4 hours |
| **HIGH (P1)** | 5 | 1-2 days |
| **MEDIUM (P2)** | 5 | 2-3 days |
| **LOW (P3)** | 4 | 1 day |

---

## Phase 1: Critical (P0) - Complete Before Next Deploy

### 1.1 Fix PostgreSQL Firewall (0.0.0.0/0)

**Risk**: Production database exposed to entire internet
**Effort**: 30 minutes

#### Step 1: Update Terraform Configuration

Edit `infra/main.tf`:

```hcl
# CHANGE this (around line 385):
resource "azurerm_postgresql_flexible_server" "main" {
  # ... existing config ...

  # Change FROM:
  # public_network_access_enabled = true

  # Change TO:
  public_network_access_enabled = false
}
```

#### Step 2: Remove Open Firewall Rule

Delete or comment out (around line 468-473):

```hcl
# DELETE this entire resource:
# resource "azurerm_postgresql_flexible_server_firewall_rule" "all" {
#   name             = "allow-all-ips"
#   server_id        = azurerm_postgresql_flexible_server.main.id
#   start_ip_address = "0.0.0.0"
#   end_ip_address   = "255.255.255.255"
# }
```

#### Step 3: Keep Azure Services Rule (if needed)

The `allow-access-from-azure-services` rule (0.0.0.0 to 0.0.0.0) allows Azure services to connect. Keep this if Container Apps need database access:

```hcl
# KEEP this rule:
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "allow-access-from-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
```

#### Step 4: Apply Changes

```bash
cd infra
tofu plan    # Review changes
tofu apply   # Apply changes
```

#### Step 5: Verify

```bash
# Try to connect from your local machine (should fail)
psql "postgresql://user:pass@cosmaneura-prod-postgres.postgres.database.azure.com:5432/production"
# Expected: Connection refused or timeout
```

---

### 1.2 Fix Blob Storage CORS Wildcard

**Risk**: Any website can make requests to storage containing PHI
**Effort**: 15 minutes

#### Step 1: Update Terraform Configuration

Edit `infra/main.tf` (around line 159-168):

```hcl
resource "azurerm_storage_account" "uploads" {
  # ... existing config ...

  blob_properties {
    cors_rule {
      max_age_in_seconds = 86400

      # CHANGE FROM:
      # allowed_origins = ["*"]
      # allowed_headers = ["*"]
      # exposed_headers = ["*"]

      # CHANGE TO:
      allowed_origins = [
        "https://app.cosmaneura.com",
        "https://chat.cosmaneura.com"
      ]
      allowed_headers = [
        "Content-Type",
        "x-ms-blob-type",
        "x-ms-blob-content-type"
      ]
      allowed_methods = ["PUT"]
      exposed_headers = ["ETag"]
    }
  }
}
```

#### Step 2: Apply Changes

```bash
cd infra
tofu plan
tofu apply
```

#### Step 3: Verify

```bash
# Test CORS from unauthorized origin (should fail)
curl -I -X OPTIONS \
  -H "Origin: https://evil.com" \
  -H "Access-Control-Request-Method: PUT" \
  "https://cosmaneuraprodupload.blob.core.windows.net/uploads/test"

# Should NOT return Access-Control-Allow-Origin header
```

---

### 1.3 Remove .env Files from Git

**Risk**: Production configuration exposed in repository history
**Effort**: 1-2 hours

#### Step 1: Audit Current .env Files

```bash
# List all tracked .env files
git ls-files | grep -i "\.env"

# Check each file for actual secrets (not op:// references)
for f in $(git ls-files | grep -i "\.env"); do
  echo "=== $f ==="
  grep -v "^#" "$f" | grep -v "op://" | grep -v "^export [A-Z_]*=$" | head -10
done
```

#### Step 2: Update .gitignore

Edit `.gitignore`:

```gitignore
# Add at the top of the file:

# Environment files - NEVER commit secrets
.env
.env.*
!.env.example
!.env.local-example
!.env.dev.local-example

# Already have:
*.env.*local
```

#### Step 3: Remove Files from Git Tracking (Keep Locally)

```bash
# Remove from git but keep local copies
git rm --cached .env
git rm --cached .env.dev
git rm --cached .env.production.backend
git rm --cached .env.production.frontend
git rm --cached .env.shared
git rm --cached .env.test
git rm --cached .envrc

# Commit the removal
git add .gitignore
git commit -m "fix(security): remove .env files from git tracking

These files use 1Password references, not hardcoded secrets,
but tracking them exposes vault structure and configuration.

SECURITY: P0 remediation"
```

#### Step 4: Clean Git History (Optional but Recommended)

If any actual secrets were ever committed, clean the history:

```bash
# Install BFG Repo-Cleaner
brew install bfg

# Create a backup
git clone --mirror git@github.com:CosmaNeura/cosmaneura.git cosmaneura-backup.git

# Remove .env files from all history
bfg --delete-files '.env*' cosmaneura-backup.git

# Or use git filter-repo (more modern)
pip install git-filter-repo
git filter-repo --path .env --path .env.dev --path .env.production.backend \
  --path .env.production.frontend --path .env.shared --path .env.test \
  --path .envrc --invert-paths
```

#### Step 5: Rotate Credentials

Even though files use 1Password references, rotate these as a precaution:
- [ ] PostgreSQL password
- [ ] Redis access key
- [ ] Session secret key
- [ ] Any API keys referenced

#### Step 6: Verify

```bash
# Confirm files are no longer tracked
git ls-files | grep -i "\.env"
# Should return nothing

# Confirm files still exist locally
ls -la .env*
# Should show files
```

---

## Phase 2: High (P1) - Complete Within 1 Week

### 2.1 Add Rate Limiting

**Risk**: Brute force, DoS, enumeration attacks
**Effort**: 2-3 hours

#### Step 1: Install SlowAPI

```bash
uv add slowapi
```

#### Step 2: Create Rate Limiter Configuration

Create `app/routes/middleware/rate_limit.py`:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse

limiter = Limiter(key_func=get_remote_address)

async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": str(exc.detail),
        },
        headers={"Retry-After": "60"},
    )
```

#### Step 3: Add to Application

Edit `app/server.py`:

```python
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.routes.middleware.rate_limit import limiter

# Add after app creation:
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

#### Step 4: Apply to Sensitive Endpoints

Edit `app/routes/api.py`:

```python
from app.routes.middleware.rate_limit import limiter

@external_api_app.post("/intake")
@limiter.limit("10/minute")
def create_intake(request: Request, data: AppointmentRequest) -> AppointmentResponse:
    return app.commands.intake.create.perform(data)
```

Edit `app/routes/chat.py`:

```python
from app.routes.middleware.rate_limit import limiter

@chat_api_app.get("/intake/{intake_id}/question")
@limiter.limit("30/minute")
def get_last_question(request: Request, intake: Intake = Depends(process_intake)) -> ChatResponse:
    # ...

@chat_api_app.post("/intake/{intake_id}/question")
@limiter.limit("20/minute")
def get_next_question(request: Request, data: ChatInput, intake: Intake = Depends(process_intake)) -> ChatResponse:
    # ...
```

#### Step 5: Verify

```bash
# Test rate limiting
for i in {1..15}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST "https://app.cosmaneura.com/external/v1/intake" \
    -H "Authorization: Bearer test"
done
# Should see 429 responses after limit reached
```

---

### 2.2 Implement Webhook Signature Validation

**Risk**: Forged webhook events could create unauthorized accounts
**Effort**: 1-2 hours

#### Step 1: Install Svix

```bash
uv add svix
```

#### Step 2: Add Webhook Secret to Configuration

Edit `app/configuration/clerk.py`:

```python
from decouple import config

CLERK_PRIVATE_KEY = config("CLERK_PRIVATE_KEY", cast=str)
CLERK_WEBHOOK_SECRET = config("CLERK_WEBHOOK_SECRET", cast=str, default="")
```

#### Step 3: Create Webhook Endpoint

Create `app/routes/webhooks.py`:

```python
from fastapi import APIRouter, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app import log
from app.configuration.clerk import CLERK_WEBHOOK_SECRET

webhook_app = APIRouter(prefix="/webhooks")


@webhook_app.post("/clerk")
async def clerk_webhook(request: Request):
    if not CLERK_WEBHOOK_SECRET:
        log.warning("Clerk webhook received but CLERK_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    payload = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }

    try:
        wh = Webhook(CLERK_WEBHOOK_SECRET)
        event = wh.verify(payload, headers)
    except WebhookVerificationError as e:
        log.warning("Webhook signature verification failed", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event.get("type")
    log.info("Clerk webhook received", event_type=event_type)

    if event_type == "user.created":
        await handle_user_created(event["data"])
    elif event_type == "user.updated":
        await handle_user_updated(event["data"])
    elif event_type == "user.deleted":
        await handle_user_deleted(event["data"])

    return {"status": "ok"}


async def handle_user_created(data: dict):
    log.info("User created", user_id=data.get("id"))
    # Add user creation logic


async def handle_user_updated(data: dict):
    log.info("User updated", user_id=data.get("id"))
    # Add user update logic


async def handle_user_deleted(data: dict):
    log.info("User deleted", user_id=data.get("id"))
    # Add user deletion logic
```

#### Step 4: Register Webhook Router

Edit `app/server.py`:

```python
from app.routes.webhooks import webhook_app

# Add after other router includes:
app.include_router(webhook_app)
```

#### Step 5: Add Environment Variable

Add to `.env.production.backend`:

```bash
export CLERK_WEBHOOK_SECRET="op://${OP_VAULT_UID}/your-clerk-item/webhook-secret"
```

#### Step 6: Configure in Clerk Dashboard

1. Go to Clerk Dashboard → Webhooks
2. Add endpoint: `https://app.cosmaneura.com/webhooks/clerk`
3. Select events: `user.created`, `user.updated`, `user.deleted`
4. Copy the signing secret to 1Password

---

### 2.3 Remove Debug Logging from AdminBar

**Risk**: User identifiers exposed in browser console
**Effort**: 10 minutes

#### Step 1: Edit AdminBar Component

Edit `web/app/components/AdminBar.tsx`:

```typescript
// REMOVE these lines (around line 28-33):
// Debug logging
// console.log("AdminBar render:", { data, error, hasData: !!data, hasError: !!error })

// ...

// REMOVE this line (around line 32):
// console.log("AdminBar returning early:", { data, error })
```

Or wrap in development check:

```typescript
if (import.meta.env.DEV) {
  console.log("AdminBar render:", { data, error, hasData: !!data, hasError: !!error })
}
```

#### Step 2: Verify

```bash
# Build and check for console.log
cd web
pnpm build
grep -r "console.log" dist/ | grep -i admin
# Should return nothing
```

---

### 2.4 Generate JavaScript Lockfile

**Risk**: Non-reproducible builds, supply chain attacks
**Effort**: 15 minutes

#### Step 1: Install Correct Node/pnpm Versions

```bash
# Check required versions
cat web/package.json | grep -A2 '"engines"'

# Install correct versions
nvm install 22.11.0
nvm use 22.11.0
npm install -g pnpm@9.15.9
```

#### Step 2: Generate Lockfile

```bash
cd web
pnpm install
```

#### Step 3: Commit Lockfile

```bash
git add pnpm-lock.yaml
git commit -m "chore: add pnpm lockfile for reproducible builds

SECURITY: P1 remediation - prevents supply chain attacks"
```

#### Step 4: Verify

```bash
# Run audit
pnpm audit

# Verify lockfile integrity
pnpm install --frozen-lockfile
```

---

### 2.5 Migrate Container Registry to Managed Identity

**Risk**: Shared admin credentials harder to audit
**Effort**: 1-2 hours

#### Step 1: Upgrade SKU (if needed)

Edit `infra/main.tf`:

```hcl
resource "azurerm_container_registry" "main" {
  name                = "${local.base_name_alphanumeric}registry"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  # CHANGE FROM Basic to Standard:
  sku = "Standard"

  # DISABLE admin credentials:
  admin_enabled = false
}
```

#### Step 2: Add Managed Identity to Container Apps

```hcl
resource "azurerm_container_app" "app" {
  # ... existing config ...

  identity {
    type = "SystemAssigned"
  }
}
```

#### Step 3: Grant ACR Pull Permission

```hcl
resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app.app.identity[0].principal_id
}
```

#### Step 4: Update Container App Registry Config

```hcl
resource "azurerm_container_app" "app" {
  # ... existing config ...

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_container_app.app.identity[0].principal_id
  }
}
```

#### Step 5: Apply and Verify

```bash
cd infra
tofu plan
tofu apply

# Verify container app can pull images
az containerapp show --name cosmaneura-prod-app --resource-group cosmaneura-prod
```

---

## Phase 3: Medium (P2) - Complete Within 2 Weeks

### 3.1 Expand Zod Validation

**Effort**: 2-4 hours

#### Step 1: Create Shared Schemas

Create `web/app/lib/schemas.ts`:

```typescript
import { z } from "zod"

export const patientSchema = z.object({
  name: z.string().min(1).max(200),
  date_of_birth: z.string().datetime(),
})

export const appointmentSchema = z.object({
  appointment_date: z.string().datetime(),
  patient_name: z.string().min(1).max(200),
  date_of_birth: z.string().datetime(),
})

export const settingsSchema = z.object({
  biography: z.string().max(5000).nullable(),
})
```

#### Step 2: Apply to Forms

Update form components to use schemas with react-hook-form.

---

### 3.2 De-identify Example Notes

**Effort**: 2-3 hours

#### Step 1: Create Migration

```bash
uv run alembic revision -m "deidentify_example_notes"
```

#### Step 2: Implement Migration

```python
from alembic import op
from app.jobs.anonymize import deidentify
from app.models.doctor_note_example import DoctorNoteExample

def upgrade():
    # Get all example notes
    examples = DoctorNoteExample.all()

    for example in examples:
        if example.field_examples:
            deidentified = {}
            for key, value in example.field_examples.items():
                if isinstance(value, str):
                    deidentified[key] = deidentify(value)
                else:
                    deidentified[key] = value
            example.field_examples = deidentified
            example.save()
```

---

### 3.3 Add Input Length Limits

**Effort**: 1-2 hours

Update Pydantic models with explicit limits:

```python
from pydantic import BaseModel, Field

class VisitAudioUploadRequest(BaseModel):
    file_name: str = Field(..., max_length=255)
    patient_name: str = Field(..., max_length=200)
    # ...
```

---

### 3.4 Review Log Statements

**Effort**: 1-2 hours

Search and update:

```bash
grep -rn "log\.(info|error|warn|debug).*=" app/ --include="*.py"
```

Change from:
```python
log.info("login_as_user", login_as_user=login_as_user)
```

To:
```python
log.info("login_as_user", user_id=str(login_as_user.id))
```

---

### 3.5 Replace Assert with HTTPException

**Effort**: 30 minutes

Edit `app/routes/internal.py`:

```python
# CHANGE FROM:
assert appointment

# CHANGE TO:
if not appointment:
    raise HTTPException(status_code=404, detail="Appointment not found")
```

---

## Phase 4: Low (P3) - Complete Within 1 Month

### 4.1 Add Security Headers

Create middleware or configure in reverse proxy:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response
```

### 4.2 Extract Hardcoded Admin Path

```python
# app/routes/constants.py
ADMIN_PATH_PREFIX = "/internal/v1/admin"

# app/routes/dependencies/login_as.py
from app.routes.constants import ADMIN_PATH_PREFIX

if request.url.path.startswith(ADMIN_PATH_PREFIX):
    return
```

### 4.3 Configure Session Timeout

In Clerk Dashboard:
1. Go to Sessions
2. Set "Session lifetime" to appropriate value (e.g., 24 hours)
3. Set "Inactivity timeout" to appropriate value (e.g., 1 hour)

### 4.4 Add HIPAA Audit Logging

Create audit logging for PHI access:

```python
# app/audit.py
from app import log

def log_phi_access(user_id: str, resource_type: str, resource_id: str, action: str):
    log.info(
        "phi_access",
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        _audit=True,  # Tag for filtering
    )
```

---

## Verification Checklist

After completing remediation, verify each item:

### P0 Verification
- [ ] `tofu plan` shows no public DB access
- [ ] Cannot connect to PostgreSQL from external IP
- [ ] CORS rejects requests from unauthorized origins
- [ ] `git ls-files | grep .env` returns nothing

### P1 Verification
- [ ] Rate limiting returns 429 after threshold
- [ ] Webhook endpoint rejects unsigned requests
- [ ] No console.log in production bundle
- [ ] `pnpm-lock.yaml` exists and is committed
- [ ] Container app uses managed identity

### P2 Verification
- [ ] All forms have Zod validation
- [ ] Example notes contain surrogate data
- [ ] Log statements only contain IDs
- [ ] No assert statements in routes

### P3 Verification
- [ ] Security headers present in responses
- [ ] Admin path uses constant
- [ ] Session timeout configured in Clerk
- [ ] Audit logs capture PHI access

---

## Rollback Procedures

### PostgreSQL Firewall Rollback

If applications can't connect after firewall changes:

```bash
# Temporarily re-enable public access
cd infra
# Uncomment the firewall rule in main.tf
tofu apply

# Debug connection issues
az postgres flexible-server firewall-rule list \
  --resource-group cosmaneura-prod \
  --name cosmaneura-prod-postgres
```

### CORS Rollback

If uploads fail after CORS changes:

```bash
# Check browser console for CORS errors
# Temporarily widen origins if needed
```

### Rate Limiting Rollback

If legitimate users are blocked:

```python
# Increase limits or disable temporarily
@limiter.limit("100/minute")  # Increase limit
# or
@limiter.exempt  # Disable for endpoint
```

---

## Timeline

| Week | Tasks |
|------|-------|
| **Week 1** | P0 items (PostgreSQL, CORS, .env) |
| **Week 2** | P1 items (rate limiting, webhooks, debug logs) |
| **Week 3** | P1 items (lockfile, managed identity) |
| **Week 4** | P2 items (Zod, de-identify, logging) |
| **Week 5** | P3 items (headers, audit logging) |

---

## Sign-Off

| Item | Completed | Verified | Date | By |
|------|-----------|----------|------|-----|
| PostgreSQL Firewall | [ ] | [ ] | | |
| CORS Wildcard | [ ] | [ ] | | |
| .env Files | [ ] | [ ] | | |
| Rate Limiting | [ ] | [ ] | | |
| Webhook Validation | [ ] | [ ] | | |
| Debug Logging | [ ] | [ ] | | |
| JS Lockfile | [ ] | [ ] | | |
| Managed Identity | [ ] | [ ] | | |
| Zod Validation | [ ] | [ ] | | |
| De-identify Notes | [ ] | [ ] | | |
| Log Statements | [ ] | [ ] | | |
| Assert Statements | [ ] | [ ] | | |
| Security Headers | [ ] | [ ] | | |
| Session Timeout | [ ] | [ ] | | |
| Audit Logging | [ ] | [ ] | | |
