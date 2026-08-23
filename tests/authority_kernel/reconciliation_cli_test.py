import json
from contextlib import nullcontext

from typeid import TypeID
from typer.testing import CliRunner

import app.cli as cli_module
import app.environments as environments
import app.services.authority_reconciliation as reconciliation
from app.cli import app
from app.services.authority_reconciliation import CanonicalTurnReconciliationResult


def test_canonical_turn_reconciliation_defaults_to_dry_run_and_requires_apply_confirm(
    monkeypatch,
) -> None:
    turn_ref = str(TypeID("conversation_turn"))
    calls: list[dict[str, object]] = []

    def reconcile(**kwargs):
        calls.append(kwargs)
        return CanonicalTurnReconciliationResult(
            turn_ref=turn_ref,
            interaction_ref="ix_opaque_reconciliation_ref",
            state="eligible" if not kwargs["apply"] else "reconciled",
            dry_run=not kwargs["apply"],
            changed=bool(kwargs["apply"]),
        )

    monkeypatch.setattr(environments, "is_staging", lambda: True)
    monkeypatch.setattr(cli_module, "global_session", nullcontext)
    monkeypatch.setattr(reconciliation, "reconcile_stale_accepted_turn", reconcile)
    runner = CliRunner()

    dry_run = runner.invoke(
        app,
        [
            "reconcile-stale-canonical-turn",
            "--turn-ref",
            turn_ref,
            "--older-than-minutes",
            "30",
        ],
    )
    assert dry_run.exit_code == 0, dry_run.output
    output = json.loads(dry_run.stdout)
    assert output["mode"] == "dry-run"
    assert set(output) == {
        "turn_ref",
        "interaction_ref",
        "state",
        "mode",
        "changed",
    }
    assert calls[-1]["apply"] is False

    refused = runner.invoke(
        app,
        [
            "reconcile-stale-canonical-turn",
            "--turn-ref",
            turn_ref,
            "--apply",
        ],
    )
    assert refused.exit_code == 2
    assert len(calls) == 1

    applied = runner.invoke(
        app,
        [
            "reconcile-stale-canonical-turn",
            "--turn-ref",
            turn_ref,
            "--apply",
            "--confirm-staging-only",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["mode"] == "apply"
    assert calls[-1]["apply"] is True


def test_canonical_turn_reconciliation_refuses_outside_staging(monkeypatch) -> None:
    monkeypatch.setattr(environments, "is_staging", lambda: False)

    result = CliRunner().invoke(
        app,
        [
            "reconcile-stale-canonical-turn",
            "--turn-ref",
            str(TypeID("conversation_turn")),
        ],
    )

    assert result.exit_code == 2
    assert "staging environment is required" in result.output
