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


def test_disabled_or_unselected_provider_discovers_no_capability() -> None:
    decision = resolve_session_prep_discovery(
        selected_for_beta=False,
        provider_access_allowed=True,
        view_as_active=False,
    )

    assert decision.eligible is False
    assert decision.can_review_sources is False


def test_selected_provider_discovers_review_capability() -> None:
    decision = resolve_session_prep_discovery(
        selected_for_beta=True,
        provider_access_allowed=True,
        view_as_active=False,
    )

    assert decision.eligible is True
    assert decision.can_review_sources is True


def test_view_as_can_discover_target_selection_but_never_review_authority() -> None:
    decision = resolve_session_prep_discovery(
        selected_for_beta=True,
        provider_access_allowed=True,
        view_as_active=True,
    )

    assert decision.eligible is True
    assert decision.can_review_sources is False


def test_selected_provider_without_current_product_access_cannot_review() -> None:
    decision = resolve_session_prep_discovery(
        selected_for_beta=True,
        provider_access_allowed=False,
        view_as_active=False,
    )

    assert decision.eligible is True
    assert decision.can_review_sources is False
