# Vulnerability Patterns — Cosmaneura

Reference knowledgebase for security review of the Cosmaneura healthcare application. Covers FastAPI backend, React frontend, Clerk authentication, and HIPAA compliance.

---

## Vulnerability Class 1: Secrets Exposure

**Risk**: Exposed secrets allow attackers to impersonate the application, access backend systems, or compromise patient data.

### Detection Patterns

```python
# VULNERABLE: Hardcoded secret
CLERK_SECRET_KEY = "sk_live_abc123xyz"

# VULNERABLE: Secret in logs
structlog.get_logger().info("Using API key", api_key=config.api_key)

# VULNERABLE: Secret in error message
raise HTTPException(status_code=401, detail=f"Invalid key: {api_key}")

# SAFE: Environment variable
CLERK_SECRET_KEY = os.environ["CLERK_SECRET_KEY"]

# SAFE: Structured logging without secrets
structlog.get_logger().info("API request authenticated", user_id=user.id)
```

```typescript
// VULNERABLE: Secret in frontend (gets bundled!)
const API_KEY = import.meta.env.VITE_SECRET_KEY;

// VULNERABLE: Hardcoded credential
const clerkKey = "pk_live_abc123";

// SAFE: Only public keys in frontend
const clerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;
```

### Key Checks

- Search for string literals with high entropy
- Check `VITE_*` env vars don't contain secrets
- Verify secrets aren't in structlog calls
- Check `.gitignore` for secret patterns

---

## Vulnerability Class 2: HIPAA Violations

**Risk**: PHI exposure violates federal law, incurs massive fines, and destroys patient trust.

### Detection Patterns

```python
# VULNERABLE: PHI in logs
logger.info("Processing patient", patient_name=patient.name, diagnosis=patient.diagnosis)

# VULNERABLE: PHI in error response
raise HTTPException(
    status_code=404,
    detail=f"Patient {patient.name} with SSN {patient.ssn} not found"
)

# VULNERABLE: PHI without de-identification to AI
response = await openai.chat.completions.create(
    messages=[{"role": "user", "content": patient.medical_notes}]  # Raw PHI!
)

# SAFE: De-identify before AI
deidentified = await azure_deidentify(patient.medical_notes)
response = await openai.chat.completions.create(
    messages=[{"role": "user", "content": deidentified}]
)

# SAFE: Logging with IDs only
logger.info("Processing patient", patient_id=patient.id)
```

```typescript
// VULNERABLE: PHI in localStorage
localStorage.setItem('currentPatient', JSON.stringify(patient));

// VULNERABLE: PHI in URL
navigate(`/patients?name=${patient.name}&diagnosis=${patient.diagnosis}`);

// SAFE: Use IDs only
navigate(`/patients/${patient.id}`);
```

### Key Checks

- Search for patient data in log calls
- Check error messages for PHI
- Verify de-identification before AI processing
- Check localStorage/sessionStorage for PHI
- Audit URL parameters for sensitive data

---

## Vulnerability Class 3: Authentication Bypass

**Risk**: Authentication bypass allows attackers to access protected resources without valid credentials.

### Detection Patterns

```python
# VULNERABLE: Missing auth dependency
@router.get("/patients/{patient_id}")
async def get_patient(patient_id: str):  # No auth!
    return await PatientService.get(patient_id)

# VULNERABLE: Auth check after data access
@router.get("/patients/{patient_id}")
async def get_patient(patient_id: str, request: Request):
    patient = await PatientService.get(patient_id)  # Accessed before auth!
    if not request.state.user:
        raise HTTPException(status_code=401)
    return patient

# SAFE: Auth middleware first
@router.get("/patients/{patient_id}")
async def get_patient(
    patient_id: str,
    auth: AuthenticatedUser = Depends(AuthenticateClerkRequest),
):
    return await PatientService.get(patient_id)
```

```typescript
// VULNERABLE: Client-side only check
function AdminPanel() {
  const { user } = useUser();
  if (user?.role !== 'admin') return null;  // Attacker can bypass!
  return <AdminContent />;
}

// SAFE: Server-side authorization + client guard
function AdminPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-data'],
    queryFn: () => api.getAdminData(),  // Server validates role
  });
  // ...
}
```

### Key Checks

- Every protected route has `Depends(AuthenticateClerkRequest)`
- Auth checks happen before any data access
- Server-side auth is always enforced
- No path traversal bypasses (`/api/../admin`)

---

## Vulnerability Class 4: Authorization Bypass (IDOR)

**Risk**: Insecure Direct Object References allow users to access other users' patient data.

### Detection Patterns

```python
# VULNERABLE: No ownership check
@router.get("/patients/{patient_id}")
async def get_patient(
    patient_id: str,
    auth: AuthenticatedUser = Depends(AuthenticateClerkRequest),
):
    # Any authenticated user can access any patient!
    return await PatientService.get(patient_id)

# VULNERABLE: Trusting user-provided IDs
@router.post("/appointments")
async def create_appointment(
    request: CreateAppointmentRequest,
    auth: AuthenticatedUser = Depends(AuthenticateClerkRequest),
):
    return await AppointmentService.create(
        provider_id=request.provider_id,  # Attacker can set any provider!
        patient_id=request.patient_id,
    )

# SAFE: Verify ownership/access
@router.get("/patients/{patient_id}")
async def get_patient(
    patient_id: str,
    auth: AuthenticatedUser = Depends(AuthenticateClerkRequest),
):
    patient = await PatientService.get(patient_id)
    if patient.provider_id != auth.provider_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return patient
```

### Key Checks

- Resource access checks ownership/permissions
- User IDs come from auth context, not request
- Bulk operations check permissions on all items
- Provider can only access their patients

---

## Vulnerability Class 5: SQL Injection

**Risk**: Injection allows attackers to access, modify, or delete patient data.

### Detection Patterns

```python
# VULNERABLE: String interpolation
query = f"SELECT * FROM patients WHERE name = '{name}'"
await session.execute(text(query))

# VULNERABLE: Format string
query = "SELECT * FROM patients WHERE id = {}".format(patient_id)

# SAFE: SQLModel (parameterized)
patient = await session.exec(
    select(Patient).where(Patient.id == patient_id)
).first()

# SAFE: Parameterized raw query (if needed)
await session.execute(
    text("SELECT * FROM patients WHERE id = :id"),
    {"id": patient_id}
)
```

### Key Checks

- No f-strings or `.format()` with SQL
- Use SQLModel/SQLAlchemy ORM exclusively
- Any raw SQL uses parameterized queries
- Database user has minimal permissions

---

## Vulnerability Class 6: Cross-Site Scripting (XSS)

**Risk**: XSS allows attackers to execute scripts in users' browsers, stealing sessions or patient data.

### Detection Patterns

```typescript
// VULNERABLE: dangerouslySetInnerHTML with user content
<div dangerouslySetInnerHTML={{ __html: patientNotes }} />

// VULNERABLE: innerHTML assignment
element.innerHTML = userInput;

// VULNERABLE: eval with user input
eval(userProvidedCode);

// VULNERABLE: javascript: URL
<a href={userProvidedUrl}>Click</a>  // Could be javascript:alert(1)

// SAFE: React's default escaping
<div>{patientNotes}</div>

// SAFE: Sanitize if HTML needed
import DOMPurify from 'dompurify';
<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(notes) }} />

// SAFE: Validate URL scheme
const safeUrl = url.startsWith('https://') ? url : '#';
```

### Key Checks

- No `dangerouslySetInnerHTML` without sanitization
- No `innerHTML`, `outerHTML` with user content
- No `eval()`, `Function()`, `setTimeout(string)`
- URL inputs validate scheme

---

## Vulnerability Class 7: Celery Task Vulnerabilities

**Risk**: Insecure task handling can lead to code execution, data exposure, or resource exhaustion.

### Detection Patterns

```python
# VULNERABLE: Deserializing untrusted data in task
@celery_app.task
def process_data(pickled_data: bytes):
    data = pickle.loads(pickled_data)  # Code execution!
    ...

# VULNERABLE: No validation on task args
@celery_app.task
def send_to_patient(patient_id: str, message: str):
    patient = Patient.get(patient_id)  # No access control!
    send_email(patient.email, message)

# VULNERABLE: Task results with PHI
@celery_app.task
def get_patient_records(patient_id: str):
    patient = Patient.get(patient_id)
    return patient.dict()  # PHI in Redis!

# SAFE: Validate and authorize in task
@celery_app.task
def send_to_patient(provider_id: str, patient_id: str, message: str):
    patient = Patient.get(patient_id)
    if patient.provider_id != provider_id:
        raise PermissionError("Access denied")
    send_email(patient.email, message)

# SAFE: Return IDs, not PHI
@celery_app.task
def process_patient(patient_id: str) -> str:
    # ... processing ...
    return patient_id  # ID only, not PHI
```

### Key Checks

- No `pickle.loads()` on untrusted data
- Authorization checks in task handlers
- No PHI in task results (stored in Redis)
- Input validation on all task arguments

---

## Vulnerability Class 8: Unsafe Error Handling

**Risk**: Poor error handling exposes internal details, crashes the application, or leaves inconsistent state.

### Detection Patterns

```python
# VULNERABLE: Bare except
try:
    patient = await get_patient(patient_id)
except:  # Catches KeyboardInterrupt, SystemExit!
    return None

# VULNERABLE: Stack trace to client
@app.exception_handler(Exception)
async def handle_exception(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "traceback": traceback.format_exc()}
    )

# VULNERABLE: PHI in error
except PatientNotFoundError:
    raise HTTPException(
        status_code=404,
        detail=f"Patient {patient.name} not found"  # PHI!
    )

# SAFE: Specific exception handling
try:
    patient = await get_patient(patient_id)
except PatientNotFoundError:
    raise HTTPException(status_code=404, detail="Patient not found")

# SAFE: Generic error to client, details to logs
@app.exception_handler(Exception)
async def handle_exception(request: Request, exc: Exception):
    logger.exception("Unhandled error", request_id=request.state.request_id)
    return JSONResponse(
        status_code=500,
        content={"error": "An internal error occurred"}
    )
```

### Key Checks

- No bare `except:` clauses
- Specific exceptions caught and handled
- Error messages don't expose internals or PHI
- Errors logged with context for debugging
- Application fails securely (denies access)

---

## Vulnerability Class 9: Missing Rate Limiting

**Risk**: Unlimited requests enable brute force, enumeration, and denial of service.

### Detection Patterns

```python
# VULNERABLE: No rate limiting on auth
@router.post("/auth/login")
async def login(credentials: LoginRequest):
    # Attacker can try unlimited passwords!
    return await authenticate(credentials)

# VULNERABLE: Expensive operation unprotected
@router.post("/reports/generate")
async def generate_report(request: ReportRequest):
    # Resource exhaustion possible
    return await generate_large_report(request)

# SAFE: Rate limiting with slowapi
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@router.post("/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, credentials: LoginRequest):
    return await authenticate(credentials)
```

### Key Checks

- Rate limiting on authentication endpoints
- Rate limiting on expensive operations
- Per-user and per-IP limits where appropriate
- Returns 429 with Retry-After header

---

## Vulnerability Class 10: Insecure Storage

**Risk**: Sensitive data in browser storage is accessible to XSS attacks.

### Detection Patterns

```typescript
// VULNERABLE: Token in localStorage
localStorage.setItem('authToken', token);

// VULNERABLE: PHI in sessionStorage
sessionStorage.setItem('currentPatient', JSON.stringify(patient));

// VULNERABLE: Sensitive data in URL
navigate(`/patients?ssn=${patient.ssn}`);

// SAFE: httpOnly cookies for tokens (handled by Clerk)
// Clerk manages session tokens securely

// SAFE: Keep PHI server-side
const { data: patient } = useQuery({
  queryKey: ['patient', patientId],
  queryFn: () => api.getPatient(patientId),
});
```

### Key Checks

- No tokens in localStorage/sessionStorage
- No PHI in client-side storage
- Sensitive data not in URL parameters
- Clerk handles session tokens securely

---

## Quick Reference: Search Patterns

| Vulnerability | Search Command |
|---------------|----------------|
| Hardcoded secrets | `grep -rn "sk_\|pk_\|password.*=" app/` |
| PHI in logs | `grep -rn "patient\|diagnosis" app/ \| grep -i "log\|info\|error"` |
| Missing auth | `grep -rn "@router\." app/routes \| grep -v "Depends"` |
| SQL injection | `grep -rn "f\"SELECT\|format.*SELECT" app/` |
| XSS | `grep -rn "dangerouslySetInnerHTML\|innerHTML" web/` |
| Bare except | `grep -rn "except:" app/ \| grep -v "except.*:"` |
| localStorage | `grep -rn "localStorage\|sessionStorage" web/` |
| Open redirect | `grep -rn "RedirectResponse\|redirect\|window.location" app/ web/` |
| Mass assignment | `grep -rn "\*\*data\|\*\*kwargs\|\.dict()" app/` |
| Path traversal | `grep -rn "FileResponse\|open(\|Path(" app/ \| grep -v test` |
| ReDoS | `grep -rn "re\.compile\|re\.match\|re\.search" app/` |
| Timing attack | `grep -rn "==.*password\|==.*secret\|==.*key" app/` |
| Webhook no-verify | `grep -rn "@router.*webhook" app/ -A 10 \| grep -v "svix\|verify"` |
| CSRF (GET state change) | `grep -rn "@router\.get.*delete\|@router\.get.*update" app/` |

---

## Vulnerability Class 11: Cross-Site Request Forgery (CSRF)

**Risk**: CSRF allows attackers to perform actions on behalf of authenticated users without their consent.

### Detection Patterns

```python
# VULNERABLE: State-changing endpoint without CSRF protection
@router.post("/patients/{patient_id}/delete")
async def delete_patient(
    patient_id: str,
    auth: AuthenticatedUser = Depends(AuthenticateClerkRequest),
):
    # Attacker can trick user into clicking link that deletes patient!
    await PatientService.delete(patient_id)

# VULNERABLE: Using GET for state changes
@router.get("/patients/{patient_id}/archive")
async def archive_patient(patient_id: str):
    await PatientService.archive(patient_id)  # GET should be idempotent!

# SAFE: Clerk session cookies have SameSite protection
# Verify Clerk is configured with SameSite=Lax or Strict

# SAFE: For sensitive actions, require re-authentication or confirmation
@router.post("/patients/{patient_id}/delete")
async def delete_patient(
    patient_id: str,
    confirmation: DeleteConfirmation,  # Requires user to confirm
    auth: AuthenticatedUser = Depends(AuthenticateClerkRequest),
):
    if confirmation.patient_name != patient.name:
        raise HTTPException(400, "Confirmation failed")
    await PatientService.delete(patient_id)
```

### Key Checks

- State-changing operations use POST/PUT/DELETE, not GET
- SameSite cookie attribute is set (Clerk handles this)
- Sensitive actions require explicit confirmation
- No predictable action URLs without tokens

---

## Vulnerability Class 12: Open Redirect

**Risk**: Open redirects allow attackers to redirect users to malicious sites after authentication, enabling phishing attacks.

### Detection Patterns

```python
# VULNERABLE: Unvalidated redirect
@router.get("/auth/callback")
async def auth_callback(redirect_url: str):
    # Attacker: /auth/callback?redirect_url=https://evil.com
    return RedirectResponse(redirect_url)

# VULNERABLE: Partial validation (bypassable)
@router.get("/auth/callback")
async def auth_callback(redirect_url: str):
    if "cosmaneura.com" in redirect_url:  # evil.com?cosmaneura.com bypasses!
        return RedirectResponse(redirect_url)

# SAFE: Allowlist of permitted redirect domains
ALLOWED_REDIRECT_HOSTS = {"app.cosmaneura.com", "cosmaneura.com"}

@router.get("/auth/callback")
async def auth_callback(redirect_url: str):
    parsed = urlparse(redirect_url)
    if parsed.netloc not in ALLOWED_REDIRECT_HOSTS:
        raise HTTPException(400, "Invalid redirect URL")
    return RedirectResponse(redirect_url)

# SAFE: Only allow relative paths
@router.get("/auth/callback")
async def auth_callback(next_path: str = "/"):
    if next_path.startswith("/") and not next_path.startswith("//"):
        return RedirectResponse(next_path)
    return RedirectResponse("/")
```

```typescript
// VULNERABLE: Unvalidated redirect in frontend
const redirectUrl = searchParams.get('redirect');
window.location.href = redirectUrl;  // XSS via javascript: or redirect to evil.com

// SAFE: Validate and sanitize
const redirectUrl = searchParams.get('redirect');
if (redirectUrl?.startsWith('/') && !redirectUrl.startsWith('//')) {
  navigate(redirectUrl);
} else {
  navigate('/dashboard');
}
```

### Key Checks

- Search for `RedirectResponse`, `redirect`, `window.location`
- Verify redirect targets are validated against allowlist
- Check for partial string matching (easily bypassed)
- Verify relative paths don't start with `//` (protocol-relative)

---

## Vulnerability Class 13: Mass Assignment

**Risk**: Mass assignment allows attackers to modify fields they shouldn't have access to by including extra parameters in requests.

### Detection Patterns

```python
# VULNERABLE: Accepting all fields from request
class PatientUpdate(BaseModel):
    name: str
    email: str
    is_admin: bool  # Attacker can set this!
    provider_id: str  # Attacker can reassign to different provider!

@router.patch("/patients/{patient_id}")
async def update_patient(patient_id: str, update: PatientUpdate):
    patient = await Patient.get(patient_id)
    for field, value in update.dict().items():
        setattr(patient, field, value)  # Sets ALL fields including is_admin!
    await patient.save()

# VULNERABLE: Using **kwargs or dict unpacking
@router.post("/patients")
async def create_patient(request: Request):
    data = await request.json()
    patient = Patient(**data)  # Attacker controls all fields!

# SAFE: Explicit field allowlist
class PatientUpdateAllowed(BaseModel):
    name: str | None = None
    email: str | None = None
    # is_admin and provider_id NOT included

@router.patch("/patients/{patient_id}")
async def update_patient(patient_id: str, update: PatientUpdateAllowed):
    patient = await Patient.get(patient_id)
    update_data = update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(patient, field, value)
    await patient.save()

# SAFE: Separate models for create vs update vs admin
class PatientCreate(BaseModel):  # Public creation
    name: str
    email: str

class PatientAdminUpdate(BaseModel):  # Admin only
    is_admin: bool
    provider_id: str
```

### Key Checks

- Pydantic models don't include sensitive fields (is_admin, role, provider_id)
- No `**kwargs` or `**data` when creating database objects
- Separate models for different permission levels
- `exclude_unset=True` used when updating partial data

---

## Vulnerability Class 14: Path Traversal

**Risk**: Path traversal allows attackers to access files outside intended directories, potentially reading sensitive configuration or source code.

### Detection Patterns

```python
# VULNERABLE: User input in file path
@router.get("/files/{filename}")
async def get_file(filename: str):
    # Attacker: /files/../../../etc/passwd
    path = f"/uploads/{filename}"
    return FileResponse(path)

# VULNERABLE: Insufficient sanitization
@router.get("/files/{filename}")
async def get_file(filename: str):
    clean = filename.replace("../", "")  # ....// becomes ../
    path = f"/uploads/{clean}"
    return FileResponse(path)

# SAFE: Use pathlib and verify containment
from pathlib import Path

UPLOAD_DIR = Path("/uploads").resolve()

@router.get("/files/{filename}")
async def get_file(filename: str):
    # Resolve the full path and verify it's within UPLOAD_DIR
    requested = (UPLOAD_DIR / filename).resolve()
    if not requested.is_relative_to(UPLOAD_DIR):
        raise HTTPException(403, "Access denied")
    if not requested.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(requested)

# SAFE: Use database IDs instead of filenames
@router.get("/files/{file_id}")
async def get_file(file_id: str):
    file_record = await FileService.get(file_id)  # Lookup by ID
    return FileResponse(file_record.storage_path)
```

### Key Checks

- No user input directly in file paths
- Use `pathlib.Path.resolve()` and `is_relative_to()` for validation
- Prefer database IDs over user-provided filenames
- Check for `../` bypass attempts (`....//`, URL encoding `%2e%2e%2f`)

---

## Vulnerability Class 15: Regular Expression Denial of Service (ReDoS)

**Risk**: Malicious input can cause catastrophic regex backtracking, freezing the server.

### Detection Patterns

```python
# VULNERABLE: Nested quantifiers
import re
pattern = re.compile(r"^(a+)+$")  # Catastrophic backtracking!
pattern.match("aaaaaaaaaaaaaaaaaaaaaaaaaaaa!")  # Hangs

# VULNERABLE: Overlapping alternatives with quantifiers
pattern = re.compile(r"^(.*a.*)*$")  # Evil regex

# VULNERABLE: User-controlled regex
@router.get("/search")
async def search(pattern: str):
    regex = re.compile(pattern)  # Attacker provides ReDoS pattern
    results = [p for p in patients if regex.match(p.name)]

# SAFE: Use atomic groups or possessive quantifiers (not in Python re)
# SAFE: Set timeout on regex operations
import re
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Regex timeout")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(1)  # 1 second timeout
try:
    result = pattern.match(input_string)
finally:
    signal.alarm(0)

# SAFE: Use re2 library (linear time guarantee)
import re2
pattern = re2.compile(r"^(a+)+$")  # Safe with re2

# SAFE: Don't allow user-controlled regex
@router.get("/search")
async def search(query: str):
    # Use simple string matching or escape user input
    escaped = re.escape(query)
    regex = re.compile(escaped)
```

### Key Checks

- No nested quantifiers: `(a+)+`, `(a*)*`, `(a?)*`
- No overlapping alternatives: `(a|a)*`, `(.*a.*)*`
- User input is escaped with `re.escape()` before regex
- Consider using `re2` library for untrusted patterns
- Set timeouts on regex operations

---

## Vulnerability Class 16: Timing Attacks

**Risk**: Timing differences in authentication can leak information about valid usernames or allow credential enumeration.

### Detection Patterns

```python
# VULNERABLE: Early return on invalid user
async def authenticate(username: str, password: str):
    user = await User.get_by_username(username)
    if not user:
        return None  # Fast return - attacker knows user doesn't exist
    if not verify_password(password, user.password_hash):
        return None  # Slow return - attacker knows user exists
    return user

# VULNERABLE: String comparison timing leak
def verify_api_key(provided: str, actual: str) -> bool:
    return provided == actual  # Short-circuits on first difference

# SAFE: Constant-time comparison
import hmac

def verify_api_key(provided: str, actual: str) -> bool:
    return hmac.compare_digest(provided.encode(), actual.encode())

# SAFE: Consistent timing for auth
import hmac
from passlib.hash import bcrypt

async def authenticate(username: str, password: str):
    user = await User.get_by_username(username)

    if user:
        password_valid = bcrypt.verify(password, user.password_hash)
    else:
        # Perform dummy hash to maintain consistent timing
        bcrypt.verify(password, "$2b$12$dummy.hash.for.timing")
        password_valid = False

    if not password_valid:
        return None
    return user
```

### Key Checks

- Use `hmac.compare_digest()` for secret comparisons
- Authentication takes consistent time regardless of validity
- Don't reveal whether username or password was wrong
- API key validation uses constant-time comparison

---

## Vulnerability Class 17: Webhook Signature Validation

**Risk**: Unvalidated webhooks allow attackers to forge events, potentially creating fake users or triggering unauthorized actions.

### Detection Patterns

```python
# VULNERABLE: No signature validation
@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request):
    payload = await request.json()
    # Attacker can forge any webhook event!
    if payload["type"] == "user.created":
        await create_user(payload["data"])

# VULNERABLE: Signature check after processing
@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request):
    payload = await request.json()
    await process_event(payload)  # Processed before validation!

    # Too late - already processed
    if not verify_signature(request):
        return {"error": "Invalid signature"}

# SAFE: Validate Clerk webhook with Svix
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
        raise HTTPException(400, "Invalid webhook signature")

    # Now safe to process
    if event["type"] == "user.created":
        await create_user(event["data"])
```

### Key Checks

- All webhook endpoints validate signatures BEFORE processing
- Clerk webhooks use Svix signature verification
- Webhook secrets are stored in environment variables
- Failed signature validation returns 400, not 200
- Replay attacks prevented (timestamp validation)
