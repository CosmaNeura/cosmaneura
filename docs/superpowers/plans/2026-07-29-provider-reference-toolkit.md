# Provider Reference Toolkit — Implementation Plan

Date: 2026-07-29
Status: awaiting approval (no feature code written yet)
Origin: pilot feedback from a nutritionist user. Her manual workflow:
check drug-induced nutrient depletions (DIND), supplement/drug/nutrient
interactions, and adverse drug effects (many presenting symptoms are Rx side
effects; identifying the nutrient depletion usually improves them); verify
state-by-state eligibility to practice; interpret labs against both standard
and functional-medicine "optimal" ranges.

## 1. What her resources actually are (parsed 2026-07-29)

1. **Natural Medicines / NatMed Pro** (naturalmedicines.therapeuticresearch.com,
   TRC Healthcare): the clinical reference for natural products — 1,400+
   ingredient monographs, ~300k commercial products, evidence-rated
   effectiveness, an interaction checker, and a dedicated **Nutrient
   Depletions Tool** (which meds deplete which nutrients, with clinical-
   importance ratings). Subscription/login-gated. **Key fact: TRC licenses a
   RESTful API and embeddable tools for integration into CDSS/apps**
   (quote-based, per-organization; existing precedent: their vital.ly
   partnership). *Pharmacist's Letter* is a sibling TRC product
   (advisory/CE content) — less structured, lower integration value.
2. **Holistic Council nutrition-laws** (holisticcouncil.org/nutrition-laws):
   free public state-by-state guide to nutrition practice law, with per-state
   pages and a stable taxonomy: no law / title protection / certification /
   licensure with exclusive scope.
3. **Lamkin Clinic Lab Reference Library** (lamkinclinic.com): free web
   library of 110+ biomarkers with functional-medicine optimal ranges +
   interpretation, categorized (metabolic, thyroid, nutrients, …), authored
   by one DO. Copyrighted content.
4. **Fullscript "Optimal biomarker ranges" PDF**: marketing-tier guide of
   optimal vs standard ranges. Copyrighted.

Licensing reality that shapes everything: TRC content requires a paid API
license (business conversation, not code). Lamkin/Fullscript pages are
copyrighted — we link to them and let providers configure their own range
values; we do not ingest their text.

## 2. Design principles

- **License, don't scrape.** TRC via API license; Lamkin/Fullscript via
  deep links + provider-editable values; Holistic Council via linked,
  curated summaries with attribution.
- **Reference display + provider judgment**, never diagnosis — same clinical
  posture as notes/session prep. Flagged to the pilot cohort via the
  existing allowlist rails; PHI conventions unchanged (IDs-only logs, Azure
  OpenAI only, content stored like notes).
- **Reuse the rails we just built**: `patient_context` service, Session Prep
  alerts, settings plumbing, Celery job conventions, per-feature gates.

## 3. Architecture — four features, phased

### A. Medication & supplement registry (foundation — everything needs it)

New model `PatientMedication`: patient/doctor FKs, name, normalized code
(RxNorm CUI, nullable), kind (rx | otc | supplement), status
(active | stopped), source (transcript | intake | manual) + provenance,
timestamps. Extraction job (standard perform/queue convention) runs after
transcription/intake summarize: LLM extracts a structured med+supplement
list (JSON mode, offline test fallback), upserts rows as *unconfirmed*;
provider confirms/edits in a panel on the patient view. RxNorm lookup (free
NLM API) normalizes names→CUIs for anything downstream.

### B. DIND / interaction / adverse-effect checks

- **B1 — no license needed (immediate value):** a "Check medications" panel
  on the patient view: per-medication deep links into the provider's own
  NatMed subscription (she already pays for one), plus adverse-reaction and
  warnings sections pulled from **openFDA drug labels** (public domain) and
  supplement fact sheets from **NIH ODS** (public domain), rendered in-app.
- **B2 — TRC NatMed API (business action: license quote):**
  `app/services/natmed.py` client → nutrient-depletion + interaction
  endpoints. Celery job `check_medications(patient_id)` produces a
  structured `MedicationCheck`: depletions (with TRC clinical-importance
  ratings), supplement↔drug interactions, adverse effects matched against
  the registry. Cached per med-list hash; TRC attribution as required by
  license. Surfaced on the patient view **and folded into Session Prep
  alerts** ("possible B12 depletion — metformin, long-term") — this is
  exactly her symptom→Rx→depletion workflow, automated.

### C. Lab interpretation with dual ranges

- `LabResult` model + lab upload path (reuse the existing upload + LLM
  extraction conventions; labs arrive as PDFs) → structured biomarker
  values with units.
- `LabRangeSet` reference table: biomarker, unit, standard low/high,
  optimal low/high, citation URL. Seeded with a conservative default set;
  **provider-editable per practice** (this is where her Lamkin/Fullscript
  values go — entered or imported by *her*, linked to source, not copied by
  us). Per-biomarker "view source" deep links to Lamkin/Fullscript.
- Interpretation job: each value classified in-range / outside-standard /
  outside-optimal, with a one-line LLM context note citing which range set
  fired. Out-of-optimal flags feed Session Prep open threads.

### D. State practice eligibility (small, standalone)

Curated static dataset (`state → category, two-sentence summary, links to
the Holistic Council state page + statute`), a "states I practice in"
setting, and an eligibility card that shows the category with an explicit
"informational, not legal advice" disclaimer and links out. Quarterly
review chore documented in the dataset header. Needs legal review before
shipping summaries; v0 can be links-only.

### Quick win alongside: `nutritionist_client` terminology set — one enum
member + one catalog entry, exactly the extensibility the terminology
feature was built for.

## 4. PR breakdown (each small, flagged, independently reviewable)

1. **PR R1** — `PatientMedication` model + extraction job + confirm/edit UI
   (foundation; no external dependencies). ~500 LOC.
2. **PR R2** — Resource panel with NatMed deep links + provider resource
   settings; `nutritionist_client` terminology set. ~250 LOC.
3. **PR R3** — RxNorm normalization + openFDA/NIH ODS adverse-effect and
   supplement info rendering (public-domain data path). ~400 LOC.
4. **PR R4** — TRC NatMed API service + `MedicationCheck` job + UI + Session
   Prep alert integration. **Blocked on the TRC license.** ~500 LOC.
5. **PR R5** — `LabResult` model + upload/extraction path. ~450 LOC.
6. **PR R6** — `LabRangeSet` + dual-range evaluation + provider range editor
   + Session Prep integration. ~450 LOC.
7. **PR R7** — state eligibility dataset + settings + card (links-only until
   legal review clears summaries). ~250 LOC.

Recommended order: R1 → R2 → R3 (ship value while the TRC conversation
happens) → R5/R6 in parallel with R4 when the license lands. R7 anytime.

## 5. Open questions / non-code actions

1. **TRC NatMed API license** — commercial conversation with TRC
   (quote-based). Who owns it? Ask specifically about: nutrient-depletion +
   interaction endpoints, per-seat vs per-org pricing, attribution and
   display requirements, and whether Pharmacist's Letter content is in scope.
2. Confirm the pilot nutritionist has her own NatMed login (makes R2 deep
   links useful day one).
3. Legal review of state-law summaries (R7) — or ship links-only.
4. Which labs does she receive (LabCorp/Quest/other, PDF formats)? Drives
   the R5 extraction work.
5. BAA note: openFDA/NIH/RxNorm calls must send drug names only — never
   patient identifiers (they're not BAA'd endpoints). Enforced in code
   review; worth stating in each PR.
