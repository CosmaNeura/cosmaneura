"""Score a patient's enacted communication role from their visit transcript.

Engagement engine v2: reads the patient's turns of the visit transcript and maps
them to a Control Preferences Scale role (Self-Directed / Collaborative / Directed)
plus a calibrated confidence. Decision-support communication aid only -- not a
diagnosis or personality assessment. One EngagementRole row per transcript.
"""

from app import log
from app.celery import celery_app
from app.engagement.service import (
    build_doctor_baseline,
    reconstruct_intake_text,
    reconstruct_patient_text,
    score_patient_text,
)
from app.services.doctor_access import doctor_access_in_force
from app.services.external_webhook_inbox import provider_log_ref

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor import Doctor
from app.models.engagement_role import ConfidenceBand, EngagementRole
from app.models.intake.intake import Intake


def perform(transcript_id: str | TypeIDType):
    log.info(
        "starting engagement scoring",
        transcript_ref=provider_log_ref(str(transcript_id)),
    )

    transcript = AIVisitTranscript.get(id=transcript_id)
    assert transcript

    doctor = Doctor.get(id=transcript.doctor_id)
    if doctor is None or not doctor_access_in_force(doctor):
        log.info(
            "engagement scoring skipped: access unavailable",
            event_code="engagement_scoring_access_not_in_force",
            transcript_ref=provider_log_ref(str(transcript_id)),
        )
        return

    # Idempotency: one role per transcript. Celery retries / duplicate triggers
    # hit this guard (ai_transcript_id is unique).
    if EngagementRole.get(ai_transcript_id=transcript.id):
        log.info(
            "transcript already scored, skipping",
            transcript_ref=provider_log_ref(str(transcript_id)),
        )
        return

    patient_text = reconstruct_patient_text(transcript.transcript_data)

    # Preferred read: the patient's own intake free-text, if any. When present the
    # engine also returns the preference-enactment gap.
    intake = Intake.get(appointment_id=transcript.appointment_id)
    intake_text = reconstruct_intake_text(intake.intake_questions) if intake else None

    # R3: recalibrate the enacted read against this doctor's own population once
    # enough encounters have accrued (neutral until then).
    baseline = build_doctor_baseline(transcript.doctor_id)
    result = score_patient_text(
        patient_text,
        intake_text=intake_text,
        patient_ref=str(transcript.id),
        enacted_baseline=baseline,
    )
    if result is None:
        log.info(
            "too little patient speech to score, skipping",
            transcript_ref=provider_log_ref(str(transcript_id)),
            words=len(patient_text.split()),
        )
        return

    role = result.role
    ind = role.indicators
    distribution = {item.role_key: item.probability for item in role.distribution}

    preferred = result.preferred_role
    gap = result.gap

    log.info(
        "saving engagement role",
        transcript_ref=provider_log_ref(str(transcript_id)),
        role=role.role_key,
        confidence=role.confidence,
        band=role.confidence_band.value,
    )
    EngagementRole(
        appointment_id=transcript.appointment_id,
        doctor_id=transcript.doctor_id,
        ai_transcript_id=transcript.id,
        role_key=role.role_key,
        role_name=role.role_name,
        cps_role=role.cps_role,
        confidence=role.confidence,
        confidence_band=ConfidenceBand(role.confidence_band.value),
        channel=role.channel,
        decision_control=ind.decision_control.score,
        information_engagement=ind.information_engagement.score,
        expressed_concern=ind.expressed_concern.score,
        decision_control_confidence=ind.decision_control.confidence,
        information_engagement_confidence=ind.information_engagement.confidence,
        concern_flag=role.concern_flag,
        distribution=distribution,
        preferred_role_key=preferred.role_key if preferred else None,
        preferred_role_name=preferred.role_name if preferred else None,
        preferred_confidence=preferred.confidence if preferred else None,
        gap_direction=gap.direction if gap else None,
        gap_decision_control=gap.decision_control_gap if gap else None,
        gap_magnitude_band=gap.magnitude_band.value if gap else None,
        baseline_source=role.baseline_source,
        model=result.model,
        engine_version=result.engine_version,
        framework_version=result.framework_version,
        rubric_version=result.rubric_version,
    ).save()


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    log.info("queuing engagement scoring job")
    perform_celery.delay(*args, **kwargs)
