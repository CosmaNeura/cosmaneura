"""Synthetic-only tests for runner.py (r2). Needs synthetic_pg.sh start. No Azure, no prod, no real secret."""
import base64, hashlib, io, json, os, pathlib, re, runpy, subprocess, sys, time, zlib, contextlib
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pass_check

HERE = pathlib.Path(__file__).resolve().parent
PKG = HERE.parent
RUNNER = (PKG / "runner.py").read_bytes()
BOOT = (PKG / "bootstrap-args.txt").read_text()
REPO = os.environ.get("CORE_REPO", "/workspace/cosmaneura")
SHA = "cf67a63129426c558b84559618822a6b59e7cdba"
PGT = os.environ.get("PGT", "/workspace/r2-pgtest")
SENTINEL = "SyntheticSentinelPw7f3a9c"
HOST = "cosmaneura-prod-postgres.postgres.database.azure.com"
URL = f"postgresql://app_rw:{SENTINEL}@{HOST}:5432/production?sslmode=verify-full&sslrootcert={PGT}/ca.crt"
FORBIDDEN = [SENTINEL, "postgresql://", "app_rw", "leak-canary", "user_canary", "Canary Practice", "Traceback", "prac_", "doc_"]
ALL_OUTPUT = []


def git_show(p):
    return subprocess.check_output(["git", "-C", REPO, "show", f"{SHA}:{p}"]).decode()


SCRIPTS = {p: git_show(p) for p in ("scripts/production_migration_precheck.py", "scripts/session_prep_production_eligibility_preflight.py")}
MIGS = {p: git_show(p) for p in subprocess.check_output(["git", "-C", REPO, "ls-tree", "-r", "--name-only", SHA, "migrations/versions"]).decode().split() if p.endswith(".py")}
enc = lambda o: base64.b64encode(zlib.compress(json.dumps(o).encode(), 9)).decode()


def psql(sql, db="production"):
    return subprocess.run(["psql", "-h", f"{PGT}/sock", "-U", "postgres", "-d", db, "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql],
                          check=True, capture_output=True, text=True).stdout.strip()


def run(runner=RUNNER, bundle=None, migs=None, url=URL, boot=BOOT, extra_env=None):
    """Start the runner exactly as a carrier would: python <bootstrap args> with only the four env inputs."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
           "DATABASE_URL": url,
           "CORE_RUNNER_Z": base64.b64encode(zlib.compress(runner, 9)).decode(),
           "CORE_BUNDLE_Z": enc(bundle if bundle is not None else {"scripts": SCRIPTS}),
           "CORE_MIGRATIONS_Z": enc(migs if migs is not None else MIGS)}
    env.update(extra_env or {})
    t = time.time()
    p = subprocess.run([sys.executable, boot], env=env, capture_output=True, text=True, timeout=300)
    out = p.stdout + p.stderr
    ALL_OUTPUT.append(out)
    return p.returncode, p.stdout.splitlines(), p.stderr, time.time() - t


def load_runner_module():
    # runpy returns a copy of the globals; hand back the live module dict so patches reach the functions
    return runpy.run_path(str(PKG / "runner.py"), run_name="runner_under_test")["run_access"].__globals__


def capture(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a)
    out = buf.getvalue()
    ALL_OUTPUT.append(out)
    return out.splitlines()


class T01HappyPath(unittest.TestCase):
    def test_happy_path_pass(self):
        before = psql("SELECT count(*) FROM doctor") + psql("SELECT count(*) FROM practice")
        rc, lines, err, _ = run()
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertEqual(lines[:3], ["RUNNER_SHA_OK=1", "RUN_START=1", "BUNDLE_VERIFIED=97"])
        self.assertIn("PRECHECK_JSON=" + pass_check.EXPECT_G2, lines)
        self.assertIn("PRECHECK_EXIT=1", lines)
        self.assertIn("ACCESS_LOCK_TIMEOUT_MS=4000", lines)
        expected = {"doctors_active": 2, "doctors_total": 3, "practices_total": 2, "practices_baa_clickwrap_accepted": 1,
                    "practices_negotiated_baa_on_file": 1, "practices_subscription_status_set": 1, "baa_acceptances_total": 1,
                    "practice_members_active": 2, "pilot_grants_active": 1}
        got = dict(l[len("ACCESS_COUNT="):].split(",") for l in lines if l.startswith("ACCESS_COUNT="))
        self.assertEqual({k: int(v) for k, v in got.items()}, expected)
        self.assertEqual(lines[-1], "RUN_DONE=1")
        self.assertEqual(pass_check.verdict(lines), ("PASS", None))
        # every line is a fixed key with a boundary-typed value
        for l in lines:
            self.assertRegex(l, r"^(RUNNER_SHA_OK=1|RUN_START=1|BUNDLE_VERIFIED=\d+|URL_SHAPE=[a-z_:01,]+|PRECHECK_JSON=\{.*\}|PRECHECK_EXIT=\d+|ACCESS_LOCK_TIMEOUT_MS=\d+|ACCESS_COUNT=[a-z_]+,\d+|ACCESS_ROWS=9|RUN_DONE=1)$")
        # read-only: nothing changed
        self.assertEqual(before, psql("SELECT count(*) FROM doctor") + psql("SELECT count(*) FROM practice"))
        print("\n    happy-path markers:\n      " + "\n      ".join(lines))


class T02LabelMismatchSilent(unittest.TestCase):
    def test_db_returns_unexpected_label_prints_no_counts(self):
        g = load_runner_module()
        root, _ = self._root(g)
        g["ACCESS_SQL"] = g["ACCESS_SQL"].replace("'doctors_active'", "'doctors_active_by_practice'")
        lines = capture(g["run_access"], root, URL)
        self.assertEqual(lines, ["ACCESS_LOCK_TIMEOUT_MS=4000", "ACCESS_ERROR=shape_refused"])
        self.assertEqual(pass_check.verdict(lines)[0], "STOP")

    def test_missing_and_extra_label_prints_no_counts(self):
        g = load_runner_module()
        root, _ = self._root(g)
        g["ACCESS_SQL"] = g["ACCESS_SQL"] + "\nUNION ALL SELECT 'extra_category', 1"
        self.assertNotIn("ACCESS_COUNT", "\n".join(capture(g["run_access"], root, URL)))
        g["ACCESS_SQL"] = g["ACCESS_SQL"].split("\nUNION ALL SELECT 'pilot_grants_active'")[0]
        self.assertNotIn("ACCESS_COUNT", "\n".join(capture(g["run_access"], root, URL)))

    def test_access_pairs_unit(self):
        g = load_runner_module()
        L = g["ACCESS_LABELS"]
        ok = [(l, 1) for l in L]
        self.assertEqual(g["access_pairs"](list(reversed(ok)), ["category", "n"]), ok)  # order-insensitive, output in allowlist order
        bad = [
            (ok[:-1], ["category", "n"]),                                  # 8 rows
            (ok + [("x", 1)], ["category", "n"]),                           # 10 rows
            (ok[:-1] + [(L[0], 1)], ["category", "n"]),                     # duplicate
            ([(l, -1) for l in L], ["category", "n"]),                      # negative
            ([(l, True) for l in L], ["category", "n"]),                    # bool posing as int
            ([(l, 1.0) for l in L], ["category", "n"]),                     # float
            ([(l, "1") for l in L], ["category", "n"]),                     # text count
            (ok, ["category", "email"]),                                    # wrong columns
            ([(l, 1, "x") for l in L], ["category", "n"]),                  # extra column
        ]
        for rows, cols in bad:
            with self.assertRaises(g["Boundary"]):
                g["access_pairs"](rows, cols)

    @staticmethod
    def _root(g):
        os.environ["CORE_BUNDLE_Z"] = enc({"scripts": SCRIPTS})
        os.environ["CORE_MIGRATIONS_Z"] = enc(MIGS)
        try:
            return g["load_bundle"]()
        finally:
            del os.environ["CORE_BUNDLE_Z"], os.environ["CORE_MIGRATIONS_Z"]


class T03RowContentLeakBlocked(unittest.TestCase):
    def test_sql_selecting_row_content_is_refused_without_printing_it(self):
        g = load_runner_module()
        root, _ = T02LabelMismatchSilent._root(g)
        for sql in ("SELECT email AS category, 1 AS n FROM public.doctor",
                    "SELECT 'doctors_active' AS category, email AS n FROM public.doctor",
                    "SELECT name AS category, count(*) AS n FROM public.practice GROUP BY name"):
            g["ACCESS_SQL"] = sql
            out = "\n".join(capture(g["run_access"], root, URL))
            self.assertIn("ACCESS_ERROR=", out)
            self.assertNotIn("ACCESS_COUNT", out)
            for f in FORBIDDEN:
                self.assertNotIn(f, out)

    def test_tampered_runner_with_leaky_sql_never_executes(self):
        evil = RUNNER.replace(b"\"SELECT 'doctors_active' AS category, count(*) AS n FROM public.doctor WHERE deleted_at IS NULL\"",
                              b"\"SELECT email AS category, 1 AS n FROM public.doctor\"")
        self.assertNotEqual(evil, RUNNER)
        rc, lines, err, _ = run(runner=evil)
        self.assertEqual((rc, lines, err), (0, ["RUN_ERROR=runner_sha_mismatch"], ""))

    def test_boundary_refuses_non_typed_values(self):
        g = load_runner_module()
        B, render = g["Boundary"], g["render"]
        for key, val in [("ACCESS_ROWS", "9"), ("ACCESS_ROWS", -1), ("ACCESS_ROWS", True), ("RUN_ERROR", "free text"),
                         ("ACCESS_COUNT", ("leak-canary-1@example.test", 1)), ("UNKNOWN_KEY", 1),
                         ("URL_SHAPE", {"scheme": "postgresql://x"}), ("PRECHECK_JSON", '{"scope":"core"}')]:
            with self.assertRaises(B, msg=key):
                render(key, val)
        good = json.loads(pass_check.EXPECT_G2)
        for k, v in [("migration_head", "leak-canary"), ("scope", "x"), ("pending_migrations", ["Canary Practice"]),
                     ("abort_counts", {"practice_conflicts": "a", "reused_uuids": 0, "zero_uuids": 0}),
                     ("refusal_reasons", ["free text"]), ("extra", 1)]:
            d = dict(good); d[k] = v
            with self.assertRaises(B, msg=k):
                g["canonical_precheck"](json.dumps(d, sort_keys=True, separators=(",", ":")))
        with self.assertRaises(B):
            g["canonical_precheck"](json.dumps(good))  # non-canonical spacing is refused too
        self.assertEqual(g["canonical_precheck"](pass_check.EXPECT_G2), pass_check.EXPECT_G2)


class T04RunnerShaMismatch(unittest.TestCase):
    def test_one_byte_change_refused_in_bootstrap(self):
        rc, lines, err, _ = run(runner=RUNNER + b"\n")
        self.assertEqual((rc, lines, err), (0, ["RUN_ERROR=runner_sha_mismatch"], ""))

    def test_bootstrap_pins_the_file_sha(self):
        self.assertIn(hashlib.sha256(RUNNER).hexdigest(), BOOT)


class T05ManifestAndBundleMismatch(unittest.TestCase):
    def _refused(self, **kw):
        rc, lines, err, _ = run(url="postgresql://u:" + SENTINEL + "@nowhere/x", **kw)
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(lines, ["RUNNER_SHA_OK=1", "RUN_START=1", "RUN_ERROR=bundle_refused"])  # no URL_SHAPE: DATABASE_URL never read

    def test_candidate_manifest_mismatch(self):
        m = dict(MIGS); k = sorted(m)[0]; m[k] += "\n# changed\n"
        self._refused(migs=m)

    def test_migration_missing(self):
        m = dict(MIGS); m.pop(sorted(m)[-1])
        self._refused(migs=m)

    def test_extra_migration(self):
        m = dict(MIGS); m["migrations/versions/zz_extra.py"] = "revision = 'abcabcabcabc'\n"
        self._refused(migs=m)

    def test_script_mismatch(self):
        s = dict(SCRIPTS); s["scripts/production_migration_precheck.py"] += "\n"
        self._refused(bundle={"scripts": s})

    def test_bundle_sql_key_refused(self):
        self._refused(bundle={"scripts": SCRIPTS, "sql": "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"})


class T06LockTimeout(unittest.TestCase):
    def test_session_lock_timeout_is_4s_and_enforced(self):
        holder = subprocess.Popen(["psql", f"dbname=production user=postgres host={PGT}/sock application_name=r2-lock-holder", "-q", "-c",
                                   "BEGIN; LOCK TABLE public.practice IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(25); COMMIT;"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.0)
            g = load_runner_module()
            root, _ = T02LabelMismatchSilent._root(g)
            t = time.time()
            lines = capture(g["run_access"], root, URL)
            dt = time.time() - t
        finally:
            holder.terminate(); holder.wait()
            psql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = 'r2-lock-holder'")
        self.assertEqual(lines, ["ACCESS_ERROR=query_refused"])
        self.assertGreaterEqual(dt, 3.8)
        self.assertLess(dt, 10)
        log = pathlib.Path(PGT, "server.log").read_text()
        self.assertIn("canceling statement due to lock timeout", log)
        print(f"\n    blocked access query refused after {dt:.1f}s (lock_timeout 4s)")


class T07GuardOutcomes(unittest.TestCase):
    def test_e78845_abort_is_stop(self):
        psql("INSERT INTO scribe_capture_provider_link VALUES ('prac_1','33333333-3333-3333-3333-333333333333')")
        try:
            rc, lines, _, _ = run()
        finally:
            psql("DELETE FROM scribe_capture_provider_link WHERE scribe_tenant_id='33333333-3333-3333-3333-333333333333'")
        pj = json.loads([l for l in lines if l.startswith("PRECHECK_JSON=")][0].split("=", 1)[1])
        self.assertIs(pj["e78845cecca6_would_abort"], True)
        self.assertIn("e78845_abort", pj["refusal_reasons"])
        self.assertEqual(pass_check.verdict(lines), ("STOP", "e78845cecca6_would_abort"))

    def test_guard_tables_missing_is_stop(self):
        psql("ALTER TABLE provider_practice_authority RENAME TO ppa_hidden")
        try:
            rc, lines, _, _ = run()
        finally:
            psql("ALTER TABLE ppa_hidden RENAME TO provider_practice_authority")
        self.assertEqual(pass_check.verdict(lines), ("STOP", "guard_tables_missing"))

    def test_null_practice_authority_is_stop(self):
        psql("INSERT INTO provider_practice_authority VALUES (NULL)")
        try:
            rc, lines, _, _ = run()
        finally:
            psql("DELETE FROM provider_practice_authority WHERE practice_id IS NULL")
        self.assertEqual(pass_check.verdict(lines), ("STOP", "G2_SHAPE=differs"))

    def test_unknown_head_is_stop(self):
        psql("UPDATE alembic_version SET version_num='aaaaaaaaaaaa'")
        try:
            rc, lines, _, _ = run()
        finally:
            psql("UPDATE alembic_version SET version_num='49126bf99c72'")
        self.assertEqual(pass_check.verdict(lines)[0], "STOP")

    def test_access_table_missing_is_stop(self):
        psql("ALTER TABLE pilot_access_grandfather_grant RENAME TO pg_hidden")
        try:
            rc, lines, _, _ = run()
        finally:
            psql("ALTER TABLE pg_hidden RENAME TO pilot_access_grandfather_grant")
        self.assertIn("ACCESS_ERROR=query_refused", lines)
        self.assertNotIn("ACCESS_ROWS=9", lines)
        self.assertEqual(pass_check.verdict(lines)[0], "STOP")


class T08SecretsAndFailures(unittest.TestCase):
    def test_wrong_password_fixed_codes_only(self):
        rc, lines, err, _ = run(url=URL.replace(SENTINEL, SENTINEL + "x"))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("PRECHECK_REFUSED=snapshot_refused", lines)
        self.assertIn("ACCESS_ERROR=query_refused", lines)

    def test_prefer_and_require_urls_never_connect(self):
        for bad in (URL.replace("verify-full", "prefer"), URL.replace("verify-full", "require")):
            rc, lines, err, _ = run(url=bad)
            self.assertIn("PRECHECK_REFUSED=configuration_refused", lines)
            self.assertIn("ACCESS_ERROR=configuration_refused", lines)

    def test_pg_env_refused(self):
        rc, lines, err, _ = run(extra_env={"PGPASSWORD": SENTINEL})
        self.assertIn("ACCESS_ERROR=configuration_refused", lines)

    def test_missing_payload_env(self):
        rc, lines, err, _ = run(extra_env={"CORE_BUNDLE_Z": "not-base64!!"})
        self.assertEqual(lines, ["RUNNER_SHA_OK=1", "RUN_START=1", "RUN_ERROR=bundle_refused"])

    def test_zz_no_secret_or_row_content_in_any_output(self):
        blob = "\n".join(ALL_OUTPUT)
        self.assertGreater(len(ALL_OUTPUT), 15)
        for f in FORBIDDEN:
            self.assertNotIn(f, blob, f)
        print(f"\n    scanned {len(ALL_OUTPUT)} runner outputs ({len(blob)} bytes) for {len(FORBIDDEN)} forbidden strings: none found")


class T09StaticContract(unittest.TestCase):
    def test_sql_literals_and_no_input_path(self):
        src = RUNNER.decode()
        self.assertNotIn('bundle["sql"]', src)
        self.assertNotIn("CORE_SQL", src)
        g = load_runner_module()
        self.assertEqual(g["ACCESS_SQL"], (PKG / "candidate-access-counts.sql").read_text())
        self.assertEqual(hashlib.sha256(g["ACCESS_SQL"].encode()).hexdigest(), "5568dd8f5aefaef199b32165412a665f5373851ce29d059f94a92270895e700a")
        self.assertEqual(len(g["ACCESS_LABELS"]), 9)
        # DATABASE_URL is read exactly once, after load_bundle()
        self.assertEqual(src.count('os.environ.get("DATABASE_URL"'), 1)
        self.assertLess(src.index("root, n = load_bundle()"), src.index('os.environ.get("DATABASE_URL"'))
        self.assertEqual(re.findall(r"print\(", src), ["print("])  # single print, inside emit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
