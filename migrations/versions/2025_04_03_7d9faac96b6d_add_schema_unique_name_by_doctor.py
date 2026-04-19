"""add_schema_unique_name_by_doctor

Revision ID: 7d9faac96b6d
Revises: 913e1e0f0401
Create Date: 2025-04-03 15:33:27.189168

This migration is more complex:

1. Find all note schemas that are duplicates on (doctor_id, note_type)
2. Pick the first-created note schema as the "master" schema
3. For all non-master schemas of the same note_type (duplicates)
  1. Find all AIVisitNotes referencing this schema, update it to use the master schema ID. Do this using a SQL Alchemy update query.
  2. Find all DoctorNoteExamples referencing this schema, update it to use the master schema. Do this using a SQL Alchemy update query.
4. Delete the duplicate schema
5. Wrap this entire operation in a database transaction
6. Assume all models are using SQLModel (based on SQLAlchemy) so write database queries using SQLAlchemy syntax
7. Add a compound key, before the transaction is committed to ensure the migration properly ran :)
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
import sqlmodel
import activemodel


# revision identifiers, used by Alembic.
revision: str = '7d9faac96b6d'
down_revision: Union[str, None] = '913e1e0f0401'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

from sqlalchemy import func, select, update, text
from sqlalchemy.orm import Session
from collections import defaultdict
import sys

from app import log
from app.models.ai_visit_note import AIVisitNote
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema

def find_duplicate_groups(sess: Session):
    """Find schemas grouped by (doctor_id, note_type) with more than one schema"""
    schemas = sess.execute(select(DoctorNoteSchema)).scalars().all()

    groups = defaultdict(list)
    for schema in schemas:
        key = (schema.doctor_id, schema.note_type)
        groups[key].append(schema)

    return {k: v for k, v in groups.items() if len(v) > 1}


def get_master_schema(schemas):
    """Pick the oldest schema as the master schema"""
    return sorted(schemas, key=lambda s: s.created_at or "")[0]


def update_references(sess: Session, duplicate_id: str, master_id: str):
    """Update all references from duplicate schema to master schema"""
    # Update AIVisitNotes
    notes_query = update(AIVisitNote).where(
        AIVisitNote.doctor_note_schema_id == duplicate_id
    ).values(doctor_note_schema_id=master_id)
    notes_count = sess.execute(notes_query).rowcount

    # Update DoctorNoteExamples
    examples_query = update(DoctorNoteExample).where(
        DoctorNoteExample.doctor_note_schema_id == duplicate_id
    ).values(doctor_note_schema_id=master_id)
    examples_count = sess.execute(examples_query).rowcount

    return notes_count, examples_count


def get_reference_counts(sess: Session, schema_id: str):
    """Get counts of references to a schema"""
    notes_count = sess.execute(
        select(func.count()).select_from(AIVisitNote).where(
            AIVisitNote.doctor_note_schema_id == schema_id
        )
    ).scalar_one()

    examples_count = sess.execute(
        select(func.count()).select_from(DoctorNoteExample).where(
            DoctorNoteExample.doctor_note_schema_id == schema_id
        )
    ).scalar_one()

    return notes_count, examples_count


def consolidate_schemas(sess: Session):
    """Consolidate duplicate schemas into master schemas"""
    # Find duplicate groups
    duplicate_groups = find_duplicate_groups(sess)

    if not duplicate_groups:
        log.info("no duplicate schemas found")
        return

    log.info("found duplicate schema groups", count=len(duplicate_groups))

    stats = {
        "schemas_processed": 0,
        "notes_updated": 0,
        "examples_updated": 0,
        "schemas_deleted": 0
    }

    for (doctor_id, note_type), schemas in duplicate_groups.items():
        # Get master schema (oldest one)
        master = get_master_schema(schemas)
        duplicates = [s for s in schemas if s.id != master.id]

        log.info("processing duplicate group",
                    doctor_id=doctor_id,
                    note_type=note_type,
                    master_id=master.id,
                    duplicate_count=len(duplicates))

        for duplicate in duplicates:
            # Get reference counts
            notes_count, examples_count = get_reference_counts(sess, duplicate.id)

            log.info("processing duplicate schema",
                        schema_id=duplicate.id,
                        notes_count=notes_count,
                        examples_count=examples_count)

            # Update references
            updated_notes, updated_examples = update_references(
                sess, duplicate.id, master.id
            )

            log.info("updated references",
                        updated_notes=updated_notes,
                        updated_examples=updated_examples)

            sess.delete(duplicate)

            stats["notes_updated"] += updated_notes
            stats["examples_updated"] += updated_examples
            stats["schemas_deleted"] += 1

            stats["schemas_processed"] += 1

    log.info(f"consolidation summary", **stats)


def upgrade() -> None:
    """
    1. I want to consolidate schema
    2. Then, I want to validate that we could add a unique constraint without failure
    3. If that's true, I want to commit the changes and add the unique constraint
    """

    if context.is_offline_mode():
        op.create_unique_constraint('doctor_and_note_type_unique', 'doctor_note_schema', ['note_type', 'doctor_id'])
        return

    session = Session(bind=op.get_bind())

    consolidate_schemas(session)

    # flush before creating a constraint, otherwise not all changes will persist to the transaction
    session.flush()

    # ### commands auto generated by Alembic - please adjust! ###
    op.create_unique_constraint('doctor_and_note_type_unique', 'doctor_note_schema', ['note_type', 'doctor_id'])
    # ### end Alembic commands ###

def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('doctor_and_note_type_unique', 'doctor_note_schema', type_='unique')
    # ### end Alembic commands ###
