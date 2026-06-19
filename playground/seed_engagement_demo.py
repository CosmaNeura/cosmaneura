"""Seed demo encounters with engagement roles under dev@cosmaneura.com (staging).

Idempotent: re-running does not duplicate (rows tagged created_by_system='seed-demo'
and patient names prefixed '[Demo] '). Reversible: `--delete` removes all demo rows.

Run against STAGING only, with secrets injected and DATABASE_URL overridden to the
staging connection string. PYTHON_ENV is forced to development so no migration runs.

    .venv/bin/python playground/seed_engagement_demo.py          # seed
    .venv/bin/python playground/seed_engagement_demo.py --delete # teardown
"""

import os
import sys

os.environ["PYTHON_ENV"] = "development"  # SAFETY: no auto-migration

import activemodel
from activemodel.session_manager import get_session

from app.configuration.database import database_url
from app.engagement.cosmas_engagement.version import (
    ENGINE_VERSION,
    FRAMEWORK_VERSION,
    RUBRIC_VERSION,
)
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.engagement_role import ConfidenceBand, EngagementRole
from app.models.patient import Patient

from datetime import datetime, timezone, timedelta

DEMO_TAG = "seed-demo"
DEV_EMAIL = "dev@cosmaneura.com"

# 6 encounters: 3 collaborative, 2 self_directed, 1 directed.
ENCOUNTERS = [
    ("Avery",  "collaborative", "Collaborative", "collaborative", 52, 58, 20, 0.58),
    ("Jordan", "collaborative", "Collaborative", "collaborative", 48, 50, 35, 0.51),
    ("Riley",  "collaborative", "Collaborative", "collaborative", 55, 60, 15, 0.62),
    ("Sam",    "self_directed", "Self-Directed", "active",        82, 78, 25, 0.71),
    ("Quinn",  "self_directed", "Self-Directed", "active",        76, 70, 18, 0.66),
    ("Parker", "directed",      "Directed",      "passive",       22, 30, 62, 0.49),
]

DIALOGUE = {
    "collaborative": [
        ("Patient", "I'd like to hear the options and decide together with you."),
        ("Doctor",  "Let's go through them. Here's what I'd recommend and why."),
        ("Patient", "That makes sense. What matters most to me is staying active."),
    ],
    "self_directed": [
        ("Patient", "Can you walk me through the evidence? I want to make the final call."),
        ("Doctor",  "Of course. Here are the trade-offs and the studies behind them."),
        ("Patient", "Thanks. I've researched this and I'd prefer the second option."),
    ],
    "directed": [
        ("Patient", "Whatever you think is best, doctor. Just tell me what to do."),
        ("Doctor",  "We'll start with this medication and check back in two weeks."),
        ("Patient", "Okay, I trust you. I'm a little worried but I'll follow your advice."),
    ],
}

NOTE_BODY = {
    "subjective": "Patient presents for routine follow-up. See transcript.",
    "objective": "Vitals within normal limits.",
    "assessment": "Stable. Demo encounter.",
    "plan": "Continue current plan; follow up in two weeks.",
}


def _band(conf: float) -> ConfidenceBand:
    if conf >= 0.66:
        return ConfidenceBand.high
    if conf >= 0.40:
        return ConfidenceBand.moderate
    return ConfidenceBand.low


def _distribution(role_key: str) -> dict:
    base = {"self_directed": 0.2, "collaborative": 0.2, "directed": 0.2}
    base[role_key] = 0.6
    for k in base:
        if k != role_key:
            base[k] = 0.2
    return {k: round(v, 4) for k, v in base.items()}


def get_dev_doctor() -> Doctor:
    doc = Doctor.get(email=DEV_EMAIL)
    assert doc, f"Doctor {DEV_EMAIL} not found on this DB — sign in on staging first."
    return doc


def get_or_create_note_schema(doctor: Doctor) -> DoctorNoteSchema:
    existing = DoctorNoteSchema.where(DoctorNoteSchema.doctor_id == doctor.id).first()
    if existing:
        return existing
    return DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="SOAP (demo)",
        fields={
            "subjective": "Subjective",
            "objective": "Objective",
            "assessment": "Assessment",
            "plan": "Plan",
        },
    ).save()


def delete_demo(doctor: Doctor) -> None:
    appts = list(
        Appointment.where(
            Appointment.doctor_id == doctor.id,
            Appointment.created_by_system == DEMO_TAG,
        ).all()
    )
    appt_ids = [a.id for a in appts]
    with get_session() as s:
        for aid in appt_ids:
            for er in EngagementRole.where(EngagementRole.appointment_id == aid).all():
                s.delete(er)
            for note in AIVisitNote.where(AIVisitNote.appointment_id == aid).all():
                s.delete(note)
            for tr in AIVisitTranscript.where(
                AIVisitTranscript.appointment_id == aid
            ).all():
                s.delete(tr)
        s.commit()
    n = 0
    for a in appts:
        for p in Patient.where(
            Patient.id == a.patient_id, Patient.doctor_id == doctor.id
        ).all():
            if p.name.startswith("[Demo] "):
                p.delete()
        a.delete()
        n += 1
    print(f"deleted {n} demo encounters")


def seed(doctor: Doctor) -> None:
    schema = get_or_create_note_schema(doctor)
    base_date = datetime.now(timezone.utc)
    created = 0
    for i, (first, role_key, role_name, cps, dc, ie, concern, conf) in enumerate(
        ENCOUNTERS
    ):
        patient_name = f"[Demo] {first} Demo"
        existing_patient = Patient.where(
            Patient.doctor_id == doctor.id, Patient.name == patient_name
        ).first()
        if existing_patient:
            print(f"skip {patient_name} (already seeded)")
            continue

        patient = Patient(
            doctor_id=doctor.id,
            name=patient_name,
            date_of_birth=datetime(1980 + i, 1, 1, tzinfo=timezone.utc),
        ).save()

        appointment = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_date=base_date - timedelta(days=i + 1),
            external_id=f"{DEMO_TAG}-{i}",
            created_by_system=DEMO_TAG,
        ).save()

        transcript = AIVisitTranscript(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            duration=600,
            transcript_data=[
                TranscriptEntry(speaker=spk, content=txt, startTime=0, endTime=1000)
                for spk, txt in DIALOGUE[role_key]
            ],
        ).save()

        AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note=dict(NOTE_BODY),
        ).save()

        EngagementRole(
            appointment_id=appointment.id,
            doctor_id=doctor.id,
            ai_transcript_id=transcript.id,
            role_key=role_key,
            role_name=role_name,
            cps_role=cps,
            confidence=conf,
            confidence_band=_band(conf),
            channel="enacted",
            decision_control=float(dc),
            information_engagement=float(ie),
            expressed_concern=float(concern),
            decision_control_confidence=0.7,
            information_engagement_confidence=0.7,
            concern_flag=concern >= 60,
            distribution=_distribution(role_key),
            baseline_source=DEMO_TAG,
            model=DEMO_TAG,
            engine_version=ENGINE_VERSION,
            framework_version=FRAMEWORK_VERSION,
            rubric_version=RUBRIC_VERSION,
        ).save()
        created += 1
        print(f"seeded {patient_name} -> {role_name}")
    print(f"done: {created} new encounters")


def main() -> None:
    activemodel.init(database_url())  # direct connect, no migration
    doctor = get_dev_doctor()
    if "--delete" in sys.argv:
        delete_demo(doctor)
    else:
        seed(doctor)


if __name__ == "__main__":
    main()
