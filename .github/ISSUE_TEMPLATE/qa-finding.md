---
name: Browser QA finding
about: One synthetic browser failure with pinned release and reproduction evidence
title: "[QA] "
labels: "qa:reported"
---

Bot / queue handoff / batch / case:
Target and synthetic tenant ID (no credentials):
Frontend and backend release commits, before and after:
Reproduction steps (owned synthetic fixtures only):
Expected result, with source or product behavior reference:
Actual sanitized UI result and exact error code/text:
Record IDs (no patient names, transcript, audio, URLs containing credentials):
Private, reviewed browser evidence reference (no raw traces or sign-in screens):
Remaining fixtures / protected fixtures:
Impact and reproducibility:
Blocked owner / next action if applicable:

Fixer: reproduce; add failing regression; record PR, test results and fix SHA.
Release owner: record both approved test-copy pins before qa:fixed.
Independent bot: record retest case, release pin and evidence before qa:verified.
Coordinator: close on verified; link retest. Never include PHI or secrets.
