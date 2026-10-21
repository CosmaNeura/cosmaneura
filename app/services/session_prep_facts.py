"""Closed, exact-source observations for the Session Prep factual sidecar.

Dates describe the reviewed note, never an inferred measurement or start date.
Exact quotation validates provenance, not clinical truth or entailment. This
module neither decides what the patient currently takes nor changes the chart.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from app.services.session_prep_note_sources import ReviewedNoteSelection

MAX_FACTS = 48
MAX_QUOTE_CHARACTERS = 1200
MAX_FACT_RESPONSE_CHARACTERS = 80_000
Ref = Annotated[str, Field(pattern=r"^[A-Za-z0-9._~-]{8,160}$")]
Kind = Literal["medication", "order", "vital"]
State = Literal[
    "planned",
    "documented_taking",
    "documented_continued",
    "documented_started",
    "documented_prescribed",
    "documented_listed",
    "documented_not_taking",
    "stopped",
    "superseded",
    "ordered",
    "resulted",
    "cancelled",
    "not_ordered",
    "order_not_documented",
    "result_not_documented",
    "measured",
]


class FactEvidenceError(ValueError):
    """Value-free rejection; never put note text in an exception or log."""


class _SourceInputs(TypedDict):
    source_ref: str
    source_version: int
    section: str
    quote: str


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FactCandidate(_Closed):
    """An untrusted extraction proposal, not accepted clinical content."""

    kind: Kind
    subject: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, min_length=1, max_length=120)
    state: State
    setting: Literal["office", "home", "unspecified"] | None = None
    source_ref: Ref
    source_version: int = Field(ge=1, le=2_147_483_647)
    section: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARACTERS, repr=False)

    @model_validator(mode="after")
    def kind_matches_state(self) -> FactCandidate:
        states = {
            "medication": {
                "planned",
                "documented_taking",
                "documented_continued",
                "documented_started",
                "documented_prescribed",
                "documented_listed",
                "documented_not_taking",
                "stopped",
                "superseded",
            },
            "order": {
                "ordered",
                "resulted",
                "cancelled",
                "not_ordered",
                "order_not_documented",
                "result_not_documented",
            },
            "vital": {"measured"},
        }
        if self.state not in states[self.kind] or (
            (self.kind == "vital") != (self.setting is not None)
        ):
            raise ValueError("Fact shape is inconsistent")
        if self.kind == "vital" and self.value is None:
            raise ValueError("A measurement requires an exact value")
        return self


class FactSource(_Closed):
    source_ref: Ref
    source_version: int = Field(ge=1, le=2_147_483_647)
    # Receipts are JSON objects; accept their ISO datetime wire representation
    # while retaining strict strings/numbers and requiring an explicit offset.
    source_at: datetime = Field(strict=False)

    @model_validator(mode="after")
    def dated_source(self) -> FactSource:
        if self.source_at.tzinfo is None or self.source_at.utcoffset() is None:
            raise ValueError("A reviewed source requires a timezone")
        return self


class SourceEvidence(FactSource):
    section: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARACTERS, repr=False)
    start: int = Field(ge=0, le=18_000)
    end: int = Field(ge=1, le=18_000)

    @model_validator(mode="after")
    def bounded_span(self) -> SourceEvidence:
        if self.source_at.tzinfo is None or self.end - self.start != len(self.quote):
            raise ValueError("Evidence span is inconsistent")
        return self


class DocumentedFact(_Closed):
    fact_ref: Ref
    kind: Kind
    subject: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, min_length=1, max_length=120)
    state: State
    setting: Literal["office", "home", "unspecified"] | None
    evidence: SourceEvidence
    # These are observation links, not writes to a medication/order record.
    supersedes: list[Ref] = Field(default_factory=list, max_length=MAX_FACTS)
    correction_evidence: SourceEvidence | None = None

    @model_validator(mode="after")
    def shape_matches_observation(self) -> DocumentedFact:
        FactCandidate(
            kind=self.kind,
            subject=self.subject,
            value=self.value,
            state=self.state,
            setting=self.setting,
            source_ref=self.evidence.source_ref,
            source_version=self.evidence.source_version,
            section=self.evidence.section,
            quote=self.evidence.quote,
        )
        return self


class StructuredFactSet(_Closed):
    schema_version: Literal["1.0.0"] = "1.0.0"
    coverage: Literal["bounded_explicit_mentions"] = "bounded_explicit_mentions"
    source_count: int = Field(ge=0, le=5)
    selection_limited: bool
    facts_limited: bool
    sources: list[FactSource] = Field(max_length=5)
    facts: list[DocumentedFact] = Field(max_length=MAX_FACTS)

    @model_validator(mode="after")
    def links_are_closed(self) -> StructuredFactSet:
        if len(self.model_dump_json()) > MAX_FACT_RESPONSE_CHARACTERS:
            raise ValueError("Fact output exceeds its aggregate bound")
        sources = {source.source_ref: source for source in self.sources}
        if len(sources) != len(self.sources) or len(sources) != self.source_count:
            raise ValueError("Reviewed sources must be unique and counted exactly")
        by_ref = {fact.fact_ref: fact for fact in self.facts}
        if len(by_ref) != len(self.facts):
            raise ValueError("Fact references must be unique")
        for fact in self.facts:
            for evidence in (fact.evidence, fact.correction_evidence):
                if evidence is None:
                    continue
                source = sources.get(evidence.source_ref)
                if source is None or (
                    source.source_version != evidence.source_version
                    or source.source_at != evidence.source_at
                ):
                    raise ValueError("Fact evidence must match its reviewed source")
            if len(fact.supersedes) != len(set(fact.supersedes)):
                raise ValueError("Correction references must be unique")
            for ref in fact.supersedes:
                prior = by_ref.get(ref)
                if prior is None or (
                    prior.kind != fact.kind
                    or prior.subject.casefold() != fact.subject.casefold()
                    or not _observation_precedes(prior, fact)
                ):
                    raise ValueError("A correction must cite an earlier matching fact")
            if bool(fact.supersedes) != (fact.correction_evidence is not None):
                raise ValueError("Corrections require exact dated evidence")
            if fact.correction_evidence is not None and (
                fact.correction_evidence.source_ref != fact.evidence.source_ref
                or fact.correction_evidence.source_version
                != fact.evidence.source_version
                or fact.correction_evidence.source_at != fact.evidence.source_at
            ):
                raise ValueError("Correction evidence must match its observation")
        return self


def _fold(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_fact_candidates(
    candidates: list[FactCandidate], selection: ReviewedNoteSelection
) -> StructuredFactSet:
    """Check proposals against exact selected revisions and derive all dates.

    Statuses remain *documented observations*. A source quote alone cannot prove
    that a patient started therapy or a laboratory result is clinically valid.
    """
    if len(candidates) > MAX_FACTS or len(selection.sources) > 5:
        raise FactEvidenceError("fact_bound_exceeded")
    sources = {source.source_ref: source for source in selection.sources}
    if len(sources) != len(selection.sources):
        raise FactEvidenceError("fact_source_invalid")
    facts = []
    for candidate in candidates:
        source = sources.get(candidate.source_ref)
        text = source.content.get(candidate.section) if source is not None else None
        if (
            source is None
            or source.source_version != candidate.source_version
            or text is None
            or candidate.quote not in text
            or _fold(candidate.subject) not in _fold(candidate.quote)
            or (candidate.value is not None and candidate.value not in candidate.quote)
        ):
            raise FactEvidenceError("fact_evidence_invalid")
        if (
            not _safe_enclosing_context(candidate.section, text)
            or candidate.quote not in tuple(_segments(text))
            or (
                candidate.kind == "medication"
                and _medication_observation(candidate.quote, candidate.section)
                != (candidate.subject, candidate.value, candidate.state)
            )
            or (
                candidate.kind == "order"
                and (candidate.subject, candidate.state)
                not in _order_observations(candidate.quote)
            )
            or (
                candidate.kind == "vital"
                and _pressure_observation(candidate.quote)
                != (candidate.subject, candidate.value, candidate.setting)
            )
        ):
            raise FactEvidenceError("fact_status_evidence_invalid")
        # Locate the whole selected segment, not an identical substring inside
        # an earlier sentence. Repeated identical whole segments deduplicate.
        start = next(
            start for quote, start in _segment_spans(text) if quote == candidate.quote
        )
        fingerprint = "|".join(
            [
                candidate.source_ref,
                str(candidate.source_version),
                candidate.section,
                str(start),
                candidate.kind,
                candidate.subject,
                candidate.state,
                candidate.value or "",
                candidate.setting or "",
            ]
        )
        fact = DocumentedFact(
            fact_ref="fact_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32],
            kind=candidate.kind,
            subject=candidate.subject,
            value=candidate.value,
            state=candidate.state,
            setting=candidate.setting,
            evidence=SourceEvidence(
                source_ref=source.source_ref,
                source_version=source.source_version,
                source_at=source.source_at,
                section=candidate.section,
                quote=candidate.quote,
                start=start,
                end=start + len(candidate.quote),
            ),
        )
        if not any(previous.fact_ref == fact.fact_ref for previous in facts):
            facts.append(fact)
    facts.sort(
        key=lambda fact: (
            fact.evidence.source_at,
            fact.evidence.source_ref,
            fact.evidence.section,
            fact.evidence.start,
            fact.kind,
            fact.subject,
        )
    )
    _link_explicit_corrections(facts, sources)
    result = StructuredFactSet(
        source_count=len(sources),
        selection_limited=selection.truncated,
        facts_limited=False,
        sources=[
            FactSource(
                source_ref=source.source_ref,
                source_version=source.source_version,
                source_at=source.source_at,
            )
            for source in sorted(
                sources.values(), key=lambda item: (item.source_at, item.source_ref)
            )
        ],
        facts=facts,
    )
    if len(result.model_dump_json()) > MAX_FACT_RESPONSE_CHARACTERS:
        raise FactEvidenceError("fact_bound_exceeded")
    return result


_BLOOD_PRESSURE = re.compile(
    r"(?P<subject>blood\s+pressure|BP)\s*(?:readings?|range)?\s*[:=]?\s*"
    r"(?P<value>\d{2,3}(?:\s*[-–]\s*\d{2,3})?\s*/\s*"
    r"\d{2,3}(?:\s*[-–]\s*\d{2,3})?(?:\s*mm\s*Hg)?)",
    re.I,
)
_DOSE = r"\d+(?:\.\d+)?\s*(?:mcg|mg|g|mL|units?)\b"
_MEDICATION = re.compile(
    rf"(?P<subject>[A-Za-z][A-Za-z-]{{1,39}})\s+(?P<value>{_DOSE})",
    re.I,
)
_DOSE_PREFIX_WORDS = frozenset(
    {
        "a",
        "an",
        "at",
        "by",
        "continue",
        "decrease",
        "dose",
        "doses",
        "from",
        "increase",
        "instead",
        "is",
        "medication",
        "of",
        "on",
        "planned",
        "previous",
        "prior",
        "earlier",
        "current",
        "new",
        "old",
        "last",
        "next",
        "start",
        "stop",
        "superseded",
        "supersedes",
        "taking",
        "the",
        "to",
        "was",
        "were",
    }
)
_LAB = re.compile(
    r"\b(?:CBC|complete blood count|potassium|creatinine|K/Cr|"
    r"lipid panel|CMP|comprehensive metabolic panel|BMP|basic metabolic panel|"
    r"HbA1c|A1c|TSH|urinalysis)\b",
    re.I,
)


_MARKDOWN_LIST_PREFIX = re.compile(
    r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)])(?: {1,4}|\t)(?=\S)"
)


def _source_blocks(text: str):
    """Keep every payload/continuation; only known list markers are syntax.

    Ordinary unmarked prose is never split at a newline. A list item continues
    through following lines until another supported marker starts a new item.
    Marker-only lines, code indentation and inline pseudo-markers stay as data.
    """
    start = offset = 0
    for line in text.splitlines(keepends=True):
        marker = _MARKDOWN_LIST_PREFIX.match(line)
        if marker is not None:
            if offset > start:
                yield text[start:offset], start
            start = offset + marker.end()
        offset += len(line)
    if start < len(text):
        yield text[start:], start


def _segment_spans(text: str):
    for block, offset in _source_blocks(text):
        for quote, start in _sentence_spans(block):
            yield quote, offset + start


def _sentence_spans(text: str):
    # Semicolons/newlines retain enclosing qualifiers rather than manufacture
    # standalone patient assertions. Include punctuation-only and oversized
    # units so context validation cannot silently lose nonblank source text.
    lines = list(re.finditer(r"[^\r\n]+", text))
    if len(lines) > 1 and all(_supported_unit(line.group().strip()) for line in lines):
        # Newline boundaries are safe only when every complete line has a
        # closed independent meaning. An unknown continuation stays attached.
        for line in lines:
            for quote, start in _sentence_spans(line.group()):
                yield quote, line.start() + start
        return
    for match in re.finditer(r"(?:(?:\d\.\d)|[^.!?])+[.!?]?|[.!?]+", text):
        quote = match.group().strip()
        if quote:
            start = match.start() + len(match.group()) - len(match.group().lstrip())
            parts = list(re.finditer(r"[^;]+", quote))
            # Only split a semicolon when EVERY part is a complete supported
            # assertion and its enclosing unit is within the evidence bound.
            if (
                len(quote) <= MAX_QUOTE_CHARACTERS
                and 1 < len(parts) == quote.count(";") + 1
                and all(_supported_unit(part.group().strip()) for part in parts)
            ):
                for part in parts:
                    yield (
                        part.group().strip(),
                        start
                        + part.start()
                        + len(part.group())
                        - len(part.group().lstrip()),
                    )
            else:
                yield quote, start


def _segments(text: str):
    for quote, _ in _segment_spans(text):
        yield quote


_NEGATION = re.compile(
    r"\b(?:no|not|never|neither|nor|without|denies|denied|deny|declines|declined|"
    r"refuses|refused)\b|\b\w+n['’]t\b",
    re.I,
)
_AMBIGUOUS = re.compile(
    r"\b(?:but|however|although|unless|if|hypothetical|example|would|could|"
    r"might|may|consider|considered|discussed|uncertain|possibly|whether)\b",
    re.I,
)
_NOT_TAKING = re.compile(
    r"\b(?:not (?:currently |actually )?taking|never started|has not started|"
    r"(?:denies|denied|deny) (?:currently |actually )?taking)\b",
    re.I,
)


_UNASSERTED = re.compile(
    r"\b(?:unknown|unclear|unconfirmed|unverified|uncertain|question|possibly|"
    r"potential|suspected|mistake|incorrect|erroneous|error|historical|formerly|"
    r"previously|used to|in the past|last week|last month|before|after|"
    r"family|mother|father|parent|husband|wife|spouse|son|daughter|brother|sister|"
    r"friend|caregiver|another patient)\b",
    re.I,
)
_MED_PREDICATES: dict[State, str] = {
    "documented_taking": r"(?:currently )?(?:taking|takes)|has been taking",
    "documented_continued": r"continue|continues|continued|remain on",
    "planned": r"planned|plan to take|plan to start|start|recommended|to take|will take",
    "documented_started": r"started|began taking",
    "documented_prescribed": r"prescribed",
    "stopped": r"stopped|discontinued",
    "superseded": r"superseded|retracted",
    "documented_not_taking": _NOT_TAKING.pattern.removeprefix(r"\b").removesuffix(
        r"\b"
    ),
}


def _medication_mentions(quote: str):
    return [
        match
        for match in _MEDICATION.finditer(quote)
        if match["subject"].casefold() not in _DOSE_PREFIX_WORDS
    ]


def _asserted_medication(lower: str, match: re.Match, state: State) -> bool:
    subject_dose = re.escape(_fold(match["subject"] + " " + match["value"]))
    predicate = _MED_PREDICATES[state]
    prefix = r"^(?:(?:correction|plan|medications?): )?(?:patient (?:(?:is|was|reports|reported) )?)?"
    # Active "A supersedes B" does not say A was superseded.
    frequency = r"(?: (?:daily|once daily|twice daily))?"
    if state != "superseded" and re.fullmatch(
        prefix + rf"(?:{predicate}) {subject_dose}{frequency}[.!]?", lower
    ):
        return True
    frequency = r"(?: (?:daily|once daily|twice daily))?"
    return bool(
        re.fullmatch(
            rf"{subject_dose}{frequency} (?:is |was |has been )?(?:{predicate})[.!]?",
            lower,
        )
    )


_CLINICAL_SECTIONS = frozenset(
    {
        "medication",
        "medications",
        "current medications",
        "medication reconciliation",
        "medicationreconciliation",
        "plan",
        "assessment",
        "assessment and plan",
        "assessment plan",
        "subjective",
        "objective",
        "hpi",
        "history of present illness",
        "physical exam",
        "physical examination",
        "vitals",
        "vital signs",
        "vitalsigns",
        "orders",
        "labs",
        "laboratory",
        "lab results",
        "laboratory results",
    }
)
_SOAP_SECTIONS = {
    "s": "subjective",
    "o": "objective",
    "a": "assessment",
    "p": "plan",
}


def _clinical_section_title(section: str) -> str:
    title = section.strip().casefold()
    # One known legacy generated heading; its date is literal source text,
    # never provenance. Do not strip dates from arbitrary custom headings.
    if title == "labs - fasting, 02/12/2026":
        return "labs"
    soap = re.fullmatch(
        r"([soap])[ \t]*-[ \t]*(subjective|objective|assessment|plan)", title
    )
    if soap is not None and _SOAP_SECTIONS[soap[1]] == soap[2]:
        return soap[2]
    return re.sub(r"[\W_]+", " ", title).strip()


_FOREIGN_CONTEXT = re.compile(
    r"\b(?:neighbou?r|visitor|roommate|coworker|colleague|cousin|aunt|uncle|"
    r"grandparent|grandmother|grandfather|acquaintance|other patients?|"
    r"another person|other person|someone else|somebody else|belongs to|belonged to)\b|"
    r"\b(?:it|this|that) (?:is|was) \S+['’]s\b",
    re.I,
)
_CODE_INDENTATION = re.compile(r"^(?: {4}|[ \t]*\t)")


def _has_code_indentation(text: str) -> bool:
    # Inspect raw lines before strip/folding can turn sample code into a chart
    # assertion. Treat ambiguous indented continuations conservatively too.
    return any(
        line.strip() and _CODE_INDENTATION.match(line) for line in text.splitlines()
    )


def _safe_enclosing_context(section: str, text: str) -> bool:
    """Narrow section authority plus whole-section context before fragmentation.

    Omission is intentional when recognized foreign/hypothetical/conditional or
    uncertain wording qualifies a fragment. This is not an exhaustive chart read.
    """
    title = _clinical_section_title(section)
    if (
        title not in _CLINICAL_SECTIONS
        or _has_code_indentation(text)
        or "?" in text
        or (_AMBIGUOUS.search(text) or _FOREIGN_CONTEXT.search(text))
    ):
        return False
    units = list(_segments(text))
    # Unknown surrounding prose can qualify an otherwise valid-looking patient
    # fragment. Accept only wholly supported assertion/correction context.
    if not units or not all(
        len(unit) <= MAX_QUOTE_CHARACTERS
        and (not _UNASSERTED.search(unit) or _medication_correction(unit) is not None)
        and _supported_unit(unit)
        for unit in units
    ):
        return False
    # A same-section office absence cannot independently qualify a positive
    # office measurement. Home readings plus office absence remain compatible.
    office_absent = any(_office_absence(unit) for unit in units)
    return not (
        office_absent
        and any(
            (observation := _pressure_observation(unit)) is not None
            and observation[2] == "office"
            for unit in units
        )
    )


def _medication_state(quote: str, section: str) -> State | None:
    if _medication_correction(quote) is not None:
        return "documented_continued"
    lower = _fold(quote)
    if "?" in quote or _AMBIGUOUS.search(lower) or _UNASSERTED.search(lower):
        return None
    mentions = _medication_mentions(quote)
    if len(mentions) != 1 or len(re.findall(_DOSE, quote, re.I)) != 1:
        return None
    mention = mentions[0]
    negative = _NOT_TAKING.search(lower)
    if negative:
        remainder = lower[: negative.start()] + lower[negative.end() :]
        # A second predicate/negation can describe a different time or actor.
        if not _NEGATION.search(remainder) and not re.search(
            r"\b(?:taking|started|stopped|discontinued|continue|continues|continued|"
            r"prescribed|supersedes|superseded)\b",
            remainder,
        ):
            return (
                "documented_not_taking"
                if _asserted_medication(lower, mention, "documented_not_taking")
                else None
            )
        return None
    if _NEGATION.search(lower):
        return None
    predicates: list[tuple[State, str]] = [
        ("stopped", r"\b(?:stopped|discontinued)\b"),
        ("documented_continued", r"\b(?:continue|continues|continued|remain on)\b"),
        ("planned", r"\b(?:planned|plan to|to take|will take|recommended|start)\b"),
        ("documented_started", r"\b(?:started|began taking)\b"),
        ("documented_taking", r"\b(?:taking|currently takes|has been taking)\b"),
        ("superseded", r"\b(?:superseded|retracted)\b"),
        ("documented_prescribed", r"\bprescribed\b"),
    ]
    states: list[State] = [
        state for state, pattern in predicates if re.search(pattern, lower)
    ]
    # "Began taking" is one predicate, not an ambiguous taking/start pair.
    if "documented_started" in states and "documented_taking" in states:
        states.remove("documented_taking")
    if len(states) == 1:
        return states[0] if _asserted_medication(lower, mention, states[0]) else None
    if states:
        return None
    if re.search(r"medication|prescription", section, re.I) and re.fullmatch(
        re.escape(_fold(mention["subject"] + " " + mention["value"]))
        + r"(?: (?:daily|once daily|twice daily))?(?: \((?:new this visit|prior to this visit|patient-reported)\))?[.!]?",
        lower,
    ):
        return "documented_listed"
    return None


def _observation_precedes(prior: DocumentedFact, later: DocumentedFact) -> bool:
    a, b = prior.evidence, later.evidence
    return a.source_at < b.source_at or (
        a.source_at == b.source_at
        and a.source_ref == b.source_ref
        and a.source_version == b.source_version
        and a.section == b.section
        and a.end <= b.start
    )


def _explicit_medication_correction(
    fact: DocumentedFact, prior: DocumentedFact, text: str
) -> bool:
    if prior.value is None or prior.value == fact.value:
        return False
    explicit = _medication_correction(fact.evidence.quote)
    if explicit is not None:
        return (
            _fold(explicit["subject"]) == _fold(fact.subject) == _fold(prior.subject)
            and explicit["value"] == fact.value
            and re.sub(r"\s+", "", explicit["old_dose"]).casefold()
            == re.sub(r"\s+", "", prior.value).casefold()
        )
    quotes = list(_segments(text))
    try:
        index = quotes.index(fact.evidence.quote)
    except ValueError:
        return False
    if index + 1 >= len(quotes):
        return False
    correction = _fold(quotes[index + 1])
    # "This" is bound only to the immediately preceding asserted observation.
    # Unsupported subject, question, uncertainty or suffix cannot become a link.
    subject = re.escape(_fold(fact.subject))
    dose = re.escape(_fold(prior.value))
    return bool(
        re.fullmatch(
            rf"this supersedes (?:the )?(?:previous )?(?:{subject} )?{dose} (?:plan|dose)[.!]?",
            correction,
        )
    )


def _link_explicit_corrections(facts: list[DocumentedFact], sources: dict) -> None:
    for index, fact in enumerate(facts):
        text = sources[fact.evidence.source_ref].content[fact.evidence.section]
        if len(text) > MAX_QUOTE_CHARACTERS:
            continue
        medication_correction = fact.kind == "medication" and fact.state in {
            "documented_continued",
            "documented_taking",
        }
        order_cancellation = fact.kind == "order" and fact.state == "cancelled"
        if not (medication_correction or order_cancellation):
            continue
        prior_facts = [
            prior
            for prior in facts[:index]
            if prior.kind == fact.kind
            and prior.subject.casefold() == fact.subject.casefold()
            and _observation_precedes(prior, fact)
            and (
                (order_cancellation and prior.state == "ordered")
                or (
                    medication_correction
                    and prior.state
                    in {"planned", "documented_prescribed", "documented_listed"}
                    and _explicit_medication_correction(fact, prior, text)
                )
            )
        ]
        if prior_facts:
            # A singular previous plan/order does not establish correction of
            # every historical occurrence of the same drug/test.
            fact.supersedes = [prior_facts[-1].fact_ref]
            fact.correction_evidence = SourceEvidence(
                source_ref=fact.evidence.source_ref,
                source_version=fact.evidence.source_version,
                source_at=fact.evidence.source_at,
                section=fact.evidence.section,
                quote=text,
                start=0,
                end=len(text),
            )


def _is_measured_pressure(quote: str, value: str | None) -> bool:
    pressure = _BLOOD_PRESSURE.search(quote)
    return bool(
        pressure
        and pressure["value"] == value
        and "?" not in quote
        and not _UNASSERTED.search(quote)
        and not _NEGATION.search(quote)
        and not _AMBIGUOUS.search(quote)
        and not re.search(r"\b(goal|target|desired)\b", _fold(quote))
        and len(list(_BLOOD_PRESSURE.finditer(quote))) == 1
        and _fold(quote[: pressure.start()]).rstrip(":")
        in {
            "",
            "office",
            "home",
            "measured",
            "office measured",
            "home measured",
            "patient reports",
            "patient reports home",
            "patient-reported home",
        }
        and re.fullmatch(
            r"(?: ?(?:measured|recorded|at home|in office)| ?\(patient-reported\))?[.!]?",
            quote[pressure.end() :].strip(),
            re.I,
        )
        and (
            "(patient-reported)" not in quote.casefold()
            or "home" in _fold(quote[: pressure.start()])
        )
    )


def _pressure_setting(quote: str) -> Literal["office", "home", "unspecified"]:
    lower = _fold(quote)
    return (
        "office"
        if "office" in lower
        else ("home" if "home" in lower else "unspecified")
    )


def _order_states(quote: str) -> list[State]:
    labs = list(_LAB.finditer(quote))
    if len(labs) != 1:
        return []
    lower = _fold(quote)
    lab = re.escape(_fold(labs[0].group()))
    # Closed whole assertions prevent evidence absence, other actors, alleged
    # status and unrelated clause predicates from becoming physical order state.
    patterns: list[tuple[State, str]] = [
        (
            "ordered",
            rf"(?:{lab}(?: order)? (?:is |was )?ordered|{lab} order placed|ordered {lab})",
        ),
        ("cancelled", rf"{lab}(?: order)? (?:is |was )?(?:cancelled|canceled)"),
        (
            "not_ordered",
            rf"{lab} (?:was |is |has been )?(?:not|never) (?:actually )?ordered",
        ),
        (
            "order_not_documented",
            rf"(?:{lab} orders? (?:is |was |were )?not documented|no {lab} orders? (?:was |were )?documented)",
        ),
        (
            "result_not_documented",
            rf"(?:{lab} results? (?:is |was |were )?not documented|no {lab} results? (?:is |are |was |were )?documented|{lab} results? unavailable|no {lab} results? available)",
        ),
        ("resulted", rf"(?:{lab} (?:has |was )?resulted|{lab} results? available)"),
    ]
    return [
        state for state, pattern in patterns if re.fullmatch(pattern + r"[.!]?", lower)
    ]


def _complete_fragment(quote: str) -> bool:
    return bool(
        _medication_observation(quote, "Medications")
        or _order_observations(quote)
        or _pressure_observation(quote)
    )


_MEDICATION_CORRECTION = re.compile(
    rf"(?P<subject>[A-Za-z][A-Za-z-]{{1,39}})\s+(?P<value>{_DOSE})"
    rf"(?:\s+(?:daily|once daily|twice daily))?\s+\(continue;\s*"
    rf"supersedes (?:the )?previous erroneous instruction to increase to "
    rf"(?P<old_dose>{_DOSE})\)[.!]?",
    re.I,
)


def _medication_correction(quote: str) -> re.Match[str] | None:
    match = _MEDICATION_CORRECTION.fullmatch(quote.strip())
    if (
        match is None
        or match["subject"].casefold() in _DOSE_PREFIX_WORDS
        or (
            _UNASSERTED.search(match["subject"])
            or _FOREIGN_CONTEXT.search(match["subject"])
            or _AMBIGUOUS.search(match["subject"])
        )
    ):
        return None
    if (
        re.sub(r"\s+", "", match["value"]).casefold()
        == re.sub(r"\s+", "", match["old_dose"]).casefold()
    ):
        return None
    return match


def _medication_observation(quote: str, section: str) -> tuple[str, str, State] | None:
    correction = _medication_correction(quote)
    if correction is not None:
        return correction["subject"], correction["value"], "documented_continued"
    state = _medication_state(quote, section)
    mentions = _medication_mentions(quote)
    if state is not None and len(mentions) == 1:
        return mentions[0]["subject"], mentions[0]["value"], state
    return None


_HOME_PRESSURE_PROSE = re.compile(
    r"(?:The )?patient reports home (?P<subject>blood pressures) ranging from "
    r"(?P<value>\d{2,3} to \d{2,3} systolic and \d{2,3} to \d{2,3} diastolic) "
    r"since starting [A-Za-z][A-Za-z-]{1,39}[.!]?",
    re.I,
)
_HOME_PRESSURE_BETWEEN = re.compile(
    r"Patient reports home (?P<subject>blood pressure) readings between "
    r"(?P<value>\d{2,3} and \d{2,3}/\d{2,3} to \d{2,3})[.!]?",
    re.I,
)


def _pressure_observation(
    quote: str,
) -> tuple[str, str, Literal["office", "home", "unspecified"]] | None:
    for pattern in (_HOME_PRESSURE_PROSE, _HOME_PRESSURE_BETWEEN):
        match = pattern.fullmatch(quote.strip())
        if match is not None:
            return match["subject"], match["value"], "home"
    pressure = _BLOOD_PRESSURE.search(quote)
    if pressure is not None and _is_measured_pressure(quote, pressure["value"]):
        return pressure["subject"], pressure["value"], _pressure_setting(quote)
    return None


def _order_observations(quote: str) -> list[tuple[str, State]]:
    # These whole clauses bind shared absence to the exact stated test set.
    # Imperative "Order CBC ..." remains unstructured, not a placed order.
    patterns: tuple[tuple[str, tuple[State, ...]], ...] = (
        (
            r"No (?P<a>potassium) or (?P<b>creatinine) results? (?:is |are )?documented[.!]?",
            ("result_not_documented",),
        ),
        (
            r"(?P<a>Potassium) and (?P<b>creatinine) tests (?:are |were )?not ordered;\s*no results documented[.!]?",
            ("not_ordered", "result_not_documented"),
        ),
        (
            rf"(?P<a>{_LAB.pattern})(?: order)? (?:is |was )?(?:cancelled|canceled);\s*no (?:completion or )?results? documented[.!]?",
            ("cancelled", "result_not_documented"),
        ),
    )
    for pattern, states in patterns:
        match = re.fullmatch(pattern, quote.strip(), re.I)
        if match is not None:
            subjects = [match["a"]]
            if "b" in match.groupdict():
                subjects.append(match["b"])
            return [(subject, state) for subject in subjects for state in states]
    labs = list(_LAB.finditer(quote))
    return (
        [(labs[0].group(), state) for state in _order_states(quote)]
        if len(labs) == 1
        else []
    )


def _office_absence(quote: str) -> bool:
    return (
        re.fullmatch(
            r"(?:Office (?:blood pressure|BP):?\s*(?:Not documented|not measured)|"
            r"No office (?:blood pressure|BP) was measured or documented during this visit)[.!]?",
            quote.strip(),
            re.I,
        )
        is not None
    )


def _independent_context_unit(quote: str) -> bool:
    return (
        _office_absence(quote)
        or re.fullmatch(
            r"(?:No dizziness(?: reported)?|(?:The )?patient (?:reports no|denies) dizziness|"
            r"No known drug allergies|(?:The )?patient explicitly reports no known drug allergies|"
            r"Fatigue is improving|"
            r"This note was generated with AI assistance|"
            r"Greater than \d+(?:\.\d+)? minutes? was spent in patient care, including review of prior records, consultation with other providers, and documentation)[.!]?",
            quote.strip(),
            re.I,
        )
        is not None
    )


def _supported_unit(quote: str) -> bool:
    return bool(
        _complete_fragment(quote)
        or _independent_context_unit(quote)
        or re.fullmatch(
            rf"this supersedes (?:the )?(?:previous )?(?:[a-z][a-z-]{{1,39}} )?{_DOSE} (?:plan|dose)[.!]?",
            _fold(quote),
        )
    )


def project_explicit_facts(selection: ReviewedNoteSelection) -> StructuredFactSet:
    """Conservative bounded extraction; unrecognized prose is never guessed.

    This is not an exhaustive medication list, order ledger or reconciliation.
    Multiple subjects with mixed statuses in one sentence are left unstructured.
    Original notes remain the source for facts outside this narrow coverage.
    """
    candidates: list[FactCandidate] = []
    for source in selection.sources:
        for section, text in source.content.items():
            if not _safe_enclosing_context(section, text):
                continue
            for quote in _segments(text):
                common = _SourceInputs(
                    source_ref=source.source_ref,
                    source_version=source.source_version,
                    section=section,
                    quote=quote,
                )
                pressure = _pressure_observation(quote)
                if pressure is not None:
                    candidates.append(
                        FactCandidate(
                            **common,
                            kind="vital",
                            subject=pressure[0],
                            value=pressure[1],
                            state="measured",
                            setting=pressure[2],
                        )
                    )
                medication = _medication_observation(quote, section)
                if medication is not None:
                    candidates.append(
                        FactCandidate(
                            **common,
                            kind="medication",
                            subject=medication[0],
                            value=medication[1],
                            state=medication[2],
                        )
                    )
                for subject, order_state in _order_observations(quote):
                    candidates.append(
                        FactCandidate(
                            **common,
                            kind="order",
                            subject=subject,
                            state=order_state,
                        )
                    )
    limited = len(candidates) > MAX_FACTS
    result = validate_fact_candidates(candidates[:MAX_FACTS], selection)
    result.facts_limited = limited
    return result
