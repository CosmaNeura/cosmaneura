# SP2 — API: CRUD for a note-type's customization

**Date:** 2026-05-30
**Depends on:** SP1 (the `NoteTypeCustomization` model)
**See also:** `2026-05-30-per-provider-customization-overview.md`

## Goal

Expose the per-note-type customization (SP1) over the internal API so the frontend
(SP3) can read and update it. Read returns the effective configuration (defaults
when no row exists); write upserts the row scoped to the authenticated provider.

## Endpoints

Add to `app/routes/internal.py` (alongside the existing `/settings` routes,
`internal.py:254–301`).

### `GET /internal/v1/note-schemas/{schema_id}/customization`

Returns the effective customization for the given `DoctorNoteSchema`. If no row
exists, return the **defaults** (so the UI always renders concrete values).

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
```

### `PUT /internal/v1/note-schemas/{schema_id}/customization`

Upsert. Body is the full configuration (all presets + free-text). Returns the saved
`NoteTypeCustomizationData`.

```python
class UpdateNoteTypeCustomizationData(BaseModel, extra="forbid"):
    subjective_source: SubjectiveSource
    pronoun_style: PronounStyle
    plan_ordering: PlanOrdering
    flag_new_medications: bool
    require_plan_reason: bool
    ros_distinct_from_subjective: bool
    custom_instructions: str | None = None
```

Use `PUT` (full-document replace) rather than `PATCH` — the config is small and the
UI always holds the full set, so partial-update semantics add complexity for no
gain.

## Authorization

The schema must belong to the authenticated doctor. Resolve
`request.state.doctor` (the existing pattern) and verify
`schema.doctor_id == doctor.id`; otherwise `404` (not `403` — don't leak existence
of other doctors' schemas). Centralize this in a small helper:

```python
def _owned_schema_or_404(request: Request, schema_id: str) -> DoctorNoteSchema:
    doctor: Doctor = request.state.doctor
    schema = DoctorNoteSchema.get(id=schema_id)
    if schema is None or schema.doctor_id != doctor.id or schema.deleted_at:
        raise HTTPException(status_code=404)
    return schema
```

`custom_instructions` length is bounded (e.g. ≤ 4000 chars) via a Pydantic
validator to keep prompt size sane; reject longer with `422`.

## Upsert logic

```python
existing = NoteTypeCustomization.get(doctor_note_schema_id=schema.id)
record = existing or NoteTypeCustomization(doctor_note_schema_id=schema.id)
record.subjective_source = data.subjective_source
# ... assign each field ...
record.custom_instructions = data.custom_instructions
record.save()
```

(Match the codebase's actual fetch/save idiom — `find_or_initialize_by` exists and
is used in `extract.py`; prefer it if available.)

## Surfacing in `/settings` (decided: inline)

**Decision (2026-05-30):** inline each schema's effective customization in
`GET /internal/v1/settings`, which already returns `notes_schema:
list[DoctorNoteSchema]`. The settings page renders everything in one round-trip and
avoids an N+1 of one customization GET per note-type (consistent with the settings
N+1 cleanup in PR #68).

Concretely: the settings payload's per-schema item gains a
`customization: NoteTypeCustomizationData` field carrying the effective config
(defaults when no row exists). The dedicated `GET .../customization` above is then
optional — keep it only if a standalone fetch proves useful; the `PUT` remains the
write path. The UI reads customization from the settings payload and writes via
`PUT`, then invalidates the settings query.

## Testing

`tests/routes/` (mirror `admin_usage_test.py` / `notes_test.py`):

- `GET` with no row → 200 returning defaults.
- `GET`/`PUT` for a schema owned by another doctor → 404.
- `PUT` creates a row; subsequent `GET` reflects it; second `PUT` updates in place
  (no duplicate rows — exercises the 1:1 unique constraint).
- `PUT` with over-long `custom_instructions` → 422.
- Round-trip enum values serialize/deserialize correctly.

## OpenAPI

Adding routes changes the emitted schema. Run `just js_generate-openapi` so
`web/openapi.json` matches, or the snapshot test
`tests/unit/openapi_schema_test.py::test_openapi_schema_matches_generated_file`
fails in CI (this bit PR #66).
