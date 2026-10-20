"""Short build leases, bounded model work, and source revalidation at publish."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable, cast

from typeid import TypeID

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.services.session_prep import SessionPrepPreparedBrief, SessionPrepScopeDenied
from app.services.session_prep_generator import (
    SessionPrepGenerationError,
    generate_prepared_brief,
)
from app.services.session_prep_note_sources import ReviewedNoteSelection
from app.services.session_prep_runtime import (
    current_source_state,
    prepare_artifact_result,
    ready_artifact,
    source_manifest,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.session_prep_runtime import SessionPrepArtifact, SessionPrepBuild
from sqlmodel import Session, and_, or_, select

LEASE = timedelta(seconds=90)
FAILURE_COOLDOWN = timedelta(minutes=5)


def run_one_build(
    *,
    generate: Callable[
        [ReviewedNoteSelection], SessionPrepPreparedBrief
    ] = generate_prepared_brief,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> str:
    """Claim one due job; a crashed worker's expired lease can be reclaimed.

    No source text, response, or exception detail is logged or returned. The
    source and authority are checked again after the external model call.
    """
    now = clock()
    with get_session() as managed:
        session = cast(Session, managed)
        build = session.exec(
            select(SessionPrepBuild)
            .where(
                SessionPrepBuild.retry_at <= now,
                or_(
                    SessionPrepBuild.state == "pending",
                    and_(
                        SessionPrepBuild.state == "running",
                        cast(Any, SessionPrepBuild.lease_until) <= now,
                    ),
                ),
            )
            .order_by(cast(Any, SessionPrepBuild.requested_at))
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if build is None:
            return "idle"
        if build.attempts >= 3:
            build.state = "failed"
            build.failure_code = "worker_attempts_exhausted"
            build.retry_at = now + FAILURE_COOLDOWN
            build.lease_until = None
            session.add(build)
            session.commit()
            return "failed"
        scope_id, build_id, wanted = (
            build.record_scope_id,
            build.id,
            build.desired_digest,
        )
        build.state = "running"
        build.attempts += 1
        attempt = build.attempts
        lease_until = now + LEASE
        build.lease_until = lease_until
        session.add(build)
        session.commit()

    try:
        with get_session() as managed:
            session = cast(Session, managed)
            scope = session.get(ProviderRecordAuthority, scope_id)
            if scope is None:
                raise SessionPrepScopeDenied
            selection, digest = current_source_state(
                session, scope, observed_at=now, settings=settings
            )
        if not selection.sources:
            raise SessionPrepScopeDenied
        # A request superseded before execution needs a fresh claim, not an
        # unnecessary model call over a stale selection.
        prepared = generate(selection) if digest == wanted else None
        failure = None
    except SessionPrepScopeDenied:
        prepared, failure = None, "scope_unavailable"
    except SessionPrepGenerationError as error:
        prepared, failure = None, error.code
    except Exception:
        # SDK exceptions and model validation messages can contain source text.
        prepared, failure = None, "generation_failed"

    completed_at = clock()
    with get_session() as managed:
        session = cast(Session, managed)
        scope = session.exec(
            select(ProviderRecordAuthority)
            .where(ProviderRecordAuthority.id == scope_id)
            .with_for_update()
        ).one_or_none()
        build = session.exec(
            select(SessionPrepBuild)
            .where(SessionPrepBuild.id == build_id)
            .with_for_update()
        ).one_or_none()
        if scope is None or build is None:
            return "discarded"
        if (
            build.state != "running"
            or build.desired_digest != wanted
            or build.lease_until != lease_until
            or build.attempts != attempt
        ):
            return "discarded"
        # An expired worker never publishes after a newer worker may have
        # claimed the same build. Leave its lease for recovery.
        if completed_at >= lease_until:
            return "discarded"
        try:
            latest, latest_digest = current_source_state(
                session, scope, observed_at=completed_at, settings=settings
            )
        except Exception:
            latest, latest_digest = None, wanted
            failure = "scope_unavailable"
        if latest is not None and latest_digest != wanted:
            build.desired_digest = latest_digest
            build.state = "pending" if latest.sources else "failed"
            build.attempts = 0
            build.retry_at = completed_at
            build.failure_code = None if latest.sources else "no_eligible_sources"
            outcome = "superseded"
        elif prepared is not None and latest is not None and latest.sources:
            existing = ready_artifact(session, scope.id, wanted)
            if existing is None:
                predecessor = session.exec(
                    select(SessionPrepArtifact)
                    .where(SessionPrepArtifact.record_scope_id == scope.id)
                    .order_by(cast(Any, SessionPrepArtifact.version).desc())
                    .limit(1)
                ).first()
                artifact = SessionPrepArtifact(
                    record_scope_id=scope.id,
                    version=predecessor.version + 1 if predecessor else 1,
                    source_digest=wanted,
                    source_manifest=source_manifest(latest),
                    result_payload={},
                    generated_at=completed_at,
                )
                artifact.id = cast(TypeIDType, TypeID("prep_artifact"))
                try:
                    result = prepare_artifact_result(
                        artifact=artifact,
                        selection=latest,
                        prepared=prepared,
                        snapshot_at=now,
                        generated_at=completed_at,
                        predecessor=predecessor,
                    )
                except Exception:
                    failure = "invalid_brief"
                else:
                    artifact.result_payload = result.model_dump(mode="json")
                    session.add(artifact)
            if failure is None:
                build.state, build.failure_code = "ready", None
                outcome = "ready"
            else:
                outcome = "failed"
        else:
            outcome = "failed"
        if outcome == "failed":
            build.state = (
                "failed"
                if attempt >= 3 or failure == "scope_unavailable"
                else "pending"
            )
            build.failure_code = failure or "generation_failed"
            build.retry_at = completed_at + (
                FAILURE_COOLDOWN
                if build.state == "failed"
                else timedelta(seconds=15 * attempt)
            )
        build.lease_until = None
        session.add(build)
        session.commit()
        return outcome
