---
name: code-quality
description: "Enforce minimal code philosophy with measurable constraints. Triggers: quality, refactor, clean, minimal, bloat, simplify, YAGNI"
---

# Code Quality

Enforce minimal code philosophy for Python/TypeScript development in a healthcare application.

## When to Activate

- Writing new code
- Reviewing code changes
- Refactoring existing code
- Responding to "improve" or "optimize" requests

---

## Global Invariants

| Rule | Constraint | Status |
|------|------------|--------|
| Least code wins | Fewer lines = better | MANDATORY |
| Function length | ≤60 lines | MANDATORY |
| File length | 300-450 soft, 600 hard limit | MANDATORY |
| Function parameters | ≤4 (use options object/dataclass) | MANDATORY |
| Cyclomatic complexity | ≤15 per function | MANDATORY |
| Build only what's requested | No speculative features | MANDATORY |
| Delete unused code | Don't comment out | MANDATORY |

---

## Numeric Constraints

| Metric | Soft Limit | Hard Limit | Action |
|--------|------------|------------|--------|
| Function lines | 40 | 60 | Extract helper |
| File lines | 300 | 600 | Split file |
| Parameters | 3 | 4 | Use dataclass/TypedDict |
| Nesting depth | 3 | 4 | Extract or early return |
| Cyclomatic complexity | 10 | 15 | Simplify logic |

---

## Python Patterns

### Parameter Handling

```python
# ✅ CORRECT: Dataclass for 4+ params
@dataclass
class CreatePatientRequest:
    name: str
    email: str
    date_of_birth: date
    provider_id: str | None = None

async def create_patient(request: CreatePatientRequest) -> Patient:
    ...

# ❌ FAIL: Too many positional params
async def create_patient(name: str, email: str, dob: date, provider_id: str) -> Patient:
    ...
```

### Error Handling

```python
# ✅ CORRECT: Specific exception handling
try:
    patient = await get_patient(patient_id)
except PatientNotFoundError:
    raise HTTPException(status_code=404, detail="Patient not found")

# ❌ FAIL: Bare except
try:
    patient = await get_patient(patient_id)
except:  # Catches everything including KeyboardInterrupt!
    return None
```

### Type Hints

```python
# ✅ CORRECT: Full type hints
async def get_patients(
    provider_id: str,
    limit: int = 10,
) -> list[Patient]:
    ...

# ❌ FAIL: Missing type hints
async def get_patients(provider_id, limit=10):
    ...
```

---

## TypeScript Patterns

### Component Structure

```typescript
// ✅ CORRECT: Small, focused component
function PatientCard({ patient }: { patient: Patient }) {
  return (
    <div className="p-4 border rounded">
      <h3>{patient.name}</h3>
      <p>{patient.email}</p>
    </div>
  );
}

// ❌ FAIL: Over-engineered for simple case
interface PatientCardProps {
  patient: Patient;
  variant?: 'default' | 'compact' | 'expanded';
  showActions?: boolean;
  onEdit?: () => void;
  onDelete?: () => void;
  className?: string;
  // ... 10 more props nobody asked for
}
```

### Type Safety

```typescript
// ✅ CORRECT: Proper typing
const patient = await api.getPatient(id);
if (!patient) {
  throw new Error('Patient not found');
}
// patient is now Patient, not Patient | null

// ❌ FAIL: Type assertion
const patient = await api.getPatient(id) as Patient;  // Might be null!
```

---

## Abstraction Decision Framework

Before extracting a function/class:

| Question | If NO | If YES |
|----------|-------|--------|
| Used 3+ times? | Don't extract | Consider extraction |
| Reduces complexity? | Don't extract | Extract |
| Has clear name? | Don't extract | Extract |
| Hides implementation detail? | Keep inline | Extract |
| Will change independently? | Keep together | Separate |

**Rule of Three**: Don't abstract until third occurrence.

---

## YAGNI — You Aren't Gonna Need It

| ✅ DO | ❌ DON'T |
|-------|---------|
| Implement the requested feature | Add "configurable" options |
| Solve the specific problem | Design for hypothetical futures |
| Write minimal working code | Build "extensible" frameworks |
| Use existing patterns | Create new patterns for one use |

---

## Delete, Don't Comment

| ✅ DO | ❌ DON'T |
|-------|---------|
| `git rm unused_file.py` | `# TODO: remove later` |
| Remove unused imports | `_unused_variable` renaming |
| Delete dead code paths | `# OLD: previous impl` |

Git has history. Delete with confidence.

---

## Naming Conventions

### Python

| Type | Convention | Example |
|------|------------|---------|
| Functions | `snake_case`, verb phrase | `get_patient`, `create_appointment` |
| Classes | `PascalCase`, noun | `PatientService`, `AppointmentModel` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_PATIENTS`, `DEFAULT_TIMEOUT` |
| Private | `_leading_underscore` | `_validate_input` |

### TypeScript

| Type | Convention | Example |
|------|------------|---------|
| Functions | `camelCase`, verb phrase | `getPatient`, `createAppointment` |
| Components | `PascalCase` | `PatientCard`, `AppointmentList` |
| Types/Interfaces | `PascalCase` | `Patient`, `AppointmentProps` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_PATIENTS`, `API_URL` |

---

## File Organization

| Threshold | Action |
|-----------|--------|
| File > 300 lines | Consider splitting |
| File > 450 lines | Plan to split |
| File > 600 lines | MUST split |
| Function > 60 lines | MUST extract |

**Split Criteria:**
- Separate by feature/domain
- Separate by layer (routes, services, models)
- Separate by change frequency

---

## Anti-Patterns

| Pattern | Status | Instead |
|---------|--------|---------|
| Speculative features | FORBIDDEN | Build what's requested |
| Commented-out code | FORBIDDEN | Delete (git has history) |
| `_unusedVar` naming | FORBIDDEN | Remove the variable |
| Premature abstraction | FORBIDDEN | Wait for 3rd occurrence |
| Wrapper with no logic | FORBIDDEN | Call directly |
| Functions >60 lines | FORBIDDEN | Extract helper |
| Files >600 lines | FORBIDDEN | Split file |
| >4 function params | FORBIDDEN | Use dataclass/TypedDict |
| `# type: ignore` | FORBIDDEN | Fix the type issue |
| `any` type | FORBIDDEN | Use proper types |
