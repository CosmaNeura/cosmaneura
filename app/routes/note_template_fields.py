"""Provider-owned template edits; never rewrite saved notes or examples."""

import hashlib
import json
from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field, model_validator

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlmodel import select

note_template_fields_api = APIRouter()


class TemplateField(BaseModel, extra="forbid"):
    heading: str
    guidance: str


class TemplateFields(BaseModel, extra="forbid"):
    revision: str
    fields: list[TemplateField]


class UpdateTemplateFields(BaseModel, extra="forbid"):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    fields: list[TemplateField] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_headings(self) -> "UpdateTemplateFields":
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
        if len(data.fields) != len(current.fields):
            raise HTTPException(422, "Edit the existing headings; keep every section.")
        schema.fields = {field.heading: field.guidance for field in data.fields}
        session.add(schema)
        result = _snapshot(schema)
        session.commit()
        return result
