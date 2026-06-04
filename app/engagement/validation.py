"""Validation harness for the engagement engine (the gate).

Compares the engine's *predicted* communication role (EngagementRole, from the visit
transcript) against the patient's *self-reported* control preference (CpsSelfReport,
the ground truth) for encounters where both exist.

This is what closes the "confident but unvalidated" gap: it measures whether a role
the engine is confident about actually matches what the patient says they want, and
tracks the preference-enactment concordance against the ~61% benchmark reported in the
Control Preferences Scale literature (Singh et al.).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional, cast

# Pooled preference-vs-experienced concordance from the CPS literature.
CPS_CONCORDANCE_BENCHMARK = 0.61


def compare_engine_vs_self_report(doctor_id: Optional[Any] = None) -> dict:
    """Summarize agreement between engine role and patient self-report.

    Returns a structured summary (safe to log / serialize). ``n_pairs`` is 0 until
    self-reports start landing, so this is callable any time during the pilot.
    """
    from app.models.cps_self_report import CpsSelfReport
    from app.models.engagement_role import EngagementRole

    er_conds = [EngagementRole.deleted_at == None]  # noqa: E711
    sr_conds = [CpsSelfReport.deleted_at == None]  # noqa: E711
    if doctor_id is not None:
        er_conds.append(EngagementRole.doctor_id == doctor_id)
        sr_conds.append(CpsSelfReport.doctor_id == doctor_id)

    engine_rows = cast(
        list[EngagementRole], list(EngagementRole.where(*er_conds).all())
    )
    report_rows = cast(list[CpsSelfReport], list(CpsSelfReport.where(*sr_conds).all()))

    # One self-report per appointment; keep the latest engine role per appointment.
    reports = {str(r.appointment_id): r for r in report_rows}
    engine_by_appt: dict[str, EngagementRole] = {}
    for e in engine_rows:
        engine_by_appt[str(e.appointment_id)] = e

    pairs = [(engine_by_appt[a], reports[a]) for a in reports if a in engine_by_appt]

    n = len(pairs)
    summary: dict = {
        "n_pairs": n,
        "benchmark": CPS_CONCORDANCE_BENCHMARK,
    }
    if n == 0:
        summary["note"] = "No engine/self-report pairs yet — collect CPS responses."
        return summary

    agree = 0
    confusion: Counter = Counter()  # (predicted_cps_role, reported_cps_role)
    per_role_total: Counter = Counter()
    per_role_agree: Counter = Counter()
    confidences = []
    for engine, report in pairs:
        predicted = engine.cps_role
        reported = report.preferred_cps_role.value
        confusion[(predicted, reported)] += 1
        per_role_total[reported] += 1
        confidences.append(engine.confidence)
        if predicted == reported:
            agree += 1
            per_role_agree[reported] += 1

    summary["agreement_rate"] = round(agree / n, 3)
    summary["mean_confidence"] = round(sum(confidences) / n, 3)
    summary["delta_vs_benchmark"] = round(agree / n - CPS_CONCORDANCE_BENCHMARK, 3)
    summary["by_reported_role"] = {
        role: {"n": per_role_total[role], "agree": per_role_agree[role]}
        for role in per_role_total
    }
    summary["confusion"] = {f"{p}->{r}": c for (p, r), c in confusion.items()}
    return summary


__all__ = ["CPS_CONCORDANCE_BENCHMARK", "compare_engine_vs_self_report"]
