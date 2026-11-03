"""Source inventory, not an assertion that customer custody has been resolved.

Every surface requires a post-expiry enumeration, including empty surfaces.
Adapters must discover unlisted copies/schema changes and refuse closure until
the catalog is reviewed. No tenant wildcard or login deletion is authorized.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Surface:
    key: str
    disposition: Literal[
        "erase", "baa-deidentified", "compliance", "finance", "protected"
    ]
    source: str


CORE_PHI_TABLES = (
    "patient",
    "appointment",
    "ai_visit_transcript",
    "ai_visit_note",
    "visit_audio_file",
    "doctor_note_file",
    "doctor_note_example",
    "doctor_note_schema",
    "note_type_customization",
    "intake",
    "intake_summary",
    "patient_medication",
    "lab_panel",
    "lab_result",
    "engagement_role",
    "cps_self_report",
    "llm_response",
    "scribe_patient_identity",
    "scribe_private_note",
    "scribe_capture_note_context",
    "scribe_capture_admission",
    "session_prep_artifact",
    "session_prep_read_receipt",
    "session_prep_note_finalization",
    "canonical_conversation_user_phase",
    "canonical_conversation_final_phase",
    "visit_prep_question_set",
    "visit_prep_question_set_version",
    "visit_prep_question",
    "billing_header_prepared_operation",
    "external_webhook_delivery",
)
SCRIBE_PHI_TABLES = (
    "consent_records",
    "scribe_sessions",
    "audio_chunks",
    "audio_upload_grants",
    "session_audio_keys",
    "transcripts",
    "transcription_jobs",
    "transcript_deliveries",
    "capture_leases",
    "capture_lease_receipts",
    "audio_chunk_reservation_batches",
    "audio_chunk_reservations",
    "audio_chunk_reservation_batch_items",
    "audio_upload_authorization_receipts",
    "capture_channel_finalizations",
    "external_session_terminal_events",
    "normalized_source_transcripts",
    "normalized_source_segments",
    "normalized_source_receipt_keys",
    "normalized_translation_attempts",
    "normalized_translation_reviews",
    "normalized_working_transcripts",
    "normalized_working_translation_inputs",
    "normalized_working_review_inputs",
    "normalized_working_segments",
    "normalized_working_blockers",
    "encrypted_translation_jobs",
    "source_start_attempts",
    "source_start_extensions",
    "source_start_retirements",
    "creator_transcript_edits",
)

# Metadata with PHI links must be classified/redacted separately from immutable
# non-PHI audit facts. These surfaces cannot be deleted wholesale.
CORE_LINK_TABLES = (
    "scribe_capture_provider_link",
    "scribe_capture_binding",
    "scribe_capture_source_version",
    "scribe_capture_reviewed_source",
    "scribe_capture_note_edit",
    "scribe_capture_note_save_fence",
    "scribe_capture_correction_frontier",
    "scribe_capture_gap_manifest",
    "scribe_encounter_creation",
    "scribe_transcript_delivery",
    "session_prep_build",
    "provider_record_authority",
    "canonical_conversation_turn",
    "interaction_lineage",
    "zoom_capture_binding",
    "zoom_meeting",
    "ehr_sandbox_record_mapping",
    "synthetic_preview_appointment_fixture",
    "note_template_preference",
    "scribe_private_note_receipt",
    "ehr_sandbox_patient_designation",
    "external_webhook_operation",
)

# Several physical tables share a model source; preserve exact discovery paths.
CORE_MODEL_FILES = {
    "intake": "intake/intake.py",
    "intake_summary": "intake/intake_summary.py",
    "session_prep_artifact": "session_prep_runtime.py",
    "session_prep_read_receipt": "session_prep_runtime.py",
    "canonical_conversation_user_phase": "authority_conversation.py",
    "canonical_conversation_final_phase": "authority_conversation.py",
    "visit_prep_question_set_version": "visit_prep_question_set.py",
    "visit_prep_question": "visit_prep_question_set.py",
    "billing_header_prepared_operation": "billing_header_operation.py",
}

SURFACES = tuple(
    [
        Surface(
            f"core.{name}",
            "erase",
            f"app/models/{CORE_MODEL_FILES.get(name, name + '.py')}",
        )
        for name in CORE_PHI_TABLES
    ]
    + [
        Surface(
            f"core.{name}",
            "erase",
            "app/models: classify PHI-linked metadata; preserve audit",
        )
        for name in CORE_LINK_TABLES
    ]
    + [
        Surface(
            f"scribe.{name}",
            "erase",
            "Scribe alembic/versions and postgres_repository.py",
        )
        for name in SCRIBE_PHI_TABLES
    ]
    + [
        Surface(
            "storage.core-uploads",
            "erase",
            "VisitAudioFile/LabPanel.azure_file_name; AZURE_UPLOADS_CONTAINER_NAME",
        ),
        Surface(
            "storage.core-examples",
            "erase",
            "DoctorNoteFile.azure_file_name; registered encrypted refs; pending/*.pdf; owner-stamped legacy; AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME",
        ),
        Surface(
            "storage.scribe-audio",
            "erase",
            "storage.py: tenants/{tenant}/sessions/{session}/{channel}/{sequence:08d}-{object}.enc",
        ),
        Surface(
            "copies.blob-versions-soft-delete",
            "erase",
            "All three containers: previous versions, snapshots, soft-deleted objects, replicas, uncommitted blocks",
        ),
        Surface(
            "copies.database-backups-replicas",
            "erase",
            "Core and Scribe: PITR/WAL, automated/manual backups, restore replicas, snapshots, exports",
        ),
        Surface(
            "copies.queues-caches-workers",
            "erase",
            "Core Redis/Celery/redbeat payloads; Scribe workers; temporary audio, transcripts, crash files, captured mounts",
        ),
        Surface(
            "copies.exports-and-devices",
            "erase",
            "Server export archives/temp copies; Scribe preferences/localStorage/Keychain/app sandbox; disconnected device custody unresolved",
        ),
        Surface(
            "copies.processors-observability",
            "erase",
            "Subprocessor retained speech/translation/AI/deidentification copies, telemetry/log/error/trace bodies; discover deployed providers under actual BAAs",
        ),
        Surface(
            "copies.unregistered-orphans",
            "erase",
            "Uploads/grants without registered rows, orphaned sessions, legacy paths, canceled/in-flight writers; never infer ownership from prefix",
        ),
        Surface(
            "core.ai_visit_transcript_non_phi",
            "baa-deidentified",
            "No identifying FK/timestamp; content still needs independent deidentification and actual BAA permission",
        ),
        Surface(
            "documents.compliance",
            "compliance",
            "Core/Scribe audit facts, BAA assets/acceptances, cancellation/erasure evidence, access/compliance records: >=6 years; append-only/indefinite rules remain",
        ),
        Surface(
            "documents.finance-contracts",
            "finance",
            "Billing/Stripe financial facts, executed contracts/amendments and finance records: >=7 years; separate classified non-PHI custody",
        ),
        Surface(
            "protected.shared-account-practice",
            "protected",
            "Clerk user/Core Doctor/AuthorityIdentity/actors; practice and memberships including other clinicians remain unchanged",
        ),
    ]
)
CATALOG_VERSION = "core-scribe-retention-2026-11-02-v2"
BY_KEY = {surface.key: surface for surface in SURFACES}
assert len(BY_KEY) == len(SURFACES)
