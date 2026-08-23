"""
Extract the medications and supplements a patient mentions in a visit
transcript into structured, unconfirmed PatientMedication rows.

Runs after transcription (same fan-out as engagement scoring). Rows land
unconfirmed; the provider reviews them before any interaction/depletion check
runs against the list. Same clinical LLM path as note generation (Azure
OpenAI, JSON mode) with an offline deterministic fallback for tests.

Transcript content is PHI: logs carry IDs and counts only.
"""

import json
from textwrap import dedent

from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.environments import should_use_test_openai_fallback
from app.services.rxnorm import normalize_drug_name

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.patient_medication import (
    MedicationKind,
    MedicationSource,
    MedicationStatus,
    PatientMedication,
)

_VALID_KINDS = {k.value for k in MedicationKind}


def _local_extract(transcript_text: str) -> list[dict]:
    "deterministic offline stand-in for tests — matches a few seeded fixtures"

    catalog = [
        ("metformin", MedicationKind.prescription),
        ("lisinopril", MedicationKind.prescription),
        ("fish oil", MedicationKind.supplement),
        ("vitamin d", MedicationKind.supplement),
        ("ibuprofen", MedicationKind.otc),
    ]
    lowered = transcript_text.lower()
    return [
        {"name": name, "kind": kind.value, "dosage": None, "status": "active"}
        for name, kind in catalog
        if name in lowered
    ]


def build_extraction_prompt(transcript_text: str) -> str:
    return dedent(f"""
    You are reviewing the transcript of a clinical visit. Extract every
    medication, over-the-counter drug, and dietary supplement the patient
    says they are taking or that the provider tells them to take.

    <TRANSCRIPT>
    {transcript_text}
    </TRANSCRIPT>

    Rules:
    - Include prescription drugs, OTC drugs, vitamins, minerals, herbs, and
      other supplements.
    - Do NOT include foods, procedures, or conditions.
    - Use the plain ingredient/drug name; keep dose/frequency separately.
    - If the patient says they stopped something, mark status "stopped".
    - Do not invent anything not stated in the transcript.

    Respond only in JSON with this schema:
    {{
      "medications": [
        {{
          "name": string,            // e.g. "metformin"
          "kind": "prescription" | "otc" | "supplement" | "unknown",
          "dosage": string | null,   // e.g. "500mg twice daily"
          "status": "active" | "stopped"
        }}
      ]
    }}
    """)


def extract_from_text(transcript_text: str) -> list[dict]:
    if should_use_test_openai_fallback():
        return _local_extract(transcript_text)

    prompt = build_extraction_prompt(transcript_text)
    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )
    raw = response.choices[0].message.content
    if not raw:
        return []
    return json.loads(raw).get("medications") or []


def perform(transcript_id: str | TypeID | TypeIDType):
    log.info("extracting medications", transcript_id=transcript_id)

    transcript = AIVisitTranscript.get(id=transcript_id)
    if transcript is None:
        log.warning("transcript not found for medication extraction")
        return

    # idempotent: skip if we already extracted from this appointment's transcript
    existing = PatientMedication.where(
        (PatientMedication.appointment_id == transcript.appointment_id)
        & (PatientMedication.source == MedicationSource.transcript)
    ).count()
    if existing:
        log.info("medications already extracted, skipping", transcript_id=transcript_id)
        return

    transcript_text = "\n".join(
        f"{u.speaker}: {u.content}" for u in transcript.transcript_data
    )

    extracted = extract_from_text(transcript_text)

    created = 0
    for item in extracted:
        name = (item.get("name") or "").strip()
        if not name:
            continue

        kind = item.get("kind")
        if kind not in _VALID_KINDS:
            kind = MedicationKind.unknown.value

        rxnorm_cui, canonical = normalize_drug_name(name)

        # RxNorm is a *drug* vocabulary: it recognizes prescriptions well and
        # supplements poorly, so for a nutrition-led practice the no-match path
        # is the common one. Display the best name we have, but always match on
        # a lowercased key derived from it — otherwise the same substance keys
        # as 'vitamin D' or 'vitamin d' depending on whether RxNav answered,
        # which silently splits a patient's list and misses downstream lookups.
        display_name = canonical or name
        normalized_name = display_name.strip().lower()

        PatientMedication(
            doctor_id=transcript.doctor_id,
            patient_id=transcript.patient_id,
            appointment_id=transcript.appointment_id,
            name=name,
            display_name=display_name,
            normalized_name=normalized_name,
            rxnorm_cui=rxnorm_cui,
            kind=MedicationKind(kind),
            status=MedicationStatus(
                item.get("status")
                if item.get("status") in {s.value for s in MedicationStatus}
                else "active"
            ),
            source=MedicationSource.transcript,
            dosage=item.get("dosage"),
            confirmed=False,
        ).save()
        created += 1

    log.info(
        "extracted medications",
        transcript_id=transcript_id,
        count=created,
    )


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)
