import runpy
from pathlib import Path

MODULE = runpy.run_path(
    str(
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "session_prep_discovery.py"
    )
)
resolve_session_prep_discovery = MODULE["resolve_session_prep_discovery"]


def test_globally_disabled_provider_discovers_no_capability() -> None:
    decision = resolve_session_prep_discovery(
        rollout_enabled=False,
        provider_access_allowed=True,
        view_as_active=False,
    )

    assert decision.eligible is False
    assert decision.can_review_sources is False


def test_globally_enabled_current_provider_discovers_review_capability() -> None:
    decision = resolve_session_prep_discovery(
        rollout_enabled=True,
        provider_access_allowed=True,
        view_as_active=False,
    )

    assert decision.eligible is True
    assert decision.can_review_sources is True


def test_view_as_discovers_no_provider_capability() -> None:
    decision = resolve_session_prep_discovery(
        rollout_enabled=True,
        provider_access_allowed=True,
        view_as_active=True,
    )

    assert decision.eligible is False
    assert decision.can_review_sources is False


def test_provider_becomes_eligible_automatically_when_onboarding_access_is_ready() -> (
    None
):
    before_onboarding = resolve_session_prep_discovery(
        rollout_enabled=True,
        provider_access_allowed=False,
        view_as_active=False,
    )
    after_onboarding = resolve_session_prep_discovery(
        rollout_enabled=True,
        provider_access_allowed=True,
        view_as_active=False,
    )

    assert before_onboarding.eligible is False
    assert before_onboarding.can_review_sources is False
    assert after_onboarding.eligible is True
    assert after_onboarding.can_review_sources is True


def test_incomplete_or_revoked_provider_discovers_no_capability() -> None:
    decision = resolve_session_prep_discovery(
        rollout_enabled=True,
        provider_access_allowed=False,
        view_as_active=False,
    )

    assert decision.eligible is False
    assert decision.can_review_sources is False
