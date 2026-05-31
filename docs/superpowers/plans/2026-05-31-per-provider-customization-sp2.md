# SP2 — Per-Note-Type Customization API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose the per-note-type `NoteTypeCustomization` (SP1) over the internal API: read the effective config inlined in `GET /internal/v1/settings`, and write it via `PUT /internal/v1/note-schemas/{schema_id}/customization`.

**Architecture:** `SettingsData` gains a parallel `note_customizations: list[NoteTypeCustomizationData]` (one entry per active schema, defaults when no row), batch-loaded to avoid N+1. A new `PUT` endpoint upserts the row scoped to the authenticated doctor (404 for others). No change to the existing `notes_schema` element type or any other endpoint.

**Tech Stack:** FastAPI, SQLModel/activemodel, pydantic, pytest, uv. CIs gate on `just py_lint` (ruff check + `ruff format --check` + pyright) and the openapi snapshot test.

**Spec:** `docs/superpowers/specs/2026-05-30-per-provider-customization-sp2-api-design.md` (decision: inline in `/settings`).

**Branch:** create `feat/note-customization-api-sp2` off `origin/master`.

---

## File Structure

- **Modify** `app/routes/internal.py`: add `NoteTypeCustomizationData` + `UpdateNoteTypeCustomization` pydantic DTOs, a `_customization_data()` helper, the `note_customizations` field on `SettingsData` (populated in `settings_data`), and the new `PUT` endpoint.
- **Create** `tests/routes/note_customization_test.py`.
- **Regenerate** `web/openapi.json` via `just js_generate-openapi`.

All pytest runs use `PYTHON_ENV=test`. Before pushing, run `uv tool run ruff format --check .` and `uv tool run ruff check .` and `uv run pyright .` (CI gates on all three).

---

## Task 1: GET — inline effective customization in `/settings`

**Files:**
- Modify: `app/routes/internal.py` (`SettingsData` at `254`, `settings_data` at `268`)
- Test: `tests/routes/note_customization_test.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/routes/note_customization_test.py
from fastapi.testclient import TestClient

from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.server import api_app

from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def test_settings_includes_default_customization_when_no_row(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 SOAP", fields={"CC": "x"}
    ).save()

    resp = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert resp.status_code == 200
    body = resp.json()

    entry = next(
        c
        for c in body["note_customizations"]
        if c["doctor_note_schema_id"] == str(schema.id)
    )
    assert entry["subjective_source"] == "patient_only"
    assert entry["require_plan_reason"] is True
    assert entry["custom_instructions"] is None
```

(`settings_data` is the existing endpoint function name; `url_path_for` resolves it.)

- [ ] **Step 2: Run to confirm failure**

Run: `PYTHON_ENV=test uv run pytest tests/routes/note_customization_test.py -v`
Expected: FAIL — `KeyError: 'note_customizations'` (field doesn't exist yet). (If it errors instead with a Clerk skip, ensure `CLERK_PRIVATE_KEY` is set — see `docs/2026-05-30-session-handoff.md`; CI has it.)

- [ ] **Step 3: Implement the DTO, helper, and the inline field**

Add imports near the other model imports in `app/routes/internal.py`:
```python
from app.models.note_type_customization import (
    NoteTypeCustomization,
    PlanOrdering,
    PronounStyle,
    SubjectiveSource,
)
```

Add the DTO + helper (near `SettingsData`, before the endpoint):
```python
class NoteTypeCustomizationData(BaseModel, extra="forbid"):
    doctor_note_schema_id: str
    subjective_source: SubjectiveSource
    pronoun_style: PronounStyle
    plan_ordering: PlanOrdering
    flag_new_medications: bool
    require_plan_reason: bool
    ros_distinct_from_subjective: bool
    custom_instructions: str | None


def _customization_data(
    schema_id, row: "NoteTypeCustomization | None"
) -> NoteTypeCustomizationData:
    # None -> a transient defaults record (== current behavior)
    c = row or NoteTypeCustomization()
    return NoteTypeCustomizationData(
        doctor_note_schema_id=str(schema_id),
        subjective_source=c.subjective_source,
        pronoun_style=c.pronoun_style,
        plan_ordering=c.plan_ordering,
        flag_new_medications=c.flag_new_medications,
        require_plan_reason=c.require_plan_reason,
        ros_distinct_from_subjective=c.ros_distinct_from_subjective,
        custom_instructions=c.custom_instructions,
    )
```

Add the field to `SettingsData`:
```python
    note_customizations: list[NoteTypeCustomizationData]
```

Populate it in `settings_data`, after `notes_schema` is fetched (batch-load, no N+1):
```python
    schema_ids = [s.id for s in notes_schema]
    customization_rows = (
        NoteTypeCustomization.where(
            cast(Any, NoteTypeCustomization.doctor_note_schema_id).in_(schema_ids)
        ).all()
        if schema_ids
        else []
    )
    by_schema = {
        r.doctor_note_schema_id: r
        for r in cast(list[NoteTypeCustomization], customization_rows)
    }
    note_customizations = [
        _customization_data(s.id, by_schema.get(s.id)) for s in notes_schema
    ]
```
and pass `note_customizations=note_customizations` into the `SettingsData(...)` return.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `PYTHON_ENV=test uv run pytest tests/routes/note_customization_test.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/internal.py tests/routes/note_customization_test.py
git commit -m "feat(api): inline effective note customization in GET /settings"
```

---

## Task 2: PUT — upsert a note-type's customization

**Files:**
- Modify: `app/routes/internal.py`
- Test: `tests/routes/note_customization_test.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/routes/note_customization_test.py
from fastapi import status

from app.models.note_type_customization import NoteTypeCustomization

from tests.factories import DoctorFactory

_FULL_BODY = {
    "subjective_source": "patient_and_doctor",
    "pronoun_style": "neutral",
    "plan_ordering": "as_discussed",
    "flag_new_medications": False,
    "require_plan_reason": False,
    "ros_distinct_from_subjective": False,
    "custom_instructions": "Use metric units.",
}


def test_put_creates_then_updates_in_place(authenticated_client: TestClient):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 PUT", fields={"CC": "x"}
    ).save()
    url = api_app.url_path_for(
        "update_note_customization", schema_id=str(schema.id)
    )

    resp = authenticated_client.put(url, json=_FULL_BODY)
    assert resp.status_code == 200
    assert resp.json()["require_plan_reason"] is False
    assert resp.json()["doctor_note_schema_id"] == str(schema.id)

    # second PUT updates the same row (no duplicate; exercises the 1:1 constraint)
    resp2 = authenticated_client.put(url, json={**_FULL_BODY, "require_plan_reason": True})
    assert resp2.status_code == 200
    assert resp2.json()["require_plan_reason"] is True
    assert (
        NoteTypeCustomization.where(
            NoteTypeCustomization.doctor_note_schema_id == schema.id
        ).count()
        == 1
    )


def test_put_other_doctors_schema_returns_404(authenticated_client: TestClient):
    other = DoctorFactory.build().save()
    schema = DoctorNoteSchema(
        doctor_id=other.id, note_type="SP2 OTHER", fields={"CC": "x"}
    ).save()
    url = api_app.url_path_for(
        "update_note_customization", schema_id=str(schema.id)
    )
    resp = authenticated_client.put(url, json=_FULL_BODY)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_put_rejects_overlong_custom_instructions(authenticated_client: TestClient):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 LONG", fields={"CC": "x"}
    ).save()
    url = api_app.url_path_for(
        "update_note_customization", schema_id=str(schema.id)
    )
    resp = authenticated_client.put(
        url, json={**_FULL_BODY, "custom_instructions": "x" * 4001}
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
```

- [ ] **Step 2: Run to confirm failure**

Run: `PYTHON_ENV=test uv run pytest tests/routes/note_customization_test.py -v`
Expected: the three new tests FAIL — no route named `update_note_customization`.

- [ ] **Step 3: Implement the request model + endpoint**

Ensure `Field` is imported from pydantic in `app/routes/internal.py` (it currently imports `BaseModel, computed_field`):
```python
from pydantic import BaseModel, Field, computed_field
```

Add the request model and endpoint (near the GET settings code):
```python
class UpdateNoteTypeCustomization(BaseModel, extra="forbid"):
    subjective_source: SubjectiveSource = SubjectiveSource.patient_only
    pronoun_style: PronounStyle = PronounStyle.infer_from_transcript
    plan_ordering: PlanOrdering = PlanOrdering.patient_then_doctor
    flag_new_medications: bool = True
    require_plan_reason: bool = True
    ros_distinct_from_subjective: bool = True
    custom_instructions: str | None = Field(default=None, max_length=4000)


@internal_api_app.put(
    "/note-schemas/{schema_id}/customization", name="update_note_customization"
)
def update_note_customization(
    request: Request,
    schema_id: Annotated[str, Path()],
    data: UpdateNoteTypeCustomization,
) -> NoteTypeCustomizationData:
    doctor: Doctor = request.state.doctor

    schema = DoctorNoteSchema.get(id=schema_id)
    if (
        schema is None
        or schema.doctor_id != doctor.id
        or schema.deleted_at is not None
    ):
        # 404 (not 403) so we don't leak existence of other doctors' schemas
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    row = NoteTypeCustomization.find_or_initialize_by(
        doctor_note_schema_id=schema.id
    )
    row.subjective_source = data.subjective_source
    row.pronoun_style = data.pronoun_style
    row.plan_ordering = data.plan_ordering
    row.flag_new_medications = data.flag_new_medications
    row.require_plan_reason = data.require_plan_reason
    row.ros_distinct_from_subjective = data.ros_distinct_from_subjective
    row.custom_instructions = data.custom_instructions
    row.save()

    return _customization_data(schema.id, row)
```

If `DoctorNoteSchema.get(id=schema_id)` rejects a raw string TypeID, match how `/notes/{appointment_id}` (internal.py:142) resolves its path id and apply the same coercion. If `find_or_initialize_by` isn't available, use `NoteTypeCustomization.get(doctor_note_schema_id=schema.id) or NoteTypeCustomization(doctor_note_schema_id=schema.id)`.

- [ ] **Step 4: Run the tests**

Run: `PYTHON_ENV=test uv run pytest tests/routes/note_customization_test.py -v`
Expected: all (4) pass.

- [ ] **Step 5: Commit**

```bash
git add app/routes/internal.py tests/routes/note_customization_test.py
git commit -m "feat(api): PUT /note-schemas/{id}/customization upsert (doctor-scoped)"
```

---

## Task 3: Regenerate OpenAPI + verify snapshot

**Files:**
- Modify: `web/openapi.json` (generated)

- [ ] **Step 1: Confirm the snapshot test currently fails (schema drifted)**

Run: `PYTHON_ENV=test uv run pytest tests/unit/openapi_schema_test.py::test_openapi_schema_matches_generated_file -v`
Expected: FAIL — the new field/route changed the emitted schema vs the committed `web/openapi.json`.

- [ ] **Step 2: Regenerate**

Run: `just js_generate-openapi`
Expected: `web/openapi.json` updated to include `note_customizations` in the settings response and the new `PUT /internal/v1/note-schemas/{schema_id}/customization` path.

- [ ] **Step 3: Confirm the snapshot test passes**

Run: `PYTHON_ENV=test uv run pytest tests/unit/openapi_schema_test.py::test_openapi_schema_matches_generated_file -v`
Expected: PASS.

- [ ] **Step 4: Full lint + targeted test gate (CI parity)**

Run:
```bash
uv tool run ruff check .
uv tool run ruff format --check .
uv run pyright .
PYTHON_ENV=test uv run pytest tests/routes/note_customization_test.py tests/unit/openapi_schema_test.py -v
```
Expected: ruff clean, `ruff format --check` reports no files to reformat, pyright 0 errors, tests pass. Fix any findings (these are the exact gates CI enforces — see `ci-lint-gate`).

- [ ] **Step 5: Commit**

```bash
git add web/openapi.json
git commit -m "chore(openapi): regenerate for note customization endpoints"
```

---

## Done criteria

- `GET /internal/v1/settings` returns `note_customizations` — one entry per active schema, defaults when no row, actual values when a row exists, no N+1.
- `PUT /internal/v1/note-schemas/{schema_id}/customization` upserts (one row per schema), 404 for a schema the caller doesn't own, 422 for `custom_instructions` > 4000 chars.
- `web/openapi.json` regenerated; snapshot test green.
- ruff check + `ruff format --check` + pyright all clean; route tests green.

## Notes / gotchas

- Route tests need real Clerk auth (`authenticated_client` + `CLERK_PRIVATE_KEY`); they skip locally without it but run in CI.
- Run the three CI lint commands locally before pushing — pyright/format findings fail the build (see the `ci-lint-gate` memory). In particular: `_customization_data`'s `row` param annotation and the `cast(Any, ...).in_(...)` may need a `cast` to satisfy pyright; model the existing `.where((cond) & cast(Any, ...).is_(None))` style at internal.py:277.
- This is additive: existing `notes_schema` and all other endpoints are unchanged.
