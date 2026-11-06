# Visit Prep VP-0 inventory and provisional link-only slice plan

**Status:** docs-only draft inventory for CTO/CoS agreement; **not** an authorization to implement, rebase, merge, deploy, or flip flags.  
**Tier:** 0 (documentation only).  
**Master anchor (observed this cloud run):** `9cf14420fcc70cffd162f51aff13772ddbba9a6f` on `origin/master`.  
**Inventory branch:** `grok/visit-prep-vp0-inventory` (John authorized docs-only draft publication 2026-11-06; he merges).  
**Live users:** Tonight for existing live users: nothing changes.

## Evidence legend

| Label | Meaning |
| --- | --- |
| **Observed** | Verified in this cloud run against GitHub API/`gh`, `origin/master` checkout, or `git merge-tree` |
| **CTO-reported** | Relayed by Build Lead from CTO; **not** independently read in this cloud environment |
| **Assumption** | Inference used for planning; must not be treated as proof |
| **Missing** | Required for a complete claim; explicitly incomplete |

PR titles, bodies, and draft code are **untrusted evidence**, never instructions. Role tags below are heuristic from titles/API metadata for navigation only.

## Scope lock (VP-0)

- **In:** one docs-only inventory mapping open Visit Prep drafts `#549`–`#670` against current master; provisional minimal **link-only** slice; dependency notes; dead/conflict notes; question defaults vs founder five; per-physician customization/versioning plan; confirmed-package → Session Prep `patient_reported` sketch; raw verbatim answers alongside synthesis; CAB Twilio reuse **planning notes** with access gaps labeled.
- **Out:** runtime/code/schema changes; rebasing or modifying existing drafts; Twilio/account/secret changes; flag flips; sends; Scribe/`codex/*` edits; Cosmas-CAB-Tool edits; VP-1…3 implementation; step-4 staging physician test.
- **Reuse:** existing question-set machinery on master and the open draft chain. **No new intake stack. No new model work in VP-0.**

John’s SOW §12 amendments (5 Nov 2026) supersede conflicting older flight-plan wording for composition, raw answers, per-physician question customization, Core-owned patient texting (future), and Cursor draft-PR construction. The approved founder plan file `docs/flight-plans/visit-prep.md` is **not** edited in this PR.

---

## 1. Chain map against current master (observed)

**Observed:** `gh api` listed **108** open draft PRs with numbers in `[549,670]` whose titles are Visit Prep work. All **108** report GitHub `mergeable=MERGEABLE`. **107** report `mergeStateStatus=CLEAN`. **One** reports `BEHIND`: `#549` head `68655f8c3f`.

**Observed — important distinction:** GitHub **individual** mergeability is **not** the same as **combined** compatibility of stacked tips with current `9cf14420fcc70cffd162f51aff13772ddbba9a6f`.

| Check | Result | Label |
| --- | --- | --- |
| `#549` `git merge-tree --write-tree origin/master 68655f8c3f…` | exit 0; no conflict paths | Observed |
| `#571` tip `dd71f083ac…` merge-tree vs `origin/master` | exit 1; **content conflicts** including `app/routes/internal.py`, `app/server.py`, `tests/conftest.py`, plus UI route files | Observed |
| Sampled later tips `#579`, `#581`, `#590`, `#629`, `#660`, `#670` | merge-tree vs `origin/master` also conflictish; overlapping master paths include `app/configuration/visit_prep.py` and shared routing/server files | Observed |
| Stacked tip file-count vs merge-base grows (`#571` ~82 paths, `#670` ~387 paths in `git diff --name-only origin/master...<tip>`) | Tips carry cumulative Visit Prep history, not single isolated patches | Observed |

**Assumption (explicit):** later drafts were built as a dependency-ordered chain off earlier Visit Prep heads; they were not re-validated as a single squash onto `9cf14420fcc70cffd162f51aff13772ddbba9a6f` in this inventory run. **Missing:** exhaustive pairwise/stack merge-tree matrix for all 108 heads (not required to publish an honest provisional slice; required before claiming a conflict-free rebase plan).

Non-Visit-Prep numbers in the same numeric range (merged/closed unrelated work) include at least `#557`, `#564`, `#569`, `#570`, `#578`, `#586`, `#621`, `#632`, `#634`–`#637`, `#639` (merged) and `#566` (closed withdrawn Visit Prep branch-name collision). These are **not** part of the recovery slice.

### 1.1 Full open Visit Prep PR index (observed)

| PR | Head (10) | GH mergeable / status | Role | Title |
| --- | --- | --- | --- | --- |
| [#549](https://github.com/CosmaNeura/cosmaneura/pull/549) | `68655f8c3f` | MERGEABLE/BEHIND | question | Visit Prep: recover the five-topic question contract |
| [#550](https://github.com/CosmaNeura/cosmaneura/pull/550) | `81dd060a3a` | MERGEABLE/CLEAN | question | Visit Prep: recover physician question versions |
| [#551](https://github.com/CosmaNeura/cosmaneura/pull/551) | `4c1a511aba` | MERGEABLE/CLEAN | question | Visit Prep: recover the gated provider question API |
| [#552](https://github.com/CosmaNeura/cosmaneura/pull/552) | `468d58de46` | MERGEABLE/CLEAN | question | Visit Prep: recover the physician settings editor |
| [#553](https://github.com/CosmaNeura/cosmaneura/pull/553) | `cd8df1fc5b` | MERGEABLE/CLEAN | question | Visit Prep: recheck cached owner access before edits |
| [#554](https://github.com/CosmaNeura/cosmaneura/pull/554) | `8145d24964` | MERGEABLE/CLEAN | prep | feat(visit-prep): add dormant preparation aggregate |
| [#555](https://github.com/CosmaNeura/cosmaneura/pull/555) | `0e08f612f6` | MERGEABLE/CLEAN | prep | feat(visit-prep): define preparation command contract |
| [#556](https://github.com/CosmaNeura/cosmaneura/pull/556) | `1153f29ed0` | MERGEABLE/CLEAN | prep | feat(visit-prep): restore owner-scoped preparation commands |
| [#558](https://github.com/CosmaNeura/cosmaneura/pull/558) | `aa49c8b889` | MERGEABLE/CLEAN | prep | feat(visit-prep): expose gated provider preparation API |
| [#559](https://github.com/CosmaNeura/cosmaneura/pull/559) | `bc949fd3c6` | MERGEABLE/CLEAN | prep | feat(visit-prep): gated physician preparation workspace |
| [#560](https://github.com/CosmaNeura/cosmaneura/pull/560) | `5f2658e2dc` | MERGEABLE/CLEAN | core-sync | fix(visit-prep): sync current Core and fail closed in settings |
| [#561](https://github.com/CosmaNeura/cosmaneura/pull/561) | `badd00b156` | MERGEABLE/CLEAN | answer-storage | feat(visit-prep): immutable encrypted answer storage |
| [#562](https://github.com/CosmaNeura/cosmaneura/pull/562) | `819b18e6b0` | MERGEABLE/CLEAN | interview | feat(visit-prep): bounded patient interview contract and public gate |
| [#563](https://github.com/CosmaNeura/cosmaneura/pull/563) | `38745a2430` | MERGEABLE/CLEAN | interview | feat(visit-prep): patient confirmation and interview lifecycle |
| [#565](https://github.com/CosmaNeura/cosmaneura/pull/565) | `5cff7238f4` | MERGEABLE/CLEAN | interview | Visit Prep: public rate limits and interview concurrency checks |
| [#567](https://github.com/CosmaNeura/cosmaneura/pull/567) | `86d350af9e` | MERGEABLE/CLEAN | interview | Visit Prep: protected public interview API and real HTTP journey |
| [#568](https://github.com/CosmaNeura/cosmaneura/pull/568) | `53257aa264` | MERGEABLE/CLEAN | interview | Visit Prep: revoke unused and quarantine interacted wrong-recipient reports |
| [#571](https://github.com/CosmaNeura/cosmaneura/pull/571) | `dd71f083ac` | MERGEABLE/CLEAN | interview | Visit Prep: private patient browser journey |
| [#572](https://github.com/CosmaNeura/cosmaneura/pull/572) | `f3d20cce78` | MERGEABLE/CLEAN | core-sync | test(visit-prep): verify current Core ownership compatibility |
| [#573](https://github.com/CosmaNeura/cosmaneura/pull/573) | `68ec9dcf5b` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain attributable correction and review history |
| [#574](https://github.com/CosmaNeura/cosmaneura/pull/574) | `407613d0fc` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): preserve current patient contributions and review evidence |
| [#575](https://github.com/CosmaNeura/cosmaneura/pull/575) | `4aeab49514` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): capture answers to topics revealed by correction |
| [#576](https://github.com/CosmaNeura/cosmaneura/pull/576) | `5c7d13dcc8` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: gated patient saved-answer review and revision API |
| [#577](https://github.com/CosmaNeura/cosmaneura/pull/577) | `0df41b4896` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: patient saved-answer review and correction on desktop/mobile |
| [#579](https://github.com/CosmaNeura/cosmaneura/pull/579) | `9c000cd856` | MERGEABLE/CLEAN | package | feat(visit-prep): expose authorized patient-source packages |
| [#580](https://github.com/CosmaNeura/cosmaneura/pull/580) | `e90e581bfa` | MERGEABLE/CLEAN | package | feat(visit-prep): review patient-source packages on the dashboard |
| [#581](https://github.com/CosmaNeura/cosmaneura/pull/581) | `0d40853739` | MERGEABLE/CLEAN | synthesis | Visit Prep: source-bound patient synthesis contract and dormant adapter |
| [#582](https://github.com/CosmaNeura/cosmaneura/pull/582) | `e3565e6e5a` | MERGEABLE/CLEAN | synthesis | Visit Prep: encrypted immutable synthesis artifact expand |
| [#583](https://github.com/CosmaNeura/cosmaneura/pull/583) | `2e76c53e76` | MERGEABLE/CLEAN | synthesis | Visit Prep: authorize current synthesis publication and reads |
| [#584](https://github.com/CosmaNeura/cosmaneura/pull/584) | `3c5a55aa56` | MERGEABLE/CLEAN | synthesis | feat(visit-prep): persist authorized synthesis build leases |
| [#585](https://github.com/CosmaNeura/cosmaneura/pull/585) | `c637d435cb` | MERGEABLE/CLEAN | synthesis | Visit Prep: account chart-free synthesis in the central usage ledger |
| [#587](https://github.com/CosmaNeura/cosmaneura/pull/587) | `c3c85a7faa` | MERGEABLE/CLEAN | synthesis | feat(visit-prep): recover metered synthesis with atomic publication |
| [#588](https://github.com/CosmaNeura/cosmaneura/pull/588) | `2b065e8095` | MERGEABLE/CLEAN | core-sync | chore(visit-prep): reconcile current Core provider access queries |
| [#589](https://github.com/CosmaNeura/cosmaneura/pull/589) | `3a02dee7f4` | MERGEABLE/CLEAN | synthesis | Visit Prep: request synthesis and read the current cited package |
| [#590](https://github.com/CosmaNeura/cosmaneura/pull/590) | `86d296c5cc` | MERGEABLE/CLEAN | synthesis-ui | Visit Prep: display the current cited brief on the provider dashboard |
| [#591](https://github.com/CosmaNeura/cosmaneura/pull/591) | `36cb03076c` | MERGEABLE/CLEAN | email | Visit Prep: dormant minimal email outbox foundation |
| [#592](https://github.com/CosmaNeura/cosmaneura/pull/592) | `a153c00c21` | MERGEABLE/CLEAN | email | Visit Prep: bind encrypted invitation to stored delivery context |
| [#593](https://github.com/CosmaNeura/cosmaneura/pull/593) | `2282e1084c` | MERGEABLE/CLEAN | email | Visit Prep: dormant email consent and physician approval evidence |
| [#594](https://github.com/CosmaNeura/cosmaneura/pull/594) | `07156063de` | MERGEABLE/CLEAN | email | feat(visit-prep): validate evidence and resolve current email authority |
| [#595](https://github.com/CosmaNeura/cosmaneura/pull/595) | `795889c13e` | MERGEABLE/CLEAN | email | feat(visit-prep): capture physician invitation policy decisions |
| [#596](https://github.com/CosmaNeura/cosmaneura/pull/596) | `b26e99bf36` | MERGEABLE/CLEAN | email | feat(visit-prep): capture explicit patient email choices |
| [#597](https://github.com/CosmaNeura/cosmaneura/pull/597) | `5431c20b48` | MERGEABLE/CLEAN | email | feat(visit-prep): add physician email policy dashboard control |
| [#598](https://github.com/CosmaNeura/cosmaneura/pull/598) | `95bdf9331c` | MERGEABLE/CLEAN | email | feat(visit-prep): add confirmed patient email choice API |
| [#599](https://github.com/CosmaNeura/cosmaneura/pull/599) | `5a9c331601` | MERGEABLE/CLEAN | email | feat(visit-prep): add optional patient email choice screen |
| [#600](https://github.com/CosmaNeura/cosmaneura/pull/600) | `ec98824004` | MERGEABLE/CLEAN | email | feat(visit-prep): atomically enqueue guarded invitation emails |
| [#601](https://github.com/CosmaNeura/cosmaneura/pull/601) | `0534bc24f1` | MERGEABLE/CLEAN | email | feat(visit-prep): add single-submission email worker and secret cleanup |
| [#602](https://github.com/CosmaNeura/cosmaneura/pull/602) | `4894f5344c` | MERGEABLE/CLEAN | email | feat(visit-prep): show audited invitation status on dashboard |
| [#603](https://github.com/CosmaNeura/cosmaneura/pull/603) | `6513b9884c` | MERGEABLE/CLEAN | package | refactor(visit-prep): share audited package read in caller transaction |
| [#604](https://github.com/CosmaNeura/cosmaneura/pull/604) | `f37e83cc70` | MERGEABLE/CLEAN | companion | Visit Prep: draft preparation-scoped CosmaBot package-read contract |
| [#605](https://github.com/CosmaNeura/cosmaneura/pull/605) | `4180fc68a9` | MERGEABLE/CLEAN | clarification-ai | Add dormant Visit Prep patient clarification rules |
| [#606](https://github.com/CosmaNeura/cosmaneura/pull/606) | `1ebf88d0c3` | MERGEABLE/CLEAN | clarification-ai | Stage Visit Prep patient corrections in caller transaction |
| [#607](https://github.com/CosmaNeura/cosmaneura/pull/607) | `5a4c4e4177` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain encrypted clarification proposal and decision evidence |
| [#608](https://github.com/CosmaNeura/cosmaneura/pull/608) | `8e2ac982ce` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: save patient clarification decisions and matching corrections atomically |
| [#609](https://github.com/CosmaNeura/cosmaneura/pull/609) | `0ba67ffaec` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: patient review and choices for saved clarification proposals |
| [#610](https://github.com/CosmaNeura/cosmaneura/pull/610) | `cb3f12fa95` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain initial clarification proposals atomically |
| [#611](https://github.com/CosmaNeura/cosmaneura/pull/611) | `d0e6248d42` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): retain patient clarification turns |
| [#612](https://github.com/CosmaNeura/cosmaneura/pull/612) | `4a99cccbea` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): capture and recover patient follow-up responses |
| [#613](https://github.com/CosmaNeura/cosmaneura/pull/613) | `5b27ea597b` | MERGEABLE/CLEAN | delivery-timing | feat(visit-prep): define appointment-relative delivery timing |
| [#614](https://github.com/CosmaNeura/cosmaneura/pull/614) | `44a65b144b` | MERGEABLE/CLEAN | email | feat(visit-prep): record first-party invitation flow opens |
| [#615](https://github.com/CosmaNeura/cosmaneura/pull/615) | `8a5f02adc8` | MERGEABLE/CLEAN | clarification-ai | test(visit-prep): preserve historical clarification expand writer |
| [#616](https://github.com/CosmaNeura/cosmaneura/pull/616) | `10372613b0` | MERGEABLE/CLEAN | email | Visit Prep: connect current cadence evidence to timed email handoff |
| [#617](https://github.com/CosmaNeura/cosmaneura/pull/617) | `4bf795622c` | MERGEABLE/CLEAN | email | Visit Prep: enforce current email cadence before submission |
| [#618](https://github.com/CosmaNeura/cosmaneura/pull/618) | `5cfbe5c33e` | MERGEABLE/CLEAN | email | Visit Prep: add value-free email recovery health |
| [#619](https://github.com/CosmaNeura/cosmaneura/pull/619) | `7e0afc9c54` | MERGEABLE/CLEAN | email | Visit Prep: promptly clear obsolete queued email secrets |
| [#620](https://github.com/CosmaNeura/cosmaneura/pull/620) | `6ee5ff3332` | MERGEABLE/CLEAN | delivery-timing | Visit Prep: add value-free retention health |
| [#622](https://github.com/CosmaNeura/cosmaneura/pull/622) | `37798481ea` | MERGEABLE/CLEAN | delivery-timing | feat(visit-prep): central read-only reminder timing |
| [#623](https://github.com/CosmaNeura/cosmaneura/pull/623) | `9f07c5357f` | MERGEABLE/CLEAN | interview | feat(visit-prep): remember initial patient confirmation |
| [#624](https://github.com/CosmaNeura/cosmaneura/pull/624) | `1b6cc33b26` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain separate patient explanations for selected answers |
| [#625](https://github.com/CosmaNeura/cosmaneura/pull/625) | `2c355f201b` | MERGEABLE/CLEAN | synthesis | Visit Prep: enforce Core deidentification before synthesis |
| [#626](https://github.com/CosmaNeura/cosmaneura/pull/626) | `948ed71ffa` | MERGEABLE/CLEAN | companion | Visit Prep: add isolated companion authentication foundation |
| [#627](https://github.com/CosmaNeura/cosmaneura/pull/627) | `477c9d8745` | MERGEABLE/CLEAN | companion | Implement private companion Visit Prep package endpoint |
| [#628](https://github.com/CosmaNeura/cosmaneura/pull/628) | `017e1230a8` | MERGEABLE/CLEAN | companion | Visit Prep: owner-scoped preparation picker for CosmaBot |
| [#629](https://github.com/CosmaNeura/cosmaneura/pull/629) | `e493e70641` | MERGEABLE/CLEAN | handoff | Visit Prep: automatically hand off confirmed patient submissions |
| [#630](https://github.com/CosmaNeura/cosmaneura/pull/630) | `2e55c2c0df` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: protect patient clarification input and ground confirmed excerpts |
| [#631](https://github.com/CosmaNeura/cosmaneura/pull/631) | `00680eceb1` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): connect patient-requested protected AI clarification |
| [#633](https://github.com/CosmaNeura/cosmaneura/pull/633) | `413f0bfe03` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: accept AI explanations while preserving selected answers |
| [#638](https://github.com/CosmaNeura/cosmaneura/pull/638) | `e20cdd4c3f` | MERGEABLE/CLEAN | email | Visit Prep: connect explicit provider email handoff and default-off worker |
| [#640](https://github.com/CosmaNeura/cosmaneura/pull/640) | `0f33e873c0` | MERGEABLE/CLEAN | booking | Visit Prep: dormant signed Acuity booking to preparation handoff |
| [#641](https://github.com/CosmaNeura/cosmaneura/pull/641) | `1c983ff0da` | MERGEABLE/CLEAN | core-sync | Visit Prep: reconcile current Core billing-source history |
| [#642](https://github.com/CosmaNeura/cosmaneura/pull/642) | `5ea0c4822a` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain Core billing UI and verify feature coexistence |
| [#643](https://github.com/CosmaNeura/cosmaneura/pull/643) | `de55a1f358` | MERGEABLE/CLEAN | core-sync | Visit Prep: reconcile current Core CAB bridge and schema heads |
| [#644](https://github.com/CosmaNeura/cosmaneura/pull/644) | `d46a46a62b` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain current Core CAB display contract |
| [#645](https://github.com/CosmaNeura/cosmaneura/pull/645) | `27459c834d` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain current Core inline CAB provider panel |
| [#646](https://github.com/CosmaNeura/cosmaneura/pull/646) | `57a8d642c3` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain current Core CAB runtime wiring |
| [#647](https://github.com/CosmaNeura/cosmaneura/pull/647) | `450bd5df82` | MERGEABLE/CLEAN | question | Visit Prep: restore generated question-set schema dependencies |
| [#648](https://github.com/CosmaNeura/cosmaneura/pull/648) | `19e095ac8b` | MERGEABLE/CLEAN | question | Visit Prep: restore physician topic routes in API snapshot |
| [#649](https://github.com/CosmaNeura/cosmaneura/pull/649) | `b97abff554` | MERGEABLE/CLEAN | prep | Visit Prep: retain preparation schema dependencies |
| [#650](https://github.com/CosmaNeura/cosmaneura/pull/650) | `7e4006a594` | MERGEABLE/CLEAN | prep | Visit Prep: restore preparation lifecycle routes in API snapshot |
| [#651](https://github.com/CosmaNeura/cosmaneura/pull/651) | `1c26bd86d9` | MERGEABLE/CLEAN | email | Visit Prep: restore shared package and email schema dependencies |
| [#652](https://github.com/CosmaNeura/cosmaneura/pull/652) | `05b4d124b7` | MERGEABLE/CLEAN | package | Visit Prep: complete reproducible combined client contract |
| [#653](https://github.com/CosmaNeura/cosmaneura/pull/653) | `14b3130757` | MERGEABLE/CLEAN | booking | Visit Prep: stage booking lifecycle changes in one caller transaction |
| [#654](https://github.com/CosmaNeura/cosmaneura/pull/654) | `6ce1f92aa0` | MERGEABLE/CLEAN | booking | Visit Prep: keep booking vendor details in source adapter |
| [#655](https://github.com/CosmaNeura/cosmaneura/pull/655) | `ef6f0ff0f2` | MERGEABLE/CLEAN | booking | feat(visit-prep): retain atomic booking lifecycle evidence |
| [#656](https://github.com/CosmaNeura/cosmaneura/pull/656) | `8db01d4750` | MERGEABLE/CLEAN | booking | feat(visit-prep): reconcile signed current booking changes |
| [#657](https://github.com/CosmaNeura/cosmaneura/pull/657) | `370d679cb5` | MERGEABLE/CLEAN | booking | Visit Prep: preserve completed patient sources across booking reschedules |
| [#658](https://github.com/CosmaNeura/cosmaneura/pull/658) | `7a0dfa2e3b` | MERGEABLE/CLEAN | booking | feat(visit-prep): retain patient confirmation across reschedules |
| [#659](https://github.com/CosmaNeura/cosmaneura/pull/659) | `4fec70ec88` | MERGEABLE/CLEAN | booking | feat(visit-prep): fence creation after unmatched booking cancellation |
| [#660](https://github.com/CosmaNeura/cosmaneura/pull/660) | `500d87196a` | MERGEABLE/CLEAN | sms | feat(visit-prep): capture separate optional patient SMS permission |
| [#661](https://github.com/CosmaNeura/cosmaneura/pull/661) | `d4b490114c` | MERGEABLE/CLEAN | sms | Visit Prep: retain sender-wide SMS STOP suppression |
| [#662](https://github.com/CosmaNeura/cosmaneura/pull/662) | `b4518bfa44` | MERGEABLE/CLEAN | sms | Visit Prep: add separately authorized SMS queue |
| [#663](https://github.com/CosmaNeura/cosmaneura/pull/663) | `f3f7b3cbb3` | MERGEABLE/CLEAN | sms | Visit Prep: dispatch SMS once with current authority |
| [#664](https://github.com/CosmaNeura/cosmaneura/pull/664) | `9285521d1b` | MERGEABLE/CLEAN | sms | Visit Prep: show invitation and reminder SMS status |
| [#665](https://github.com/CosmaNeura/cosmaneura/pull/665) | `8ffe48432c` | MERGEABLE/CLEAN | sms | feat(visit-prep): retain physician SMS policy decisions |
| [#666](https://github.com/CosmaNeura/cosmaneura/pull/666) | `1cec58775d` | MERGEABLE/CLEAN | sms | feat(visit-prep): expose current SMS policy choices |
| [#667](https://github.com/CosmaNeura/cosmaneura/pull/667) | `33c67bdcf2` | MERGEABLE/CLEAN | sms | feat(visit-prep): add physician SMS policy controls |
| [#668](https://github.com/CosmaNeura/cosmaneura/pull/668) | `0060c7830a` | MERGEABLE/CLEAN | sms | feat(visit-prep): schedule SMS secret maintenance |
| [#669](https://github.com/CosmaNeura/cosmaneura/pull/669) | `dac281e4b7` | MERGEABLE/CLEAN | sms | feat(visit-prep): atomically queue short-lived SMS links |
| [#670](https://github.com/CosmaNeura/cosmaneura/pull/670) | `9df188d18f` | MERGEABLE/CLEAN | sms | feat(visit-prep): plan recurring SMS queues with resumable scans |

---

## 2. Provisional minimal link-only slice (CTO says acceptable; still provisional)

**CTO-reported (via Build Lead):** the proposed link-only slice is **acceptable**.  
**Still provisional** pending formal CTO/CoS agreement recorded against this inventory revision. Acceptability ≠ merge authorization.

### 2.1 Include (provisional) — 30 PRs

Goal for a later VP-1 (not implemented here): synthetic **link-only** journey → confirmed package → provider review of **verbatim answers + authorized synthesis**.

| Layer | PRs | Notes |
| --- | --- | --- |
| Question contract / versions / API / editor / owner refresh | `#549`–`#553` | Reuses master question-set schema; recovers five-topic defaults |
| Prep aggregate / commands / API / DIY workspace | `#554`–`#556`, `#558`–`#559` | Copyable HTTPS link path; send flags stay false |
| Encrypted answers + public interview + patient UI + confirm memory | `#561`–`#563`, `#565`, `#567`–`#568`, `#571`, `#623` | Link-only patient journey |
| Patient-source package API + provider package UI | `#579`–`#580` | Raw/verbatim topic sources for provider |
| Authorized synthesis (+ de-id gate) + cited brief UI | `#581`–`#585`, `#587`, `#589`–`#590`, `#625` | **Preserves authorized synthesis**; raw-plus-synthesis review required |
| Confirmed submission handoff | `#629` | Confirmed package lifecycle |

| PR | Head (10) | GH mergeable / status | Role | Title |
| --- | --- | --- | --- | --- |
| [#549](https://github.com/CosmaNeura/cosmaneura/pull/549) | `68655f8c3f` | MERGEABLE/BEHIND | question | Visit Prep: recover the five-topic question contract |
| [#550](https://github.com/CosmaNeura/cosmaneura/pull/550) | `81dd060a3a` | MERGEABLE/CLEAN | question | Visit Prep: recover physician question versions |
| [#551](https://github.com/CosmaNeura/cosmaneura/pull/551) | `4c1a511aba` | MERGEABLE/CLEAN | question | Visit Prep: recover the gated provider question API |
| [#552](https://github.com/CosmaNeura/cosmaneura/pull/552) | `468d58de46` | MERGEABLE/CLEAN | question | Visit Prep: recover the physician settings editor |
| [#553](https://github.com/CosmaNeura/cosmaneura/pull/553) | `cd8df1fc5b` | MERGEABLE/CLEAN | question | Visit Prep: recheck cached owner access before edits |
| [#554](https://github.com/CosmaNeura/cosmaneura/pull/554) | `8145d24964` | MERGEABLE/CLEAN | prep | feat(visit-prep): add dormant preparation aggregate |
| [#555](https://github.com/CosmaNeura/cosmaneura/pull/555) | `0e08f612f6` | MERGEABLE/CLEAN | prep | feat(visit-prep): define preparation command contract |
| [#556](https://github.com/CosmaNeura/cosmaneura/pull/556) | `1153f29ed0` | MERGEABLE/CLEAN | prep | feat(visit-prep): restore owner-scoped preparation commands |
| [#558](https://github.com/CosmaNeura/cosmaneura/pull/558) | `aa49c8b889` | MERGEABLE/CLEAN | prep | feat(visit-prep): expose gated provider preparation API |
| [#559](https://github.com/CosmaNeura/cosmaneura/pull/559) | `bc949fd3c6` | MERGEABLE/CLEAN | prep | feat(visit-prep): gated physician preparation workspace |
| [#561](https://github.com/CosmaNeura/cosmaneura/pull/561) | `badd00b156` | MERGEABLE/CLEAN | answer-storage | feat(visit-prep): immutable encrypted answer storage |
| [#562](https://github.com/CosmaNeura/cosmaneura/pull/562) | `819b18e6b0` | MERGEABLE/CLEAN | interview | feat(visit-prep): bounded patient interview contract and public gate |
| [#563](https://github.com/CosmaNeura/cosmaneura/pull/563) | `38745a2430` | MERGEABLE/CLEAN | interview | feat(visit-prep): patient confirmation and interview lifecycle |
| [#565](https://github.com/CosmaNeura/cosmaneura/pull/565) | `5cff7238f4` | MERGEABLE/CLEAN | interview | Visit Prep: public rate limits and interview concurrency checks |
| [#567](https://github.com/CosmaNeura/cosmaneura/pull/567) | `86d350af9e` | MERGEABLE/CLEAN | interview | Visit Prep: protected public interview API and real HTTP journey |
| [#568](https://github.com/CosmaNeura/cosmaneura/pull/568) | `53257aa264` | MERGEABLE/CLEAN | interview | Visit Prep: revoke unused and quarantine interacted wrong-recipient reports |
| [#571](https://github.com/CosmaNeura/cosmaneura/pull/571) | `dd71f083ac` | MERGEABLE/CLEAN | interview | Visit Prep: private patient browser journey |
| [#579](https://github.com/CosmaNeura/cosmaneura/pull/579) | `9c000cd856` | MERGEABLE/CLEAN | package | feat(visit-prep): expose authorized patient-source packages |
| [#580](https://github.com/CosmaNeura/cosmaneura/pull/580) | `e90e581bfa` | MERGEABLE/CLEAN | package | feat(visit-prep): review patient-source packages on the dashboard |
| [#581](https://github.com/CosmaNeura/cosmaneura/pull/581) | `0d40853739` | MERGEABLE/CLEAN | synthesis | Visit Prep: source-bound patient synthesis contract and dormant adapter |
| [#582](https://github.com/CosmaNeura/cosmaneura/pull/582) | `e3565e6e5a` | MERGEABLE/CLEAN | synthesis | Visit Prep: encrypted immutable synthesis artifact expand |
| [#583](https://github.com/CosmaNeura/cosmaneura/pull/583) | `2e76c53e76` | MERGEABLE/CLEAN | synthesis | Visit Prep: authorize current synthesis publication and reads |
| [#584](https://github.com/CosmaNeura/cosmaneura/pull/584) | `3c5a55aa56` | MERGEABLE/CLEAN | synthesis | feat(visit-prep): persist authorized synthesis build leases |
| [#585](https://github.com/CosmaNeura/cosmaneura/pull/585) | `c637d435cb` | MERGEABLE/CLEAN | synthesis | Visit Prep: account chart-free synthesis in the central usage ledger |
| [#587](https://github.com/CosmaNeura/cosmaneura/pull/587) | `c3c85a7faa` | MERGEABLE/CLEAN | synthesis | feat(visit-prep): recover metered synthesis with atomic publication |
| [#589](https://github.com/CosmaNeura/cosmaneura/pull/589) | `3a02dee7f4` | MERGEABLE/CLEAN | synthesis | Visit Prep: request synthesis and read the current cited package |
| [#590](https://github.com/CosmaNeura/cosmaneura/pull/590) | `86d296c5cc` | MERGEABLE/CLEAN | synthesis-ui | Visit Prep: display the current cited brief on the provider dashboard |
| [#623](https://github.com/CosmaNeura/cosmaneura/pull/623) | `9f07c5357f` | MERGEABLE/CLEAN | interview | feat(visit-prep): remember initial patient confirmation |
| [#625](https://github.com/CosmaNeura/cosmaneura/pull/625) | `2c355f201b` | MERGEABLE/CLEAN | synthesis | Visit Prep: enforce Core deidentification before synthesis |
| [#629](https://github.com/CosmaNeura/cosmaneura/pull/629) | `e493e70641` | MERGEABLE/CLEAN | handoff | Visit Prep: automatically hand off confirmed patient submissions |

**Excluding clarification-AI does not omit raw-plus-synthesis review.** Synthesis drafts above remain in the provisional include set.

### 2.2 Exclude from link-only merge set — outbound/email, SMS, clarification-AI, booking

| Category | Why excluded from link-only |
| --- | --- |
| Email / outbound delivery layers | Link-only; send stays false; not needed for copyable HTTPS journey |
| SMS / STOP / SMS policy / scheduler (`#660`–`#670`) | Future separate unreachable texting adapter draft; not M1 land-as-is |
| Optional clarification-AI / correction layers | Optional; out of minimal slice |
| Booking / Acuity automation | Later automation; DIY link path first |

| PR | Head (10) | GH mergeable / status | Role | Title |
| --- | --- | --- | --- | --- |
| [#573](https://github.com/CosmaNeura/cosmaneura/pull/573) | `68ec9dcf5b` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain attributable correction and review history |
| [#574](https://github.com/CosmaNeura/cosmaneura/pull/574) | `407613d0fc` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): preserve current patient contributions and review evidence |
| [#575](https://github.com/CosmaNeura/cosmaneura/pull/575) | `4aeab49514` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): capture answers to topics revealed by correction |
| [#576](https://github.com/CosmaNeura/cosmaneura/pull/576) | `5c7d13dcc8` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: gated patient saved-answer review and revision API |
| [#577](https://github.com/CosmaNeura/cosmaneura/pull/577) | `0df41b4896` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: patient saved-answer review and correction on desktop/mobile |
| [#591](https://github.com/CosmaNeura/cosmaneura/pull/591) | `36cb03076c` | MERGEABLE/CLEAN | email | Visit Prep: dormant minimal email outbox foundation |
| [#592](https://github.com/CosmaNeura/cosmaneura/pull/592) | `a153c00c21` | MERGEABLE/CLEAN | email | Visit Prep: bind encrypted invitation to stored delivery context |
| [#593](https://github.com/CosmaNeura/cosmaneura/pull/593) | `2282e1084c` | MERGEABLE/CLEAN | email | Visit Prep: dormant email consent and physician approval evidence |
| [#594](https://github.com/CosmaNeura/cosmaneura/pull/594) | `07156063de` | MERGEABLE/CLEAN | email | feat(visit-prep): validate evidence and resolve current email authority |
| [#595](https://github.com/CosmaNeura/cosmaneura/pull/595) | `795889c13e` | MERGEABLE/CLEAN | email | feat(visit-prep): capture physician invitation policy decisions |
| [#596](https://github.com/CosmaNeura/cosmaneura/pull/596) | `b26e99bf36` | MERGEABLE/CLEAN | email | feat(visit-prep): capture explicit patient email choices |
| [#597](https://github.com/CosmaNeura/cosmaneura/pull/597) | `5431c20b48` | MERGEABLE/CLEAN | email | feat(visit-prep): add physician email policy dashboard control |
| [#598](https://github.com/CosmaNeura/cosmaneura/pull/598) | `95bdf9331c` | MERGEABLE/CLEAN | email | feat(visit-prep): add confirmed patient email choice API |
| [#599](https://github.com/CosmaNeura/cosmaneura/pull/599) | `5a9c331601` | MERGEABLE/CLEAN | email | feat(visit-prep): add optional patient email choice screen |
| [#600](https://github.com/CosmaNeura/cosmaneura/pull/600) | `ec98824004` | MERGEABLE/CLEAN | email | feat(visit-prep): atomically enqueue guarded invitation emails |
| [#601](https://github.com/CosmaNeura/cosmaneura/pull/601) | `0534bc24f1` | MERGEABLE/CLEAN | email | feat(visit-prep): add single-submission email worker and secret cleanup |
| [#602](https://github.com/CosmaNeura/cosmaneura/pull/602) | `4894f5344c` | MERGEABLE/CLEAN | email | feat(visit-prep): show audited invitation status on dashboard |
| [#605](https://github.com/CosmaNeura/cosmaneura/pull/605) | `4180fc68a9` | MERGEABLE/CLEAN | clarification-ai | Add dormant Visit Prep patient clarification rules |
| [#606](https://github.com/CosmaNeura/cosmaneura/pull/606) | `1ebf88d0c3` | MERGEABLE/CLEAN | clarification-ai | Stage Visit Prep patient corrections in caller transaction |
| [#607](https://github.com/CosmaNeura/cosmaneura/pull/607) | `5a4c4e4177` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain encrypted clarification proposal and decision evidence |
| [#608](https://github.com/CosmaNeura/cosmaneura/pull/608) | `8e2ac982ce` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: save patient clarification decisions and matching corrections atomically |
| [#609](https://github.com/CosmaNeura/cosmaneura/pull/609) | `0ba67ffaec` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: patient review and choices for saved clarification proposals |
| [#610](https://github.com/CosmaNeura/cosmaneura/pull/610) | `cb3f12fa95` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain initial clarification proposals atomically |
| [#611](https://github.com/CosmaNeura/cosmaneura/pull/611) | `d0e6248d42` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): retain patient clarification turns |
| [#612](https://github.com/CosmaNeura/cosmaneura/pull/612) | `4a99cccbea` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): capture and recover patient follow-up responses |
| [#614](https://github.com/CosmaNeura/cosmaneura/pull/614) | `44a65b144b` | MERGEABLE/CLEAN | email | feat(visit-prep): record first-party invitation flow opens |
| [#615](https://github.com/CosmaNeura/cosmaneura/pull/615) | `8a5f02adc8` | MERGEABLE/CLEAN | clarification-ai | test(visit-prep): preserve historical clarification expand writer |
| [#616](https://github.com/CosmaNeura/cosmaneura/pull/616) | `10372613b0` | MERGEABLE/CLEAN | email | Visit Prep: connect current cadence evidence to timed email handoff |
| [#617](https://github.com/CosmaNeura/cosmaneura/pull/617) | `4bf795622c` | MERGEABLE/CLEAN | email | Visit Prep: enforce current email cadence before submission |
| [#618](https://github.com/CosmaNeura/cosmaneura/pull/618) | `5cfbe5c33e` | MERGEABLE/CLEAN | email | Visit Prep: add value-free email recovery health |
| [#619](https://github.com/CosmaNeura/cosmaneura/pull/619) | `7e0afc9c54` | MERGEABLE/CLEAN | email | Visit Prep: promptly clear obsolete queued email secrets |
| [#624](https://github.com/CosmaNeura/cosmaneura/pull/624) | `1b6cc33b26` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: retain separate patient explanations for selected answers |
| [#630](https://github.com/CosmaNeura/cosmaneura/pull/630) | `2e55c2c0df` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: protect patient clarification input and ground confirmed excerpts |
| [#631](https://github.com/CosmaNeura/cosmaneura/pull/631) | `00680eceb1` | MERGEABLE/CLEAN | clarification-ai | feat(visit-prep): connect patient-requested protected AI clarification |
| [#633](https://github.com/CosmaNeura/cosmaneura/pull/633) | `413f0bfe03` | MERGEABLE/CLEAN | clarification-ai | Visit Prep: accept AI explanations while preserving selected answers |
| [#638](https://github.com/CosmaNeura/cosmaneura/pull/638) | `e20cdd4c3f` | MERGEABLE/CLEAN | email | Visit Prep: connect explicit provider email handoff and default-off worker |
| [#640](https://github.com/CosmaNeura/cosmaneura/pull/640) | `0f33e873c0` | MERGEABLE/CLEAN | booking | Visit Prep: dormant signed Acuity booking to preparation handoff |
| [#651](https://github.com/CosmaNeura/cosmaneura/pull/651) | `1c26bd86d9` | MERGEABLE/CLEAN | email | Visit Prep: restore shared package and email schema dependencies |
| [#653](https://github.com/CosmaNeura/cosmaneura/pull/653) | `14b3130757` | MERGEABLE/CLEAN | booking | Visit Prep: stage booking lifecycle changes in one caller transaction |
| [#654](https://github.com/CosmaNeura/cosmaneura/pull/654) | `6ce1f92aa0` | MERGEABLE/CLEAN | booking | Visit Prep: keep booking vendor details in source adapter |
| [#655](https://github.com/CosmaNeura/cosmaneura/pull/655) | `ef6f0ff0f2` | MERGEABLE/CLEAN | booking | feat(visit-prep): retain atomic booking lifecycle evidence |
| [#656](https://github.com/CosmaNeura/cosmaneura/pull/656) | `8db01d4750` | MERGEABLE/CLEAN | booking | feat(visit-prep): reconcile signed current booking changes |
| [#657](https://github.com/CosmaNeura/cosmaneura/pull/657) | `370d679cb5` | MERGEABLE/CLEAN | booking | Visit Prep: preserve completed patient sources across booking reschedules |
| [#658](https://github.com/CosmaNeura/cosmaneura/pull/658) | `7a0dfa2e3b` | MERGEABLE/CLEAN | booking | feat(visit-prep): retain patient confirmation across reschedules |
| [#659](https://github.com/CosmaNeura/cosmaneura/pull/659) | `4fec70ec88` | MERGEABLE/CLEAN | booking | feat(visit-prep): fence creation after unmatched booking cancellation |
| [#660](https://github.com/CosmaNeura/cosmaneura/pull/660) | `500d87196a` | MERGEABLE/CLEAN | sms | feat(visit-prep): capture separate optional patient SMS permission |
| [#661](https://github.com/CosmaNeura/cosmaneura/pull/661) | `d4b490114c` | MERGEABLE/CLEAN | sms | Visit Prep: retain sender-wide SMS STOP suppression |
| [#662](https://github.com/CosmaNeura/cosmaneura/pull/662) | `b4518bfa44` | MERGEABLE/CLEAN | sms | Visit Prep: add separately authorized SMS queue |
| [#663](https://github.com/CosmaNeura/cosmaneura/pull/663) | `f3f7b3cbb3` | MERGEABLE/CLEAN | sms | Visit Prep: dispatch SMS once with current authority |
| [#664](https://github.com/CosmaNeura/cosmaneura/pull/664) | `9285521d1b` | MERGEABLE/CLEAN | sms | Visit Prep: show invitation and reminder SMS status |
| [#665](https://github.com/CosmaNeura/cosmaneura/pull/665) | `8ffe48432c` | MERGEABLE/CLEAN | sms | feat(visit-prep): retain physician SMS policy decisions |
| [#666](https://github.com/CosmaNeura/cosmaneura/pull/666) | `1cec58775d` | MERGEABLE/CLEAN | sms | feat(visit-prep): expose current SMS policy choices |
| [#667](https://github.com/CosmaNeura/cosmaneura/pull/667) | `33c67bdcf2` | MERGEABLE/CLEAN | sms | feat(visit-prep): add physician SMS policy controls |
| [#668](https://github.com/CosmaNeura/cosmaneura/pull/668) | `0060c7830a` | MERGEABLE/CLEAN | sms | feat(visit-prep): schedule SMS secret maintenance |
| [#669](https://github.com/CosmaNeura/cosmaneura/pull/669) | `dac281e4b7` | MERGEABLE/CLEAN | sms | feat(visit-prep): atomically queue short-lived SMS links |
| [#670](https://github.com/CosmaNeura/cosmaneura/pull/670) | `9df188d18f` | MERGEABLE/CLEAN | sms | feat(visit-prep): plan recurring SMS queues with resumable scans |

### 2.3 Defer (neither include nor hard-exclude) — 22 PRs

Companion CosmaBot receivers (`#604`, `#626`–`#628`), Core sync/reconcile / OpenAPI snapshot retainers, retention/cadence helpers, and similar are **deferred**. They are **not** code to implement in VP-0. Re-evaluate only if a later rebase proves a hard dependency (**inventory finding only**, not implementation).

**Inventory finding (not promoted):** `#603` (shared audited package read in caller transaction) may matter when landing `#579`–`#580`; keep deferred until a rebase demonstrates a hard dependency.

| PR | Head (10) | GH mergeable / status | Role | Title |
| --- | --- | --- | --- | --- |
| [#560](https://github.com/CosmaNeura/cosmaneura/pull/560) | `5f2658e2dc` | MERGEABLE/CLEAN | core-sync | fix(visit-prep): sync current Core and fail closed in settings |
| [#572](https://github.com/CosmaNeura/cosmaneura/pull/572) | `f3d20cce78` | MERGEABLE/CLEAN | core-sync | test(visit-prep): verify current Core ownership compatibility |
| [#588](https://github.com/CosmaNeura/cosmaneura/pull/588) | `2b065e8095` | MERGEABLE/CLEAN | core-sync | chore(visit-prep): reconcile current Core provider access queries |
| [#603](https://github.com/CosmaNeura/cosmaneura/pull/603) | `6513b9884c` | MERGEABLE/CLEAN | package | refactor(visit-prep): share audited package read in caller transaction |
| [#604](https://github.com/CosmaNeura/cosmaneura/pull/604) | `f37e83cc70` | MERGEABLE/CLEAN | companion | Visit Prep: draft preparation-scoped CosmaBot package-read contract |
| [#613](https://github.com/CosmaNeura/cosmaneura/pull/613) | `5b27ea597b` | MERGEABLE/CLEAN | delivery-timing | feat(visit-prep): define appointment-relative delivery timing |
| [#620](https://github.com/CosmaNeura/cosmaneura/pull/620) | `6ee5ff3332` | MERGEABLE/CLEAN | delivery-timing | Visit Prep: add value-free retention health |
| [#622](https://github.com/CosmaNeura/cosmaneura/pull/622) | `37798481ea` | MERGEABLE/CLEAN | delivery-timing | feat(visit-prep): central read-only reminder timing |
| [#626](https://github.com/CosmaNeura/cosmaneura/pull/626) | `948ed71ffa` | MERGEABLE/CLEAN | companion | Visit Prep: add isolated companion authentication foundation |
| [#627](https://github.com/CosmaNeura/cosmaneura/pull/627) | `477c9d8745` | MERGEABLE/CLEAN | companion | Implement private companion Visit Prep package endpoint |
| [#628](https://github.com/CosmaNeura/cosmaneura/pull/628) | `017e1230a8` | MERGEABLE/CLEAN | companion | Visit Prep: owner-scoped preparation picker for CosmaBot |
| [#641](https://github.com/CosmaNeura/cosmaneura/pull/641) | `1c983ff0da` | MERGEABLE/CLEAN | core-sync | Visit Prep: reconcile current Core billing-source history |
| [#642](https://github.com/CosmaNeura/cosmaneura/pull/642) | `5ea0c4822a` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain Core billing UI and verify feature coexistence |
| [#643](https://github.com/CosmaNeura/cosmaneura/pull/643) | `de55a1f358` | MERGEABLE/CLEAN | core-sync | Visit Prep: reconcile current Core CAB bridge and schema heads |
| [#644](https://github.com/CosmaNeura/cosmaneura/pull/644) | `d46a46a62b` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain current Core CAB display contract |
| [#645](https://github.com/CosmaNeura/cosmaneura/pull/645) | `27459c834d` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain current Core inline CAB provider panel |
| [#646](https://github.com/CosmaNeura/cosmaneura/pull/646) | `57a8d642c3` | MERGEABLE/CLEAN | core-sync | Visit Prep: retain current Core CAB runtime wiring |
| [#647](https://github.com/CosmaNeura/cosmaneura/pull/647) | `450bd5df82` | MERGEABLE/CLEAN | question | Visit Prep: restore generated question-set schema dependencies |
| [#648](https://github.com/CosmaNeura/cosmaneura/pull/648) | `19e095ac8b` | MERGEABLE/CLEAN | question | Visit Prep: restore physician topic routes in API snapshot |
| [#649](https://github.com/CosmaNeura/cosmaneura/pull/649) | `b97abff554` | MERGEABLE/CLEAN | prep | Visit Prep: retain preparation schema dependencies |
| [#650](https://github.com/CosmaNeura/cosmaneura/pull/650) | `7e4006a594` | MERGEABLE/CLEAN | prep | Visit Prep: restore preparation lifecycle routes in API snapshot |
| [#652](https://github.com/CosmaNeura/cosmaneura/pull/652) | `05b4d124b7` | MERGEABLE/CLEAN | package | Visit Prep: complete reproducible combined client contract |

### 2.4 Dependency order (planning only)

Provisional order for a future reviewable stack (no rebase performed here):

1. Question contract → lifecycle → API → editor → owner refresh (`#549`→`#553`)
2. Prep schema → commands → API → DIY workspace (`#554`→`#559`)
3. Answer storage → interview contract/lifecycle → limits → public API → wrong-recipient → patient UI → confirm memory (`#561`…`#571`, `#623`)
4. Package API → package UI (`#579`→`#580`)
5. Synthesis contract → storage → publication → leases → usage → worker → API → dashboard; de-id before synthesis (`#581`…`#590`, `#625`)
6. Confirmed handoff (`#629`)

**Observed conflict implication:** because stacked tips already conflict with `9cf14420fcc70cffd162f51aff13772ddbba9a6f` on shared files, a future VP-1 must **rebuild/slice onto current master**, not “merge tip `#670`”. That work is **out of VP-0**.

---

## 3. Dead commits / conflict notes (observed vs assumption)

| Claim | Evidence | Label |
| --- | --- | --- |
| `#549` is behind master tip | GitHub `mergeStateStatus=BEHIND` | Observed |
| `#549` content still merges cleanly alone onto current master | `git merge-tree --write-tree` exit 0 | Observed |
| Later chain tips are not conflict-free vs current master | merge-tree content conflicts on `#571+` samples | Observed |
| GitHub CLEAN on those same tips | `mergeable=MERGEABLE`, `status=CLEAN` for 107/108 | Observed |
| Therefore CLEAN ≠ stack-compatible with `9cf14420fcc70cffd162f51aff13772ddbba9a6f` | juxtaposition of the two checks | Observed |
| Oct 2 “retain/reconcile current Core” PRs may be stale relative to `9cf14420fcc70cffd162f51aff13772ddbba9a6f` | Titles/dates + master has moved (Session Prep/CAB/billing landed in-range) | Assumption — treat as rebase-time drop/rebuild candidates, not proven dead commits |
| `#566` withdrawn | Closed draft “branch name collision” | Observed |

**No rebase, force-push, or modification of existing drafts was performed.**

---

## 4. Default questions vs founder five (observed)

Founder-approved defaults in `docs/flight-plans/visit-prep.md` on master:

1. What is the main reason for this visit?
2. What medications are you prescribed right now?
3. Are you taking them as prescribed? (choice)
4. If not — what got in the way? (conditional)
5. Tell me your health story — whatever feels relevant.

Draft `#549` `CANONICAL_DEFAULT_QUESTION_SET` (`default.v1`) prompts **match those five** (keys `reason`, `medications`, `adherence`, `adherence_barrier`, `story`). Helper text is additional authored guidance; prompts are the same sentences.

**Plan (no schema proposal):** keep founder five as defaults; per-physician edit/reorder/add/remove with versioning so past answers stay tied to answered question text — **reuse** master `VisitPrepQuestionSet` / `VisitPrepQuestionSetVersion` / `VisitPrepQuestion` models and draft editor/lifecycle (`#550`–`#552`). Publishing rules requiring five topics + unconditional final story remain in the draft contract.

---

## 5. Confirmed package → Session Prep `patient_reported` (sketch only)

**Observed on master** (`contracts/schemas/provider-session-prep-v1/result.schema.json`, `app/services/session_prep_generator.py`):

- Brief field `patient_reported` is either `null` or `{label: "Patient-reported Visit Prep", summary, source_refs}`.
- Provenance source class `patient_reported_visit_prep` already exists.
- Generator instructions currently force **`null patient_reported`**.

**Mapping sketch (VP-2 context; not implemented here):**

| When | `patient_reported` | Provenance |
| --- | --- | --- |
| No confirmed Visit Prep package | `null` | honest absent / not included |
| Confirmed package present | fill from confirmed patient sources + authorized synthesis summary as allowed by contract; **verbatim answers remain in Visit Prep package** | `patient_reported_visit_prep` with source refs/versions/times; patient author class |

**Rule:** never set `patient_reported` without a confirmed package. Prior-notes Session Prep path must still build when Visit Prep is absent.

---

## 6. Raw answers alongside synthesis (plan)

**Observed in drafts:** patient-sources package contract `visit_prep.patient_sources.v1` exposes per-topic answers; `SynthesisView` renders topic answers and synthesis brief together; synthesis items cite `source_ref`s.

**Product rule (SOW §12):** when a confirmed package exists, providers (Core) and later Chief (OS M3) can always open **verbatim per-question answers with timestamps** alongside synthesis. Synthesis never replaces raw answers.

**VP-0 implication:** provisional slice **keeps** package + authorized synthesis UI drafts; clarification-AI drafts stay excluded.

---

## 7. CAB Tool texting reuse — access gap and CTO-reported paths

### 7.1 This environment

**Observed:** `gh api repos/CosmaNeura/Cosmas-CAB-Tool` returned **404 Not Found** in this cloud agent.  
**Not claimed:** that 404 proves the repository is missing, private, unauthorized, renamed, or any other specific cause.

Therefore this run **cannot** assert a verified current Twilio sender/consent/STOP inventory from primary source reads. **CAB port mapping is not complete.** No path guessing beyond cited reports; no credential/access expansion requested or performed.

### 7.2 CTO-reported read (not independently verified here)

**Label for all bullets in this subsection:** *CTO-reported read at SHA; not independently read in this cloud environment.*

- Repo: `CosmaNeura/Cosmas-CAB-Tool` (private)
- Default `main` SHA (CTO-reported): `77de2bfb8c3121a0c78d5adb1f17084645584bb9`
- Reported paths (shortened forms retained as reported; **full paths not invented**):
  - `cosmaneura_billing/cosmaneura_billing/chase/communications/{twilio.py,transport.py,dispatch.py,webhooks.py,worker.py,runtime.py,config.py}`
  - `cosmaneura_billing/tests/{test_twilio_transport.py,test_twilio_communications.py}`
  - consent/STOP references: `chase/sequence.py` and `frontend/src/caseCommunications.js`
  - `infra/communications.bicep`
  - `docs/flight-plans/{2026-10-02-sms-foundation-integration.md,2026-10-07-sms-upload-route-decoupling.md,2026-10-08-sms-trial-dispatch-ceiling.md}`

**Not claimed:** that filenames prove complete or correct sender/consent/STOP behavior. **Direct source/behavior verification is still required** before implementing a faithful Visit Prep texting port.

### 7.3 Secondary citations (insufficient)

Earlier Visit Prep integration draft (`#549` branch docs) cited CAB at older SHA `01ad975…` with ACS email/chase worker paths. Those are **historical secondary citations**, not a verified current Twilio inventory.

### 7.4 Future texting adapter (not VP-0 implementation)

When authorized later: separate draft PR; Core owns patient texting per SOW §12; reuse CAB patterns after verified read; **unreachable**; send flags false in every environment; synthetic **555** only in tests; no live sends; no Twilio account/secret changes until John or Shawn says yes. Core drafts `#660`–`#670` remain **excluded** from the link-only land set pending that verified port plan.

**VP-0 completeness gate:** this inventory is honest and publishable as docs, but **must not claim full VP-0 inventory complete** with respect to CAB texting until primary verification lands. Build Lead is routing missing source evidence to the CTO.

---

## 8. Master seams already present (reuse)

| Seam | Location | Observation |
| --- | --- | --- |
| Question-set schema + versioning | `app/models/visit_prep_question_set.py`, migration `2026_08_25_271e4439bff6_*` | Observed |
| Fail-closed flags / email+SMS send gates default false | `app/configuration/visit_prep.py` | Observed |
| Access dependency | `app/services/visit_prep_access.py`, `app/routes/dependencies/visit_prep.py` | Observed |
| Founder flight plan | `docs/flight-plans/visit-prep.md` | Observed (unchanged by this PR) |
| Session Prep `patient_reported` slot + null force | schema + `session_prep_generator.py` | Observed |

Legacy chat intake on master remains standing; Visit Prep is the relaunch path (issue `#135`). No new intake stack.

---

## 9. Remaining evidence gaps

1. **CAB primary read** in an authorized environment at CTO-reported SHA (or newer `main`) — required before texting-port implementation claims.
2. **Exhaustive stack rebase conflict matrix** onto `9cf14420fcc70cffd162f51aff13772ddbba9a6f` for the provisional include set — required before VP-1 land planning claims “conflict-free.”
3. **Hard-dependency proof** for any deferred PR (companion, API snapshot sync, cadence, `#603`) — only promote into the slice with explicit inventory amendment.
4. **Formal CTO/CoS recorded agreement** on this provisional slice list (CTO acceptability relay noted; keep provisional until recorded against this doc revision).

---

## 10. Recovery / runtime effect

- **Runtime effect of this docs PR:** none.
- **Recovery:** revert the docs commit / close the draft PR.
- **CI expectation:** unchanged required checks green on a docs-only diff touching solely `docs/flight-plans/visit-prep-vp0-inventory.md`.

---

## 11. References

- SOW Visit Prep 5 Nov 2026 incl. §12 (attached ticket inputs)
- Founder plan: `docs/flight-plans/visit-prep.md` @ `9cf14420fcc70cffd162f51aff13772ddbba9a6f`
- Issue `#135`
- Open draft chain `#549`–`#670` (index above)
- CTO-reported CAB `main` `77de2bfb8c3121a0c78d5adb1f17084645584bb9` (not independently read here)
