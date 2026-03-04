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
