# Frontend environment detection hardening

Tier 2: feature/cohort presentation must use the configured API's parsed origin,
not substring matches or an implicit local fallback. The owner authorized this
narrow security-hardening PR on 2026-10-28. CodeQL findings 1, 2, 3 and 19 identify
the existing substring checks; scan results must verify their disposition.

Classify the exact HTTPS staging and production origins. Reject embedded
credentials and unknown, relative or malformed targets. Explicit local API hosts
are api.cosma.localhost, api-test.cosma.localhost, localhost, 127.0.0.1 and ::1;
HTTP/HTTPS local ports remain configurable. Parsed origin classification permits
paths/query/fragment on a known origin, but never matches those fields as hosts.
Zoom's synthetic lab accounts retain their separate stricter root-URL check,
which rejects paths, query parameters and fragments. Preserve every email cohort
and the independent lab release flag. Keep local beta behavior on its existing
production cohort; only local reference-toolkit presentation remains ungated.

Unknown targets disable beta/toolkit/lab presentation and never show a staging
badge. Server authorization remains authoritative. This adds no claim parsing,
identity SDK import, dependency, environment setting, deployment or cohort.

Before release: prove consumer regressions fail on the old implementation;
verify the shared classifier, unchanged cohorts, account controls and existing
Zoom restrictions; run full frontend tests, type/style checks and a synthetic
production-mode bundle. Independently review the final pushed SHA and record
findings/dispositions in the PR. A green scan execution alone does not establish
that the four alerts are resolved. No live-account qualification is claimed.

Release as one owner-selected compatible frontend candidate after merge and
required checks. If an intended target is refused, hold promotion and review its
exact origin instead of reopening the unknown-host fallback. Recovery is the
previous compatible frontend artifact or a reviewed source revert; a revert
restores the weaker classification and is not a completed security remedy.
No schema/data sequence, migration/rehearsal, backfill or feature-flag rollback
applies. No production promotion is authorized by this flight plan.
