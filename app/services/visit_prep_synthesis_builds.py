"""Dormant authorized queue/lease recovery; never invokes a model or publishes.

Claiming exposes only an opaque fence. Source selection then reacquires the
original requester, owner authority and current contribution in canonical lock
order. A future worker must also record central usage and publish atomically
with its still-current lease; these functions do not bypass either requirement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from app.services.visit_prep_packages import (
    _audit,
    _authorize,
    _database_now,
    _load_authorized_package,
    _LockedPackage,
)
from app.services.visit_prep_synthesis_contract import (
    GENERATOR_VERSION,
    PROMPT_VERSION,
    VisitPrepSynthesisSelection,
    select_patient_sources,
)
from app.services.visit_prep_synthesis_generator import MODEL_PROFILE
from app.services.visit_prep_synthesis_publication import (
    VisitPrepSynthesisStale,
    _current_artifact,
    _decoded_current,
)
from app.services.visit_preps import (
    VisitPrepConflict,
    VisitPrepNotFound,
    _commit,
    _visit_prep_session,
)
from app.utils.encryption import decrypt_value, encrypt_value

from activemodel.types import TypeIDType
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild
from sqlmodel import Session, and_, or_, select

LEASE = timedelta(seconds=90)
FAILURE_COOLDOWN = timedelta(minutes=5)
FailureCode = Literal["scope_unavailable", "generation_failed", "usage_unavailable"]


class VisitPrepBuildDiscarded(VisitPrepConflict):
    def __init__(self) -> None:
        super().__init__("Visit Prep synthesis work is no longer current")


@dataclass(frozen=True)
class VisitPrepBuildReceipt:
    build_id: TypeIDType
    source_version: str
    epoch: int
    state: str


@dataclass(frozen=True)
class VisitPrepBuildClaim:
    build_id: TypeIDType
    visit_prep_id: TypeIDType
    source_version: str
    epoch: int
    attempt: int
    lease_until: datetime


def _profile_query():
    return select(VisitPrepSynthesisBuild).where(
        VisitPrepSynthesisBuild.generator_version == GENERATOR_VERSION,
        VisitPrepSynthesisBuild.prompt_version == PROMPT_VERSION,
        VisitPrepSynthesisBuild.model_profile == MODEL_PROFILE,
    )


def _requester_context(prep: VisitPrep) -> dict[str, str | int]:
    return dict(
        version=1,
        visit_prep_id=str(prep.id),
        owner_practice_member_id=str(prep.owner_practice_member_id),
        owner_doctor_id=str(prep.owner_doctor_id),
        practice_id=str(prep.practice_id),
        generator_version=GENERATOR_VERSION,
        prompt_version=PROMPT_VERSION,
        model_profile=MODEL_PROFILE,
    )


def _encrypt_requester(prep: VisitPrep, subject: str) -> str:
    if not isinstance(subject, str) or not 1 <= len(subject) <= 256:
        raise VisitPrepNotFound("Visit Prep is unavailable")
    return encrypt_value(
        json.dumps(
            {**_requester_context(prep), "subject": subject}, separators=(",", ":")
        )
    )


def _decrypt_requester(build: VisitPrepSynthesisBuild, prep: VisitPrep) -> str:
    try:
        if (
            build.requester_format_version != 1
            or not 1 <= len(build.requester_ciphertext) <= 4096
        ):
            raise ValueError
        payload = json.loads(decrypt_value(build.requester_ciphertext))
        expected = _requester_context(prep)
        if not isinstance(payload, dict) or set(payload) != {*expected, "subject"}:
            raise ValueError
        if any(
            type(payload[key]) is not type(value) or payload[key] != value
            for key, value in expected.items()
        ):
            raise ValueError
        subject = payload["subject"]
        if not isinstance(subject, str) or not 1 <= len(subject) <= 256:
            raise ValueError
        return subject
    except Exception:
        raise VisitPrepNotFound("Visit Prep is unavailable") from None


def _finish(
    session: Session,
    locked: _LockedPackage,
    operation: Literal["synthesis_requested", "synthesis_sources_selected"],
    *,
    build: VisitPrepSynthesisBuild | None = None,
    claim: VisitPrepBuildClaim | None = None,
) -> None:
    now = _database_now(session)
    if claim is not None and (build is None or not _matches(build, claim, now)):
        raise VisitPrepBuildDiscarded()
    _authorize(session, owner=locked.owner, practice=locked.practice, at=now)
    _audit(
        session,
        owner=locked.owner,
        prep_id=locked.preparation.id,
        source_version=locked.package.source_version,
        at=now,
        operation=operation,
    )
    _commit(session)


def request_visit_prep_synthesis(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    expected_source_version: str,
) -> VisitPrepBuildReceipt:
    """Idempotently request the currently displayed package, not arbitrary text."""
    with _visit_prep_session() as session:
        locked = _load_authorized_package(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            expected_clerk_subject=expected_clerk_subject,
            visit_prep_id=visit_prep_id,
        )
        selection = select_patient_sources(locked.package)
        if selection.source_version != expected_source_version:
            raise VisitPrepSynthesisStale()
        build = session.exec(
            _profile_query()
            .where(VisitPrepSynthesisBuild.visit_prep_id == visit_prep_id)
            .with_for_update()
        ).one_or_none()
        now = _database_now(session)
        existing = _current_artifact(
            session, prep_id=visit_prep_id, source_version=selection.source_version
        )
        if existing is not None:
            _decoded_current(existing, locked, selection)
        if build is None:
            build = VisitPrepSynthesisBuild(
                visit_prep_id=visit_prep_id,
                owner_practice_member_id=locked.owner.id,
                question_set_version_id=locked.preparation.question_set_version_id,
                generator_version=GENERATOR_VERSION,
                prompt_version=PROMPT_VERSION,
                model_profile=MODEL_PROFILE,
                source_version=selection.source_version,
                requester_ciphertext=_encrypt_requester(
                    locked.preparation, expected_clerk_subject
                ),
                requested_at=now,
                retry_at=now,
                state="ready" if existing else "pending",
            )
        elif (
            (existing is not None and build.state != "ready")
            or (existing is None and build.state == "ready")
            or build.source_version != selection.source_version
            or (build.state == "failed" and build.retry_at <= now)
        ):
            build.epoch += 1
            build.attempts = 0
            build.source_version = selection.source_version
            build.requester_ciphertext = _encrypt_requester(
                locked.preparation, expected_clerk_subject
            )
            build.requested_at = build.retry_at = now
            build.state = "ready" if existing else "pending"
            build.lease_until = build.failure_code = None
        # Same-source retries must not extend a lease, erase attempts/backoff or
        # silently replace the requester that authorized running work.
        session.add(build)
        session.flush()
        result = VisitPrepBuildReceipt(
            build.id, build.source_version, build.epoch, build.state
        )
        _finish(session, locked, "synthesis_requested")
        return result


def claim_visit_prep_synthesis() -> VisitPrepBuildClaim | None:
    """Claim one due profile job; no authority or patient content leaves here."""
    with _visit_prep_session() as session:
        now = _database_now(session)
        build = session.exec(
            _profile_query()
            .where(
                VisitPrepSynthesisBuild.retry_at <= now,
                or_(
                    VisitPrepSynthesisBuild.state == "pending",
                    and_(
                        VisitPrepSynthesisBuild.state == "running",
                        cast(Any, VisitPrepSynthesisBuild.lease_until) <= now,
                    ),
                ),
            )
            .order_by(
                cast(Any, VisitPrepSynthesisBuild.requested_at),
                cast(Any, VisitPrepSynthesisBuild.id),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if build is None:
            return None
        now = _database_now(session)
        if build.attempts >= 3:
            build.state, build.failure_code = "failed", "worker_attempts_exhausted"
            build.lease_until = None
            build.retry_at = now + FAILURE_COOLDOWN
            session.add(build)
            _commit(session)
            return None
        build.attempts += 1
        build.state, build.failure_code = "running", None
        build.lease_until = now + LEASE
        session.add(build)
        result = VisitPrepBuildClaim(
            build.id,
            build.visit_prep_id,
            build.source_version,
            build.epoch,
            build.attempts,
            build.lease_until,
        )
        _commit(session)
        return result


def _matches(
    build: VisitPrepSynthesisBuild, claim: VisitPrepBuildClaim, now: datetime
) -> bool:
    return (
        build.id == claim.build_id
        and build.visit_prep_id == claim.visit_prep_id
        and build.state == "running"
        and build.epoch == claim.epoch
        and build.attempts == claim.attempt
        and build.source_version == claim.source_version
        and build.lease_until == claim.lease_until
        and now < claim.lease_until
        and build.generator_version == GENERATOR_VERSION
        and build.prompt_version == PROMPT_VERSION
        and build.model_profile == MODEL_PROFILE
    )


def fail_visit_prep_synthesis_claim(
    claim: VisitPrepBuildClaim, *, code: FailureCode
) -> bool:
    """A stale/expired worker cannot release or fail someone else's claim."""
    if code not in {"scope_unavailable", "generation_failed", "usage_unavailable"}:
        raise VisitPrepBuildDiscarded()
    with _visit_prep_session() as session:
        build = session.exec(
            select(VisitPrepSynthesisBuild)
            .where(VisitPrepSynthesisBuild.id == claim.build_id)
            .with_for_update()
        ).one_or_none()
        now = _database_now(session)
        if build is None or not _matches(build, claim, now):
            return False
        build.state = (
            "failed"
            if claim.attempt >= 3 or code in {"scope_unavailable", "usage_unavailable"}
            else "pending"
        )
        build.failure_code, build.lease_until = code, None
        build.retry_at = now + (
            FAILURE_COOLDOWN
            if build.state == "failed"
            else timedelta(seconds=15 * claim.attempt)
        )
        session.add(build)
        _commit(session)
        return True


def load_claimed_synthesis_sources(
    claim: VisitPrepBuildClaim,
) -> VisitPrepSynthesisSelection:
    """Recheck original identity and current owner/source/lease before model work.

    A superseded request is requeued with a new epoch; the old worker receives
    no sources. Authorization failure terminates that claim rather than retrying
    a model call over inaccessible records.
    """
    try:
        with _visit_prep_session() as session:
            candidate = session.get(VisitPrepSynthesisBuild, claim.build_id)
            prep = session.get(VisitPrep, claim.visit_prep_id)
            if (
                candidate is None
                or prep is None
                or not _matches(candidate, claim, _database_now(session))
            ):
                raise VisitPrepBuildDiscarded()
            ciphertext = candidate.requester_ciphertext
            subject = _decrypt_requester(candidate, prep)
            locked = _load_authorized_package(
                session,
                owner_practice_member_id=prep.owner_practice_member_id,
                owner_doctor_id=prep.owner_doctor_id,
                practice_id=prep.practice_id,
                expected_clerk_subject=subject,
                visit_prep_id=prep.id,
            )
            build = session.exec(
                select(VisitPrepSynthesisBuild)
                .where(VisitPrepSynthesisBuild.id == claim.build_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one_or_none()
            if (
                build is None
                or build.requester_ciphertext != ciphertext
                or not _matches(build, claim, _database_now(session))
            ):
                raise VisitPrepBuildDiscarded()
            current = select_patient_sources(locked.package)
            if current.source_version != claim.source_version:
                build.source_version = current.source_version
                build.epoch += 1
                build.attempts = 0
                build.state = "pending"
                build.lease_until = build.failure_code = None
                build.requested_at = build.retry_at = _database_now(session)
                session.add(build)
                _finish(session, locked, "synthesis_requested")
                raise VisitPrepBuildDiscarded()
            _finish(
                session, locked, "synthesis_sources_selected", build=build, claim=claim
            )
            return current
    except VisitPrepBuildDiscarded:
        raise
    except (VisitPrepNotFound, VisitPrepConflict):
        fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")
        raise VisitPrepBuildDiscarded() from None
