"""Fresh discovery projection parity under the actual provider locks."""

import pytest

from app.services import authority, companion_encounters
from app.services.doctor_access_row_ownership import DoctorAccessRowOwnership

from app.models.practice import Practice
from sqlmodel import Session

from tests.authority_kernel.companion_provider_provisioning_test import (
    SETTINGS,
    session_for,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    query_counts,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


def project(session, args):
    return companion_encounters._provider(
        session,
        issuer=SETTINGS.clerk_issuer,
        subject=args["verified_clerk_subject"],
        session_id="session_invented_scribe",
        tenant_ref=args["tenant_id"],
        settings=SETTINGS,
    )


@pytest.mark.parametrize("label", ["", "A" * 200, "A" * 201, "Clínica inventada"])
def test_label_cardinality_and_response_match_original_projection(
    native, engine, monkeypatch, label
):
    with session_for(engine) as setup:
        practice = setup.get(Practice, native.args["practice_id"])
        assert practice is not None
        practice.legal_name = label
        setup.add(practice)
        setup.commit()

    results = []
    for optimized in (False, True):
        with monkeypatch.context() as patch:
            if not optimized:
                patch.setattr(
                    DoctorAccessRowOwnership, "matches", lambda *_args, **_kwargs: False
                )
            with session_for(engine) as session:
                if len(label) in range(1, 201):
                    results.append(project(session, native.args))
                else:
                    with pytest.raises(authority.AuthorityRequestDeniedError):
                        project(session, native.args)
    if results:
        assert results[0] == results[1]
        assert results[1][0].practice_label == label


def test_projection_reads_current_database_label_instead_of_cached_model(
    native, engine
):
    with Session(engine) as session:
        cached = session.get(Practice, native.args["practice_id"])
        assert cached is not None
        original = cached.legal_name
        # A dirty ORM instance is not the display projection's source. Disable
        # autoflush only for this controlled stale-value test.
        cached.legal_name = "Invented unsaved label"
        with session.no_autoflush:
            snapshot, _ = project(session, native.args)
        assert snapshot.practice_label == original


def test_only_owned_projection_reduces_queries(native, engine, monkeypatch):
    original = DoctorAccessRowOwnership.matches
    results = []
    for optimized in (False, True):
        with monkeypatch.context() as patch:
            # Retain original access-query optimization in both runs. Only the
            # direct projection's proof is invalidated in the baseline.
            def matches(proof, *args, **kwargs):
                import inspect

                frame = inspect.currentframe()
                assert frame is not None and frame.f_back is not None
                try:
                    if not optimized and frame.f_back.f_code.co_name == "_provider":
                        return False
                finally:
                    del frame
                return original(proof, *args, **kwargs)

            patch.setattr(DoctorAccessRowOwnership, "matches", matches)
            with session_for(engine) as session, query_counts(engine) as counts:
                value = project(session, native.args)
            results.append((value, counts["all_selects"]))
    assert results[0][0] == results[1][0]
    assert results[0][1] == results[1][1] + 1
