"""Provider-owned template edits; never rewrite saved notes or examples."""

import hashlib
import json
from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field, model_validator

from app.services.note_template_preferences import (
    TemplatePreference,
    preference_row,
    preference_snapshot,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_template_preference import NoteTemplatePreference
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

note_template_fields_api = APIRouter()


class TemplateField(BaseModel, extra="forbid"):
    heading: str
    guidance: str


class TemplateFields(BaseModel, extra="forbid"):
    revision: str
    fields: list[TemplateField]


class TemplateSections(BaseModel, extra="forbid"):
    fields: list[TemplateField] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_headings(self) -> "TemplateSections":
        for field in self.fields:
            if (
                not field.heading.strip()
                or field.heading != field.heading.strip()
                or len(field.heading) > 200
                or len(field.guidance) > 4000
            ):
                raise ValueError(
                    "Use nonblank headings up to 200 characters and guidance up to 4000"
                )
        if len({field.heading.casefold() for field in self.fields}) != len(self.fields):
            raise ValueError("Each heading must be unique")
        if sum(len(f.heading) + len(f.guidance) for f in self.fields) > 100_000:
            raise ValueError("Template is too long")
        return self


class UpdateTemplateFields(TemplateSections):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class CreateNoteTemplate(TemplateSections):
    note_type: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def named_template(self) -> "CreateNoteTemplate":
        if not self.note_type.strip() or self.note_type != self.note_type.strip():
            raise ValueError("Use a nonblank template name without outer spaces")
        return self


class CreatedNoteTemplate(TemplateFields):
    schema_id: str
    note_type: str


class UpdateTemplatePreference(BaseModel, extra="forbid"):
    expected_revision: int = Field(ge=0)
    default_schema_ref: str | None = Field(
        pattern=r"^doctor_note_schema_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )


@note_template_fields_api.get(
    "/note-template-preference", name="get_note_template_preference"
)
def get_note_template_preference(request: Request) -> TemplatePreference:
    with get_session() as session:
        return preference_snapshot(session, request.state.doctor.id)


@note_template_fields_api.put(
    "/note-template-preference", name="update_note_template_preference"
)
def update_note_template_preference(
    request: Request, data: UpdateTemplatePreference
) -> TemplatePreference:
    with get_session() as session:
        doctor_id = request.state.doctor.id
        owner = session.exec(
            select(Doctor).where(Doctor.id == doctor_id).with_for_update()
        ).one_or_none()
        if owner is None:
            raise HTTPException(404)
        selected = None
        if data.default_schema_ref is not None:
            selected = session.exec(
                select(DoctorNoteSchema).where(
                    DoctorNoteSchema.id == data.default_schema_ref,
                    DoctorNoteSchema.doctor_id == doctor_id,
                    cast(Any, DoctorNoteSchema.deleted_at).is_(None),
                )
            ).one_or_none()
            if selected is None:
                raise HTTPException(404)
        row = preference_row(session, doctor_id)
        current = str(row.default_schema_id) if row and row.default_schema_id else None
        if current == data.default_schema_ref:
            return preference_snapshot(
                session, doctor_id
            )  # Identical lost-response retry.
        revision = row.revision if row else 0
        if revision != data.expected_revision:
            raise HTTPException(409, "Default changed. Reload before saving.")
        if row is None:
            row = NoteTemplatePreference(doctor_id=doctor_id, default_schema_id=None)
        row.default_schema_id = selected.id if selected is not None else None
        row.revision = revision + 1
        session.add(row)
        session.commit()
        return preference_snapshot(session, doctor_id)


def _snapshot(schema: DoctorNoteSchema) -> TemplateFields:
    # An array preserves order, including numeric headings, across JS and Python.
    pairs = list(schema.fields.items())
    revision = hashlib.sha256(
        json.dumps(pairs, ensure_ascii=False).encode()
    ).hexdigest()
    return TemplateFields(
        revision=revision,
        fields=[TemplateField(heading=k, guidance=v) for k, v in pairs],
    )


@note_template_fields_api.get(
    "/note-schemas/{schema_id}/fields", name="get_note_template_fields"
)
def get_note_template_fields(
    request: Request, schema_id: Annotated[TypeIDType, Path()]
) -> TemplateFields:
    with get_session() as session:
        schema = session.exec(
            select(DoctorNoteSchema).where(
                DoctorNoteSchema.id == schema_id,
                DoctorNoteSchema.doctor_id == request.state.doctor.id,
                cast(Any, DoctorNoteSchema.deleted_at).is_(None),
            )
        ).one_or_none()
        if schema is None:
            raise HTTPException(404)
        return _snapshot(schema)


@note_template_fields_api.put(
    "/note-schemas/{schema_id}/fields", name="update_note_template_fields"
)
def update_note_template_fields(
    request: Request,
    schema_id: Annotated[TypeIDType, Path()],
    data: UpdateTemplateFields,
) -> TemplateFields:
    with get_session() as session:
        schema = session.exec(
            select(DoctorNoteSchema)
            .where(
                DoctorNoteSchema.id == schema_id,
                DoctorNoteSchema.doctor_id == request.state.doctor.id,
                cast(Any, DoctorNoteSchema.deleted_at).is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if schema is None:
            raise HTTPException(404)
        current = _snapshot(schema)
        if current.fields == data.fields:
            return current  # A lost successful response can be retried safely.
        if current.revision != data.expected_revision:
            raise HTTPException(409, "Template changed. Reload before saving.")
        schema.fields = {field.heading: field.guidance for field in data.fields}
        session.add(schema)
        result = _snapshot(schema)
        session.commit()
        return result


@note_template_fields_api.post("/note-schemas", name="create_note_template")
def create_note_template(
    request: Request, data: CreateNoteTemplate
) -> CreatedNoteTemplate:
    """A name is unique for this owner; identical retries never create duplicates."""
    with get_session() as session:
        owner = session.exec(
            select(Doctor).where(Doctor.id == request.state.doctor.id).with_for_update()
        ).one_or_none()
        if owner is None:
            raise HTTPException(404)

        def existing() -> DoctorNoteSchema | None:
            return session.exec(
                select(DoctorNoteSchema).where(
                    DoctorNoteSchema.doctor_id == owner.id,
                    DoctorNoteSchema.note_type == data.note_type,
                )
            ).one_or_none()

        def receipt(schema: DoctorNoteSchema) -> CreatedNoteTemplate:
            current = _snapshot(schema)
            if schema.deleted_at is not None or current.fields != data.fields:
                raise HTTPException(
                    409, "That template name already exists. Choose another name."
                )
            return CreatedNoteTemplate(
                schema_id=str(schema.id),
                note_type=schema.note_type,
                **current.model_dump(),
            )

        prior = existing()
        if prior is not None:
            return receipt(prior)
        schema = DoctorNoteSchema(
            doctor_id=owner.id,
            note_type=data.note_type,
            fields={field.heading: field.guidance for field in data.fields},
        )
        session.add(schema)
        try:
            session.commit()
        except IntegrityError:
            # The existing PDF importer uses the same unique name constraint.
            session.rollback()
            prior = existing()
            if prior is None:
                raise
            return receipt(prior)
        return receipt(schema)
