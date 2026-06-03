"""Score a patient's enacted communication role from their visit transcript.

Engagement engine v2: reads the patient's turns of the visit transcript and maps
them to a Control Preferences Scale role (Self-Directed / Collaborative / Directed)
plus a calibrated confidence. Decision-support communication aid only -- not a
diagnosis or personality assessment. One EngagementRole row per transcript.
"""

from app import log
from app.celery import celery_app
from app.engagement.service import reconstruct_patient_text, score_patient_text

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.engagement_role import ConfidenceBand, EngagementRole


def perform(transcript_id: str | TypeIDType):
    log.info("starting engagement scoring", transcript_id=transcript_id)

    transcript = AIVisitTranscript.get(id=transcript_id)
    assert transcript

    # Idempotency: one role per transcript. Celery retries / duplicate triggers
    # hit this guard (ai_transcript_id is unique).
    if EngagementRole.get(ai_transcript_id=transcript.id):
        log.info("transcript already scored, skipping", transcript_id=transcript_id)
        return

    patient_text = reconstruct_patient_text(transcript.transcript_data)
    result = score_patient_text(patient_text, patient_ref=str(transcript.id))
    if result is None:
        log.info(
            "too little patient speech to score, skipping",
            transcript_id=transcript_id,
            words=len(patient_text.split()),
        )
        return

    role = result.role
    ind = role.indicators
    distribution = {item.role_key: item.probability for item in role.distribution}

    log.info(
        "saving engagement role",
        transcript_id=transcript_id,
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
        baseline_source=role.baseline_source,
        model=result.model,
        engine_version=result.engine_version,
        framework_version=result.framework_version,
        rubric_version=result.rubric_version,
    ).save()


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    log.info("queuing engagement scoring job", args=args, kwargs=kwargs)
    perform_celery.delay(*args, **kwargs)
