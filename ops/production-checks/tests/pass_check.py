"""Reference implementation of the PASS criteria in ../SQL-RATIONALE.md, used by the tests.
Input: the runner's marker lines. Output: ("PASS", None) or ("STOP", reason). Anything not PASS is STOP."""
import json, re

EXPECT_G2 = ('{"abort_counts":{"practice_conflicts":0,"reused_uuids":0,"zero_uuids":0},'
             '"e78845cecca6_would_abort":false,"is_ancestor_of_repository_head":true,'
             '"migration_head":"49126bf99c72","pending_migrations":["ddd6261fa36f","e78845cecca6",'
             '"69bad2e072e2","b38d5a410e49","c764298c7590","5f3c9a1e7b24","5708b4399768"],'
             '"provider_practice_authority_null_practice_id_count":0,"refusal_reasons":["pending_migrations"],'
             '"repository_head":"5708b4399768","scope":"core"}')
LABELS = ("doctors_active", "doctors_total", "practices_total", "practices_baa_clickwrap_accepted",
          "practices_negotiated_baa_on_file", "practices_subscription_status_set", "baa_acceptances_total",
          "practice_members_active", "pilot_grants_active")
EXACT_ONCE = ("RUNNER_SHA_OK=1", "RUN_START=1", "BUNDLE_VERIFIED=97", "ACCESS_LOCK_TIMEOUT_MS=4000",
              "ACCESS_ROWS=9", "RUN_DONE=1")


def verdict(lines, expect_g2=EXPECT_G2):
    lines = [l for l in lines if l]
    for m in EXACT_ONCE:
        if lines.count(m) != 1:
            return "STOP", "missing_or_repeated:" + m.split("=")[0]
    if any(re.match(r"(RUN_ERROR|PRECHECK_REFUSED|ACCESS_ERROR)=", l) for l in lines):
        return "STOP", "error_marker"
    pj = [l[len("PRECHECK_JSON="):] for l in lines if l.startswith("PRECHECK_JSON=")]
    if len(pj) != 1:
        return "STOP", "precheck_json_count"
    if "PRECHECK_EXIT=1" not in lines:
        return "STOP", "precheck_exit"
    counts = [l[len("ACCESS_COUNT="):].split(",") for l in lines if l.startswith("ACCESS_COUNT=")]
    if len(counts) != 9 or tuple(c[0] for c in counts) != LABELS or not all(len(c) == 2 and re.fullmatch(r"[0-9]+", c[1]) for c in counts):
        return "STOP", "access_counts"
    d = json.loads(pj[0])
    if d.get("repository_head") != "5708b4399768":
        return "STOP", "repository_head"
    if "guard_tables_missing" in d.get("refusal_reasons", []):
        return "STOP", "guard_tables_missing"
    if d.get("e78845cecca6_would_abort") is not False:
        return "STOP", "e78845cecca6_would_abort"
    if pj[0] != expect_g2:
        return "STOP", "G2_SHAPE=differs"
    return "PASS", None
