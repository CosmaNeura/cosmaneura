# R2 #992 precheck + access-before counts: in-container runner, r2 (Thu 26 Nov 2026).
# Derived from Fable-reviewed runner f61f498b. Carrier-independent: any carrier that starts ONE
# execution of cosmaneura-prod-migrate on the live digest with --command /opt/venv/bin/python,
# the sha-asserting --args bootstrap, and env DATABASE_URL (secretref), CORE_RUNNER_Z,
# CORE_BUNDLE_Z, CORE_MIGRATIONS_Z can run it.
# Output boundary (P2): every line goes through emit(). Only fixed keys, fixed codes, fixed
# labels, non-negative integers, booleans and 12-hex revision IDs can leave. Never DATABASE_URL,
# exception text, env, or row/column content. The runner always exits 0, so the job's replica
# retry never re-runs it; the verdict is in the markers.
import base64, hashlib, importlib, json, os, re, subprocess, sys, tempfile, urllib.parse, zlib

EXPECT_SCRIPTS = {
    "scripts/production_migration_precheck.py": "0bca2f478bd5012d0c95218013c198b3780b2a0a13b1f617dad67367e29f3628",
    "scripts/session_prep_production_eligibility_preflight.py": "748e7bd0f283907fc9a05b435b7891479f3d08c952b7174be319d8faeb3fb658",
}
EXPECT_MIGRATIONS_MANIFEST = "a3684949bdc4cf9016ee597000dc96abf9ec4a7702bd667743d92bead9fe5895"  # cf67a631, 97 files
CA = "/etc/ssl/certs/ca-certificates.crt"
HOST = "cosmaneura-prod-postgres.postgres.database.azure.com"

# All SQL this runner itself issues: fixed literals only (P4). The #992 SQL lives in the
# sha256-pinned precheck script above. No SQL is read from env, files or arguments.
# ACCESS_SQL is the CTO-chosen candidate (9:22am ET), byte-identical to /workspace/pc-test/access.sql
# (sha256 5568dd8f5aefaef199b32165412a665f5373851ce29d059f94a92270895e700a). NEW, not yet Fable-reviewed.
ACCESS_SQL = "\n".join([
    "SELECT 'doctors_active' AS category, count(*) AS n FROM public.doctor WHERE deleted_at IS NULL",
    "UNION ALL SELECT 'doctors_total', count(*) FROM public.doctor",
    "UNION ALL SELECT 'practices_total', count(*) FROM public.practice",
    "UNION ALL SELECT 'practices_baa_clickwrap_accepted', count(*) FROM public.practice WHERE baa_accepted_at IS NOT NULL",
    "UNION ALL SELECT 'practices_negotiated_baa_on_file', count(*) FROM public.practice WHERE negotiated_baa_on_file",
    "UNION ALL SELECT 'practices_subscription_status_set', count(*) FROM public.practice WHERE subscription_status IS NOT NULL",
    "UNION ALL SELECT 'baa_acceptances_total', count(*) FROM public.baa_acceptance",
    "UNION ALL SELECT 'practice_members_active', count(*) FROM public.practice_member WHERE access_revoked_at IS NULL",
    "UNION ALL SELECT 'pilot_grants_active', count(*) FROM public.pilot_access_grandfather_grant WHERE revoked_at IS NULL",
])
ACCESS_SQL_SHA256 = "5568dd8f5aefaef199b32165412a665f5373851ce29d059f94a92270895e700a"
ACCESS_TXN_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
ACCESS_LOCK_SQL = "SET LOCAL lock_timeout = '4s'"
ACCESS_ASSERT_SQL = "SELECT current_setting('transaction_read_only'), current_setting('lock_timeout')"
ACCESS_COLUMNS = ("category", "n")
# P3: the exact label allowlist. Global categories only (no per-practice or per-user labels).
ACCESS_LABELS = (
    "doctors_active",
    "doctors_total",
    "practices_total",
    "practices_baa_clickwrap_accepted",
    "practices_negotiated_baa_on_file",
    "practices_subscription_status_set",
    "baa_acceptances_total",
    "practice_members_active",
    "pilot_grants_active",
)
ACCESS_LOCK_TIMEOUT_MS = 4000

REV = re.compile(r"[0-9a-f]{12}")
CODES = frozenset({
    "bundle_refused", "internal", "configuration_refused", "snapshot_refused", "unexpected",
    "timeout", "runner_error", "shape_refused", "query_refused", "session_refused",
})
SHAPE_KEYS = ("scheme", "user", "password", "host", "port", "path", "keys", "verify_full", "prefer", "ca_file")
PRECHECK_KEYS = frozenset({
    "abort_counts", "e78845cecca6_would_abort", "is_ancestor_of_repository_head", "migration_head",
    "pending_migrations", "provider_practice_authority_null_practice_id_count", "refusal_reasons",
    "repository_head", "scope",
})
ABORT_LABELS = frozenset({"practice_conflicts", "reused_uuids", "zero_uuids"})
REASONS = frozenset({"migration_head_unknown", "pending_migrations", "guard_tables_missing", "e78845_abort"})
KINDS = {
    "RUN_START": "one", "BUNDLE_VERIFIED": "count", "URL_SHAPE": "shape", "PRECHECK_JSON": "precheck",
    "PRECHECK_REFUSED": "code", "PRECHECK_EXIT": "count", "ACCESS_LOCK_TIMEOUT_MS": "count",
    "ACCESS_COUNT": "access", "ACCESS_ROWS": "count", "ACCESS_ERROR": "code", "RUN_ERROR": "code",
    "RUN_DONE": "one",
}


class Boundary(Exception):
    """A value tried to cross the output boundary without matching its type."""


def nonneg(n):
    return type(n) is int and n >= 0


def canonical_precheck(line):
    """Re-validate the #992 JSON field by field; return it only if every value is a fixed label,
    a non-negative int, a bool, null, or a 12-hex revision, and it is byte-canonical."""
    if not isinstance(line, str) or len(line) > 8192:
        raise Boundary
    try:
        d = json.loads(line)
    except Exception:
        raise Boundary
    if type(d) is not dict or set(d) != PRECHECK_KEYS or d["scope"] != "core":
        raise Boundary
    for k in ("migration_head", "repository_head"):
        if not isinstance(d[k], str) or not REV.fullmatch(d[k]):
            raise Boundary
    if type(d["is_ancestor_of_repository_head"]) is not bool:
        raise Boundary
    p = d["pending_migrations"]
    if type(p) is not list or len(p) > 200 or not all(isinstance(r, str) and REV.fullmatch(r) for r in p):
        raise Boundary
    if d["e78845cecca6_would_abort"] is not None and type(d["e78845cecca6_would_abort"]) is not bool:
        raise Boundary
    a = d["abort_counts"]
    if type(a) is not dict or (a and (set(a) != ABORT_LABELS or not all(nonneg(v) for v in a.values()))):
        raise Boundary
    n = d["provider_practice_authority_null_practice_id_count"]
    if n is not None and not nonneg(n):
        raise Boundary
    rr = d["refusal_reasons"]
    if type(rr) is not list or not all(isinstance(r, str) and r in REASONS for r in rr) or len(set(rr)) != len(rr):
        raise Boundary
    canon = json.dumps(d, sort_keys=True, separators=(",", ":"))
    if canon != line:
        raise Boundary
    return canon


def render(key, value):
    kind = KINDS.get(key)
    if kind == "one" and value == 1 and type(value) is int:
        return "1"
    if kind == "count" and nonneg(value):
        return str(value)
    if kind == "code" and isinstance(value, str) and value in CODES:
        return value
    if kind == "access" and type(value) is tuple and len(value) == 2 and value[0] in ACCESS_LABELS and nonneg(value[1]):
        return "%s,%d" % value
    if kind == "shape" and type(value) is dict and tuple(value) == SHAPE_KEYS and all(type(v) is bool for v in value.values()):
        return ",".join("%s:%d" % (k, int(v)) for k, v in value.items())
    if kind == "precheck":
        return canonical_precheck(value)
    raise Boundary


def emit(key, value):
    print("%s=%s" % (key, render(key, value)), flush=True)


def access_pairs(rows, cols):
    """P3: return the 9 (label, count) pairs only if the result is exactly the allowlist,
    integer-only. Otherwise raise; the caller prints a fixed code and no counts."""
    if tuple(cols) != ACCESS_COLUMNS or type(rows) is not list or len(rows) != len(ACCESS_LABELS):
        raise Boundary
    got = {}
    for r in rows:
        if len(r) != 2 or not isinstance(r[0], str) or r[0] not in ACCESS_LABELS or r[0] in got or not nonneg(r[1]):
            raise Boundary
        got[r[0]] = r[1]
    if set(got) != set(ACCESS_LABELS):
        raise Boundary
    return [(k, got[k]) for k in ACCESS_LABELS]


def manifest_of(migrations):
    return hashlib.sha256(json.dumps(sorted((p, hashlib.sha256(t.encode()).hexdigest()) for p, t in migrations.items())).encode()).hexdigest()


def self_check():
    if hashlib.sha256(ACCESS_SQL.encode()).hexdigest() != ACCESS_SQL_SHA256:
        raise Boundary
    if tuple(re.findall(r"SELECT '([a-z_]+)'", ACCESS_SQL)) != ACCESS_LABELS:
        raise Boundary


def load_bundle():
    """Verify scripts and the candidate migrations manifest BEFORE DATABASE_URL is read."""
    bundle = json.loads(zlib.decompress(base64.b64decode(os.environ["CORE_BUNDLE_Z"])))
    migrations = json.loads(zlib.decompress(base64.b64decode(os.environ["CORE_MIGRATIONS_Z"])))
    if type(bundle) is not dict or set(bundle) != {"scripts"} or type(migrations) is not dict:
        raise Boundary  # bundle['sql'] no longer exists; any extra key is refused
    scripts = bundle["scripts"]
    if type(scripts) is not dict or set(scripts) != set(EXPECT_SCRIPTS):
        raise Boundary
    for p, txt in scripts.items():
        if hashlib.sha256(txt.encode()).hexdigest() != EXPECT_SCRIPTS[p]:
            raise Boundary
    if not all(re.fullmatch(r"migrations/versions/[A-Za-z0-9_]+\.py", p) for p in migrations):
        raise Boundary
    if manifest_of(migrations) != EXPECT_MIGRATIONS_MANIFEST:
        raise Boundary
    root = tempfile.mkdtemp(prefix="core-precheck-")
    os.makedirs(root + "/scripts")
    os.makedirs(root + "/migrations/versions")
    open(root + "/scripts/__init__.py", "w").close()  # regular package so no image copy can shadow it
    for p, txt in list(scripts.items()) + list(migrations.items()):
        with open(os.path.join(root, p), "w", encoding="utf-8") as f:
            f.write(txt)
    return root, len(migrations)


def shape(u):
    r = dict.fromkeys(SHAPE_KEYS, False)
    try:
        p = urllib.parse.urlsplit(u)
        pairs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        q = dict(pairs)
        r["scheme"] = p.scheme in {"postgres", "postgresql"}
        r["user"] = bool(p.username)
        r["password"] = bool(p.password)
        r["host"] = (p.hostname or "").lower() == HOST
        try:
            r["port"] = p.port in {None, 5432}
        except Exception:
            r["port"] = False
        r["path"] = p.path == "/production"
        r["keys"] = set(q) == {"sslmode", "sslrootcert"} and len(pairs) == len(q)
        r["verify_full"] = q.get("sslmode") == "verify-full"
        r["prefer"] = q.get("sslmode") == "prefer"
        r["ca_file"] = q.get("sslrootcert") == CA and os.path.isfile(CA)
    except Exception:
        pass
    return r


def run_precheck(root, url):
    try:
        pr = subprocess.run([sys.executable, "-I", "-B", root + "/scripts/production_migration_precheck.py"],
                            env={"DATABASE_URL": url, "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": root},
                            capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        emit("PRECHECK_REFUSED", "timeout"); return
    except Exception:
        emit("PRECHECK_REFUSED", "runner_error"); return
    lines = (pr.stdout or "").strip().splitlines()
    if pr.returncode in (0, 1) and len(lines) == 1:
        try:
            emit("PRECHECK_JSON", lines[0])
        except Boundary:
            emit("PRECHECK_REFUSED", "shape_refused")
    else:
        m = re.search(r"Reason: (configuration_refused|snapshot_refused)\.", pr.stderr or "")
        emit("PRECHECK_REFUSED", m.group(1) if m else "unexpected")
    emit("PRECHECK_EXIT", pr.returncode if pr.returncode >= 0 else 255)


def run_access(root, url):
    try:
        sys.path.insert(0, root)
        pre = importlib.import_module("scripts.production_migration_precheck")
        import psycopg
        params = pre.connection_parameters(url)  # the #992 verify-full rule, read-only session
        params["application_name"] = "r2_access_before_counts"
    except Exception:
        emit("ACCESS_ERROR", "configuration_refused"); return
    try:
        with psycopg.connect(**params) as c:
            c.execute(ACCESS_TXN_SQL)
            c.execute(ACCESS_LOCK_SQL)
            if c.execute(ACCESS_ASSERT_SQL).fetchone() != ("on", "4s"):
                c.rollback()
                emit("ACCESS_ERROR", "session_refused"); return
            cur = c.execute(ACCESS_SQL)
            rows = cur.fetchall()
            cols = [d.name for d in (cur.description or [])]
            c.rollback()
    except Exception:
        emit("ACCESS_ERROR", "query_refused"); return
    emit("ACCESS_LOCK_TIMEOUT_MS", ACCESS_LOCK_TIMEOUT_MS)
    try:
        pairs = access_pairs(rows, cols)
    except Exception:
        emit("ACCESS_ERROR", "shape_refused"); return  # P3: no count is printed unless labels match exactly
    for p in pairs:
        emit("ACCESS_COUNT", p)
    emit("ACCESS_ROWS", len(pairs))


def main():
    emit("RUN_START", 1)
    try:
        self_check()
        root, n = load_bundle()
    except Exception:
        emit("RUN_ERROR", "bundle_refused"); return
    emit("BUNDLE_VERIFIED", n)
    url = os.environ.get("DATABASE_URL", "")  # first and only read, after every pin passed
    emit("URL_SHAPE", shape(url))
    run_precheck(root, url)
    run_access(root, url)
    emit("RUN_DONE", 1)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        try:
            emit("RUN_ERROR", "internal")
        except BaseException:
            pass
    sys.exit(0)
