"""The Cosmas Engagement Engine (v2): text in, communication role(s) out.

Accepts any combination of intake free-text and visit-transcript text. The intake
text is read as the patient's *preferred* communication role; the visit transcript
is read (recalibrated to the local population, R3) as the *enacted* role. When both
are present the engine also returns the preference-enactment gap -- the actionable
signal of whether the patient is getting the involvement they want.

Headline confidence per channel folds three honest factors:

    confidence = separation x mean_indicator_confidence x evidence_factor

* separation        - how cleanly the indicators point at one role
* mean_indicator..  - how confident the rater was about the indicator estimates
* evidence_factor   - how much usable text we actually had
"""

from __future__ import annotations

from typing import List, Optional

from .calibration.baseline import RoleBaseline, default_baseline
from .llm_client import LLMClient, build_default_client
from .roles import ROLES, map_indicators_to_role
from .schemas import (
    CommunicationIndicators,
    ConfidenceBand,
    EvidenceSufficiency,
    IndicatorScore,
    PreferenceEnactmentGap,
    RoleDistributionItem,
    RolePrediction,
    ScoreRequest,
    ScoreResult,
    confidence_band,
)
from .scoring import estimate_indicators
from .version import ENGINE_VERSION, FRAMEWORK_VERSION, RUBRIC_VERSION

# Worry level (0-100) at/above which we raise the reassurance flag.
_CONCERN_FLAG_THRESHOLD = 60.0
# Decision-control gap (points) at/above which the preference-enactment gap is
# considered meaningful rather than noise.
_GAP_MODERATE = 18.0
_GAP_LARGE = 35.0


def _word_count(text: str) -> int:
    return len(text.split())


def _evidence_sufficiency(
    word_count: int, sources_used: List[str]
) -> EvidenceSufficiency:
    if word_count < 60:
        score = 0.30
    elif word_count < 200:
        score = 0.55
    elif word_count < 500:
        score = 0.72
    else:
        score = 0.85
    if len(sources_used) >= 2:
        score = min(0.95, score + 0.10)

    band = confidence_band(score)
    if band == ConfidenceBand.low:
        note = "Limited text; treat the estimate as a weak prior only."
    elif band == ConfidenceBand.moderate:
        note = "Moderate amount of text; estimate is indicative, not definitive."
    else:
        note = "Ample text for this channel."
    return EvidenceSufficiency(
        band=band,
        score=round(score, 3),
        word_count=word_count,
        sources_used=sources_used,
        note=note,
    )


def _indicators_model(ind: dict) -> CommunicationIndicators:
    return CommunicationIndicators(
        **{
            name: IndicatorScore(
                indicator=name,
                score=vals["score"],
                confidence=vals["confidence"],
                evidence=vals["evidence"],
            )
            for name, vals in ind.items()
        }
    )


class EngagementEngine:
    """Scores patient text into communication role(s) + the preference-enactment gap."""

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        enacted_baseline: Optional[RoleBaseline] = None,
    ) -> None:
        self.client: LLMClient = client or build_default_client()
        # Recalibration baseline applied to the ENACTED (in-encounter) read only;
        # the preferred (intake) read uses the neutral default. Override per practice.
        self.enacted_baseline: RoleBaseline = enacted_baseline or default_baseline()

    @classmethod
    def from_env(cls) -> "EngagementEngine":
        return cls(build_default_client())

    def _predict(
        self,
        text: str,
        channel: str,
        baseline: Optional[RoleBaseline],
        evidence_word_count: int,
    ) -> RolePrediction:
        ind = estimate_indicators(text, self.client)
        indicators = _indicators_model(ind)

        match = map_indicators_to_role(indicators.as_axis_dict(), baseline=baseline)
        role = ROLES[match.role_key]

        # Confidence: separation x mean indicator confidence (over the two mapping
        # axes) x evidence factor.
        axis_conf = [
            ind["decision_control"]["confidence"],
            ind["information_engagement"]["confidence"],
        ]
        mean_conf = sum(axis_conf) / len(axis_conf)
        ev = _evidence_sufficiency(evidence_word_count, [])  # per-channel volume
        evidence_factor = 0.5 + 0.5 * ev.score
        conf = round(match.separation_probability * mean_conf * evidence_factor, 3)

        distribution = sorted(
            (
                RoleDistributionItem(
                    role_key=k, role_name=ROLES[k].display_name, probability=p
                )
                for k, p in match.distribution.items()
            ),
            key=lambda d: d.probability,
            reverse=True,
        )

        return RolePrediction(
            channel=channel,
            role_key=role.key,
            role_name=role.display_name,
            cps_role=role.cps_role,
            short_label=role.short_label,
            summary=role.summary,
            confidence=conf,
            confidence_band=confidence_band(conf),
            runner_up_key=match.runner_up_key,
            runner_up_name=match.runner_up_name,
            distribution=distribution,
            indicators=indicators,
            provider_guidance=list(role.provider_guidance),
            cautions=list(role.cautions),
            concern_flag=indicators.expressed_concern.score >= _CONCERN_FLAG_THRESHOLD,
            baseline_source=(
                baseline.source if baseline is not None else "neutral_default"
            ),
        )

    def _gap(
        self, preferred: RolePrediction, enacted: RolePrediction
    ) -> PreferenceEnactmentGap:
        pref_dc = preferred.indicators.decision_control.score
        enac_dc = enacted.indicators.decision_control.score
        delta = round(enac_dc - pref_dc, 1)
        mag = abs(delta)
        if mag >= _GAP_LARGE:
            band = ConfidenceBand.high
        elif mag >= _GAP_MODERATE:
            band = ConfidenceBand.moderate
        else:
            band = ConfidenceBand.low

        if mag < _GAP_MODERATE:
            direction = "concordant"
            interp = (
                "The patient is engaging about as much as they appear to want; "
                "preferred and enacted roles align."
            )
        elif delta < 0:
            direction = "suppressed"
            interp = (
                "The patient appears to want more involvement than the encounter "
                "drew out -- consider offering options and inviting their view."
            )
        else:
            direction = "over_enacted"
            interp = (
                "The patient took on more decision control in the visit than their "
                "intake suggested they wanted -- consider checking they feel supported."
            )

        return PreferenceEnactmentGap(
            preferred_role_key=preferred.role_key,
            preferred_role_name=preferred.role_name,
            enacted_role_key=enacted.role_key,
            enacted_role_name=enacted.role_name,
            decision_control_gap=delta,
            direction=direction,
            magnitude_band=band,
            interpretation=interp,
        )

    def score(self, request: ScoreRequest) -> ScoreResult:
        intake = (request.intake_text or "").strip()
        visit = (request.visit_text or "").strip()

        inputs_used: List[str] = []
        preferred = enacted = None

        if intake:
            inputs_used.append("intake")
            preferred = self._predict(intake, "preferred", None, _word_count(intake))
        if visit:
            inputs_used.append("visit")
            enacted = self._predict(
                visit, "enacted", self.enacted_baseline, _word_count(visit)
            )

        if intake and visit:
            view = "preferred_and_enacted"
        elif intake:
            view = "preferred_only"
        else:
            view = "enacted_only"

        # Headline: enacted (in-encounter style) when available, else preferred.
        headline = enacted or preferred
        gap = self._gap(preferred, enacted) if (preferred and enacted) else None

        # Overall evidence sufficiency across whatever text was provided.
        total_words = _word_count(intake) + _word_count(visit)
        evidence = _evidence_sufficiency(total_words, inputs_used)

        conf_values = [
            p.indicators.decision_control.confidence for p in (preferred, enacted) if p
        ]
        conf_values += [
            p.indicators.information_engagement.confidence
            for p in (preferred, enacted)
            if p
        ]
        mean_conf = (
            round(sum(conf_values) / len(conf_values), 3) if conf_values else 0.0
        )

        return ScoreResult(
            patient_ref=request.patient_ref,
            role=headline,
            preferred_role=preferred,
            enacted_role=enacted,
            gap=gap,
            evidence_sufficiency=evidence,
            indicator_confidence_mean=mean_conf,
            inputs_used=inputs_used,
            view=view,
            model=self.client.name,
            engine_version=ENGINE_VERSION,
            framework_version=FRAMEWORK_VERSION,
            rubric_version=RUBRIC_VERSION,
        )


def score_text(
    intake_text: Optional[str] = None,
    visit_text: Optional[str] = None,
    *,
    patient_ref: Optional[str] = None,
    client: Optional[LLMClient] = None,
) -> ScoreResult:
    """Convenience wrapper for one-off scoring."""
    return EngagementEngine(client=client).score(
        ScoreRequest(
            intake_text=intake_text, visit_text=visit_text, patient_ref=patient_ref
        )
    )


__all__ = ["EngagementEngine", "score_text"]
