"""Synthetic CLI fixtures; no credentials, Azure, account or database calls."""

import copy
import importlib.util
import io
import json
import subprocess
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "staging_recall_preservation", ROOT / "infra/staging_recall_api_key_live_guard.py"
)
assert spec is not None and spec.loader is not None
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
KEY = "fictional-test-credential-do-not-use"
ENV = {
    "PYTHON_ENV": "staging",
    "API_BASE_URL": m.API_ORIGIN,
    "RECALL_API_BASE": m.RECALL_BASE,
    m.INPUT: KEY,
}


class Fixture:
    def __init__(self):
        self.info = {}
        self.revision = {}
        self.secrets = {}
        self.calls = []
        self.secret_counts = {}
        self.lock = threading.Lock()
        self.change_second_secret = False
        self.change_second_revision = False
        self.account = {"id": m.SUBSCRIPTION, "tenantId": m.TENANT, "state": "Enabled"}
        for name, container in m.WORKLOADS:
            self.info[name] = {
                "name": name,
                "latest": name + "--fixture1",
                "ready": name + "--fixture1",
                "mode": "Single",
                "traffic": [{"latestRevision": True, "weight": 100}]
                if name == m.WORKLOADS[0][0]
                else None,
            }
            self.revision[name] = {
                "health": "Healthy",
                "provisioning": "Provisioned",
                "running": "Running",
                "containers": [
                    {
                        "name": container,
                        "entries": [
                            {
                                "name": "RECALL_API_KEY",
                                "value": None,
                                "secretRef": m.SECRET,
                            },
                            {
                                "name": "RECALL_API_BASE",
                                "value": m.RECALL_BASE,
                                "secretRef": None,
                            },
                            {
                                "name": "API_BASE_URL",
                                "value": m.API_ORIGIN,
                                "secretRef": None,
                            },
                        ],
                    }
                ],
            }
            self.secrets[name] = [{"name": m.SECRET, "value": KEY}]
            self.secret_counts[name] = 0

    def run(self, args):
        with self.lock:
            self.calls.append(args)
            if args[:2] == ("account", "show"):
                return json.dumps(self.account)
            name = args[args.index("--name") + 1]
            if args[:3] == ("containerapp", "secret", "list"):
                self.secret_counts[name] += 1
                if self.change_second_secret and self.secret_counts[name] == 2:
                    return json.dumps(
                        [{"name": m.SECRET, "value": "different-fictional-key"}]
                    )
                return json.dumps(self.secrets[name])
            if args[:3] == ("containerapp", "revision", "show"):
                return json.dumps(self.revision[name])
            value = copy.deepcopy(self.info[name])
            if self.change_second_revision and self.secret_counts[name] >= 1:
                value["ready"] = name + "--fixture2"
                value["latest"] = name + "--fixture2"
            return json.dumps(value)


class PreservationTests(unittest.TestCase):
    def test_success_verifies_three_actual_owned_snapshots_without_key_output(self):
        f = Fixture()
        result = m.verify_preservation(ENV, runner=f.run)
        self.assertEqual(result["result"], "verified")
        self.assertTrue(result["before_after_owned_state_equal"])
        self.assertEqual(len(result["workloads"]), 3)
        self.assertNotIn(KEY, json.dumps(result))
        self.assertEqual(set(f.secret_counts.values()), {2})
        self.assertEqual(sum(c[:2] == ("account", "show") for c in f.calls), 2)
        for args in f.calls:
            self.assertNotIn("update", args)
            self.assertNotIn("set", args)
            if args[0] == "containerapp":
                self.assertEqual(args[args.index("--resource-group") + 1], m.GROUP)
                self.assertIn(args[args.index("--name") + 1], dict(m.WORKLOADS))

    def test_invalid_context_or_input_refuses_before_any_cli_read(self):
        for field, value in [
            ("PYTHON_ENV", "production"),
            ("API_BASE_URL", "https://app.cosmaneura.com"),
            ("RECALL_API_BASE", "https://other.invalid/api"),
            (m.INPUT, ""),
            (m.INPUT, None),
            (m.INPUT, True),
            (m.INPUT, "a\nb"),
            (m.INPUT, "a\rb"),
            (m.INPUT, "a\x00b"),
            (m.INPUT, "x" * 4097),
        ]:
            f = Fixture()
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(m.PreservationRefused),
            ):
                m.verify_preservation(ENV | {field: value}, runner=f.run)
            self.assertEqual(f.calls, [])

    def test_wrong_account_or_tenant_refuses_before_workload_reads(self):
        for field, value in [
            ("id", "other"),
            ("tenantId", "other"),
            ("state", "Disabled"),
        ]:
            f = Fixture()
            f.account[field] = value
            with self.subTest(field=field), self.assertRaises(m.PreservationRefused):
                m.verify_preservation(ENV, runner=f.run)
            self.assertEqual(len(f.calls), 1)

    def test_input_mismatch_and_one_different_live_key_refuse(self):
        for change in ("input", "live"):
            f = Fixture()
            environment = dict(ENV)
            if change == "input":
                environment[m.INPUT] = "wrong-fictional-input"
            else:
                f.secrets[m.WORKLOADS[1][0]][0]["value"] = "different-fictional-key"
            with self.subTest(change=change), self.assertRaises(m.PreservationRefused):
                m.verify_preservation(environment, runner=f.run)

    def test_key_or_revision_change_between_reads_refuses(self):
        for field in ("change_second_secret", "change_second_revision"):
            f = Fixture()
            setattr(f, field, True)
            with self.subTest(field=field), self.assertRaises(m.PreservationRefused):
                m.verify_preservation(ENV, runner=f.run)

    def test_nonready_and_changed_container_refuse(self):
        cases = [
            ("info", "latest", "unexpected--revision"),
            ("info", "mode", "Multiple"),
            ("revision", "health", "Unhealthy"),
            ("revision", "provisioning", "Failed"),
            ("revision", "running", "Stopped"),
        ]
        for target, field, value in cases:
            f = Fixture()
            getattr(f, target)[m.WORKLOADS[0][0]][field] = value
            with self.subTest(field=field), self.assertRaises(m.PreservationRefused):
                m.verify_preservation(ENV, runner=f.run)
        for containers in (
            [],
            [{"name": "unexpected", "entries": []}],
            [{"name": "a"}, {"name": "b"}],
        ):
            f = Fixture()
            f.revision[m.WORKLOADS[0][0]]["containers"] = containers
            with (
                self.subTest(containers=containers),
                self.assertRaises(m.PreservationRefused),
            ):
                m.verify_preservation(ENV, runner=f.run)

    def test_owned_ref_inline_value_duplicates_and_public_base_refuse(self):
        for change in (
            "secretRef",
            "inline",
            "duplicate",
            "missing",
            "recallBase",
            "apiOrigin",
        ):
            f = Fixture()
            entries = f.revision[m.WORKLOADS[0][0]]["containers"][0]["entries"]
            if change == "secretRef":
                entries[0]["secretRef"] = "another-secret"
            if change == "inline":
                entries[0]["value"] = KEY
            if change == "duplicate":
                entries[2] = copy.deepcopy(entries[0])
            if change == "missing":
                entries.pop()
            if change == "recallBase":
                entries[1]["value"] = "https://other.invalid"
            if change == "apiOrigin":
                entries[2]["value"] = "https://app.cosmaneura.com"
            with self.subTest(change=change), self.assertRaises(m.PreservationRefused):
                m.verify_preservation(ENV, runner=f.run)

    def test_missing_duplicate_or_changed_secret_shape_refuses(self):
        for value in (
            [],
            [{"name": "other", "value": KEY}],
            [{"name": m.SECRET, "value": KEY}, {"name": m.SECRET, "value": KEY}],
            [{"name": m.SECRET, "value": KEY, "extra": "unexpected"}],
            [{"name": m.SECRET, "value": None}],
        ):
            f = Fixture()
            f.secrets[m.WORKLOADS[0][0]] = value
            with self.subTest(value=value), self.assertRaises(m.PreservationRefused):
                m.verify_preservation(ENV, runner=f.run)

    def test_api_traffic_must_select_only_the_ready_revision(self):
        for traffic in (
            None,
            [],
            [{"latestRevision": True, "weight": 50}],
            [{"revisionName": "other--revision", "weight": 100}],
            [
                {"latestRevision": True, "weight": 100},
                {"latestRevision": True, "weight": 1},
            ],
        ):
            f = Fixture()
            f.info[m.WORKLOADS[0][0]]["traffic"] = traffic
            with (
                self.subTest(traffic=traffic),
                self.assertRaises(m.PreservationRefused),
            ):
                m.verify_preservation(ENV, runner=f.run)

    def test_provider_failure_and_invalid_json_are_sanitized(self):
        for failure in (
            lambda _: "invalid JSON containing fictional-credential",
            lambda _: (_ for _ in ()).throw(subprocess.TimeoutExpired("fictional", 15)),
        ):
            with (
                self.subTest(failure=failure),
                self.assertRaisesRegex(
                    m.PreservationRefused, "^staging_recall_key_preservation_refused$"
                ),
            ):
                m.verify_preservation(ENV, runner=failure)

    def test_azure_subprocess_gets_no_resolved_app_credentials(self):
        environment = ENV | {
            "PATH": "/usr/bin:/bin",
            "HOME": "/fictional/home",
            "DATABASE_URL": "fictional-private",
            "RECALL_API_KEY": KEY,
            "OP_SERVICE_ACCOUNT_TOKEN": "fictional-private",
        }
        with (
            patch.dict(m.os.environ, environment, clear=True),
            patch.object(
                m.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "{}", ""),
            ) as run,
        ):
            m._run_az(("account", "show"))
        args, options = run.call_args
        self.assertEqual(
            args[0][-5:],
            [
                "--subscription",
                m.SUBSCRIPTION,
                "--only-show-errors",
                "--output",
                "json",
            ],
        )
        self.assertEqual(set(options["env"]), {"PATH", "HOME", "LANG", "LC_ALL"})
        self.assertNotIn(KEY, json.dumps(options["env"]))

    def test_cli_rejection_never_discloses_key_or_provider_message(self):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.dict(m.os.environ, ENV, clear=True),
            patch.object(
                m, "verify_preservation", side_effect=m.PreservationRefused(KEY)
            ),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            self.assertEqual(m.main([]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(
            json.loads(err.getvalue()),
            {"code": "staging_recall_key_preservation_failed"},
        )

    def test_actual_backend_default_and_guarded_input_behaviors(self):
        # Execute the tracked staging template with data-only vault fixtures and
        # offline helper doubles, never its real credential or live CLI clients.
        script = r"""
set -eu
GUARD_CALLS=0
op_inject_source() {
 while IFS= read -r line; do
  if [[ "$line" =~ ^export\ ([A-Z][A-Z0-9_]*)= ]]; then
   variable=${BASH_REMATCH[1]}
   export "$variable=fixture-$variable"
  fi
 done
}
python() {
 case "$1" in
  scripts/staging_deployment_database_tls.py) printf '%s' 'fictional-db';;
  scripts/superadmin_capability_config_preflight.py) printf '%s' 'https://fictional.clerk.invalid';;
  scripts/core_cab_runtime_config.py) return 0;;
  infra/staging_recall_api_key_live_guard.py)
   GUARD_CALLS=$((GUARD_CALLS+1))
   [[ "$PYTHON_ENV" == staging && "$API_BASE_URL" == https://staging-app.cosmaneura.com ]]
   [[ "$RECALL_API_KEY_INPUT" == approved-fictional-live-key ]];;
  *) return 1;;
 esac
}
source .env.staging.backend || exit 19
printf '%s\n' "$RECALL_API_KEY" "${RECALL_API_KEY_INPUT+present}" "$GUARD_CALLS"
"""
        cases = (
            ({}, "fixture-RECALL_API_KEY", "0"),
            (
                {"RECALL_API_KEY": "ordinary-ambient-fictional-key"},
                "fixture-RECALL_API_KEY",
                "0",
            ),
            ({m.INPUT: ""}, "fixture-RECALL_API_KEY", "0"),
            (
                {m.INPUT: "approved-fictional-live-key"},
                "approved-fictional-live-key",
                "1",
            ),
        )
        for environment, key, calls in cases:
            result = subprocess.run(
                ["/bin/bash", "-c", script],
                cwd=ROOT,
                env={"PATH": "/usr/bin:/bin", **environment},
                capture_output=True,
                text=True,
                check=False,
            )
            with self.subTest(environment=environment):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [key, "", calls])
        rejected = subprocess.run(
            ["/bin/bash", "-c", script],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", m.INPUT: "unapproved-fictional-key"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(rejected.returncode, 19)
        self.assertEqual(rejected.stdout, "")
        transport = (ROOT / "just/direnv.just").read_text()
        self.assertIn('RECALL_API_KEY_INPUT="${RECALL_API_KEY_INPUT:-}"', transport)
        self.assertNotIn('RECALL_API_KEY_INPUT="${RECALL_API_KEY:-}', transport)
        self.assertIn('"RECALL_API_KEY_INPUT",', transport.split('"""', 2)[1])


if __name__ == "__main__":
    unittest.main()
