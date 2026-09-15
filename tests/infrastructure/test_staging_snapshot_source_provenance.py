"""Invented GitHub/Git fixtures; only dedicated stdout/deadline child tests run.

No real GitHub, source collector, account, review, consumer or deployment call.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "source_provenance_fixture", SCRIPTS / "staging_snapshot_source_provenance.py"
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
HEAD, SOURCE = "a" * 40, "b" * 40
MERGE_RAW = (
    "tree "
    + "c" * 40
    + "\nparent "
    + "d" * 40
    + "\nparent "
    + HEAD
    + "\nauthor Invented <fixture@example.invalid> 1 +0000\n"
    + "committer Invented <fixture@example.invalid> 1 +0000\n\nfixture\n"
).encode()
MERGE = hashlib.sha1(
    b"commit " + str(len(MERGE_RAW)).encode() + b"\0" + MERGE_RAW
).hexdigest()


def policy():
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": m.CHECKS,
            "checks": [{"context": name, "app_id": None} for name in m.CHECKS],
        },
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "required_approving_review_count": 0,
            "require_code_owner_reviews": False,
            "require_last_push_approval": False,
        },
    }


def ruleset():
    return {
        "id": 3778800,
        "target": "branch",
        "source": m.REPOSITORY,
        "source_type": "Repository",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"exclude": [], "include": ["refs/heads/master"]}},
        "rules": [
            {"type": "non_fast_forward"},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": True,
                    "required_status_checks": [{"context": "change-budget"}],
                },
            },
        ],
    }


def statuses():
    return {
        "sha": HEAD,
        "total_count": 3,
        "statuses": [{"context": name, "state": "success"} for name in m.CHECKS],
    }


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="invented-source-provenance-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.files = {
            name: ("invented " + name + "\n").encode() for name in m.SOURCE_NAMES
        }
        self.expected = {name: m.sha(raw) for name, raw in self.files.items()}
        self.responses = {
            "master-before.json": {
                "name": "master",
                "protected": True,
                "commit": {"sha": SOURCE},
            },
            "protection-before.json": policy(),
            "ruleset-before.json": ruleset(),
            "actions-before.json": {"enabled": False},
            "pull-request.json": {
                "number": 409,
                "state": "closed",
                "merged": True,
                "merge_commit_sha": MERGE,
                "head": {"sha": HEAD, "repo": {"full_name": m.REPOSITORY}},
                "base": {"ref": "master", "repo": {"full_name": m.REPOSITORY}},
            },
            "statuses.json": statuses(),
            "check-runs.json": {"total_count": 0, "check_runs": []},
        }
        for before, after in (
            ("master-before.json", "master-after.json"),
            ("protection-before.json", "protection-after.json"),
            ("ruleset-before.json", "ruleset-after.json"),
            ("actions-before.json", "actions-after.json"),
            ("statuses.json", "statuses-after.json"),
            ("check-runs.json", "check-runs-after.json"),
        ):
            self.responses[after] = copy.deepcopy(self.responses[before])
        self.git_overrides = {}
        self.commands = []
        self.clock_age, self.clock_skew = 0, 0
        self.hook = lambda _label: None
        case = self

        class InventedReader:
            def __init__(self, root):
                case.assertEqual(root, case.root)
                self.started = time.monotonic() - case.clock_age
                self.started_utc = datetime.now(UTC) - timedelta(
                    seconds=case.clock_age + case.clock_skew
                )
                self.evidence = []

            def github(self, label, route):
                case.hook(label)
                case.commands.append(("GET", route))
                raw = m.canonical(case.responses[label])
                self.evidence.append((label, raw))
                return m.record(raw)

            def git(self, label, *args, **_kwargs):
                case.commands.append(("git", *args))
                if label in case.git_overrides:
                    raw = case.git_overrides[label]
                    if isinstance(raw, Exception):
                        raise raw
                elif label == "git-origin.txt":
                    raw = ("https://github.com/" + m.REPOSITORY + ".git\n").encode()
                elif label == "git-shallow.txt":
                    raw = b"false\n"
                elif label == "git-common.txt":
                    raw = str(case.root).encode() + b"\n"
                elif label == "merge-object.txt":
                    case.assertEqual(
                        args,
                        (
                            "cat-file",
                            "commit",
                            case.responses["pull-request.json"]["merge_commit_sha"],
                        ),
                    )
                    raw = MERGE_RAW
                elif label == "merge-ancestor.txt":
                    raw = b""
                else:
                    case.assertEqual(args[:2], ("cat-file", "blob"))
                    raw = case.files[args[2].split(":scripts/")[1]]
                self.evidence.append((label, raw))
                return raw

        mocked = patch.object(m, "ReadOnlySource", InventedReader)
        mocked.start()
        self.addCleanup(mocked.stop)

    def collect(self, **changes):
        arguments = {
            "source_commit": SOURCE,
            "pr_number": 409,
            "reviewed_head": HEAD,
            "expected_files": self.expected,
            "expected_bundle_sha256": m.sha(m.canonical(self.expected)),
        }
        arguments.update(changes)
        return m.collect_provenance(self.root, **arguments)

    def test_complete_collection_matches_actual_reader_schema_and_retains_evidence(
        self,
    ):
        result = self.collect()
        body = json.loads(result.provenance)
        tree = ast.parse((SCRIPTS / "staging_snapshot_artifacts.py").read_bytes())
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_provenance"
        )
        statement = method.body[0]
        assert isinstance(statement, ast.Assign)
        call = statement.value
        assert isinstance(call, ast.Call)
        self.assertEqual(set(body), set(ast.literal_eval(call.args[1]).split()))
        self.assertEqual(body["files"], self.expected)
        self.assertEqual(
            body["pull_requests"],
            [
                {
                    "number": 409,
                    "head_commit": HEAD,
                    "merge_commit": MERGE,
                    "state": "MERGED",
                    "reviewed_files": self.expected,
                }
            ],
        )
        self.assertEqual(len(result.source_files), 18)
        self.assertEqual(dict(result.source_files), self.files)
        self.assertEqual(
            json.loads(result.evidence_hashes),
            {name: m.sha(raw) for name, raw in result.evidence},
        )
        self.assertEqual(
            len(result.evidence), len({name for name, _ in result.evidence})
        )
        self.assertLess(
            (
                datetime.now(UTC) - datetime.fromisoformat(body["observed_at"])
            ).total_seconds(),
            5,
        )
        self.assertTrue(
            all(
                cmd[0] == "GET"
                or cmd[1] in {"remote", "rev-parse", "cat-file", "merge-base"}
                for cmd in self.commands
            )
        )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_expectations_refuse_before_provider(self):
        for change in (
            {"source_commit": "master"},
            {"reviewed_head": "--option"},
            {"pr_number": True},
            {"pr_number": -1},
            {"expected_files": {}},
            {"expected_bundle_sha256": "0" * 64},
            {"expected_files": self.expected | {"extra.py": "0" * 64}},
        ):
            with self.subTest(change=list(change)):
                with self.assertRaises(m.SourceProvenanceRefused):
                    self.collect(**change)
        self.assertEqual(self.commands, [])

    def test_input_map_is_snapshotted_not_mutated_or_rebased(self):
        before = dict(self.expected)

        def mutate(label):
            if label == "master-before.json":
                self.expected[next(iter(self.expected))] = "f" * 64

        self.hook = mutate
        result = self.collect()
        self.assertEqual(json.loads(result.provenance)["files"], before)

    def test_unmerged_foreign_or_wrong_head_pr_refuses(self):
        original = copy.deepcopy(self.responses["pull-request.json"])
        variants = [
            original | {"state": "open"},
            original | {"merged": False},
            original | {"head": {"sha": "f" * 40, "repo": {"full_name": m.REPOSITORY}}},
            original
            | {"base": {"ref": "temporary", "repo": {"full_name": m.REPOSITORY}}},
            original | {"base": {"ref": "master", "repo": {"full_name": "other/repo"}}},
        ]
        for value in variants:
            with self.subTest(value=value):
                self.responses["pull-request.json"] = value
                with self.assertRaises(m.SourceProvenanceRefused):
                    self.collect()

    def test_source_or_master_drift_refuses(self):
        for label in ("master-before.json", "master-after.json"):
            original = copy.deepcopy(self.responses[label])
            for field, wrong in (
                ("commit", {"sha": "f" * 40}),
                ("protected", False),
                ("name", "staging"),
            ):
                self.responses[label] = original | {field: wrong}
                with self.subTest(label=label, field=field):
                    with self.assertRaises(m.SourceProvenanceRefused):
                        self.collect()
            self.responses[label] = original
        for prefix in ("reviewed-", "protected-"):
            label = prefix + next(iter(self.files))
            self.git_overrides[label] = b"changed source"
            with self.assertRaises(m.SourceProvenanceRefused):
                self.collect()
            self.git_overrides.clear()

    def test_bad_git_origin_shallow_grafts_or_missing_ancestry_refuses(self):
        for label, raw in (
            ("git-origin.txt", b"https://example.invalid/other.git\n"),
            ("git-shallow.txt", b"true\n"),
            ("merge-ancestor.txt", m.SourceProvenanceRefused("invented")),
        ):
            self.git_overrides[label] = raw
            with self.subTest(label=label):
                with self.assertRaises(m.SourceProvenanceRefused):
                    self.collect()
            self.git_overrides.clear()
        (self.root / "info").mkdir()
        (self.root / "info/grafts").write_bytes(b"invented")
        with self.assertRaises(m.SourceProvenanceRefused):
            self.collect()

    def test_merge_object_hash_and_two_parent_normal_merge_required(self):
        for raw, rehash in (
            (MERGE_RAW + b"changed", False),
            (MERGE_RAW.replace(("parent " + HEAD + "\n").encode(), b""), True),
            (MERGE_RAW.replace(HEAD.encode(), ("e" * 40).encode()), True),
        ):
            self.git_overrides["merge-object.txt"] = raw
            if rehash:
                self.responses["pull-request.json"]["merge_commit_sha"] = hashlib.sha1(
                    b"commit " + str(len(raw)).encode() + b"\0" + raw
                ).hexdigest()
            with self.subTest(rehash=rehash):
                with self.assertRaises(m.SourceProvenanceRefused):
                    self.collect()

    def test_protection_actions_and_statuses_rechecked_after_source(self):
        for label in (
            "protection-after.json",
            "ruleset-after.json",
            "actions-before.json",
            "actions-after.json",
            "statuses-after.json",
        ):
            before = copy.deepcopy(self.responses[label])
            if label.startswith("protection"):
                self.responses[label]["enforce_admins"]["enabled"] = False
            elif label.startswith("ruleset"):
                self.responses[label]["enforcement"] = "disabled"
            elif label.startswith("actions"):
                self.responses[label]["enabled"] = True
            else:
                self.responses[label]["statuses"][0]["state"] = "failure"
            with self.subTest(label=label):
                with self.assertRaises(m.SourceProvenanceRefused):
                    self.collect()
            self.responses[label] = before

    def test_elapsed_bound_and_clock_skew_refuse(self):
        self.clock_age = 181
        with self.assertRaises(m.SourceProvenanceRefused):
            self.collect()
        self.clock_age, self.clock_skew = 0, 10
        with self.assertRaises(m.SourceProvenanceRefused):
            self.collect()


class PolicyTests(unittest.TestCase):
    def test_current_policy_and_same_name_status_collision_rules(self):
        m.protection(policy(), ruleset())
        m.statuses(statuses(), {"total_count": 0, "check_runs": []}, HEAD)
        for conclusion in (None, "failure", "cancelled", "neutral", "skipped"):
            runs = {
                "total_count": 1,
                "check_runs": [
                    {
                        "head_sha": HEAD,
                        "name": "Backend",
                        "status": "completed",
                        "conclusion": conclusion,
                    }
                ],
            }
            with self.subTest(conclusion=conclusion):
                with self.assertRaises(m.SourceProvenanceRefused):
                    m.statuses(statuses(), runs, HEAD)
        runs = {
            "total_count": 3,
            "check_runs": [
                {
                    "head_sha": HEAD,
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                }
                for name in m.CHECKS
            ],
        }
        m.statuses({"sha": HEAD, "total_count": 0, "statuses": []}, runs, HEAD)

    def test_weakened_or_changed_protection_refuses(self):
        for key in (
            "enforce_admins",
            "required_conversation_resolution",
            "allow_force_pushes",
            "allow_deletions",
        ):
            value = policy()
            value[key]["enabled"] = not value[key]["enabled"]
            with self.assertRaises(m.SourceProvenanceRefused):
                m.protection(value, ruleset())
        value = policy()
        value["required_status_checks"]["strict"] = False
        with self.assertRaises(m.SourceProvenanceRefused):
            m.protection(value, ruleset())
        for change in (
            {"bypass_actors": [{"actor_id": 1}]},
            {"enforcement": "disabled"},
            {"rules": []},
            {"source": "other/repo"},
        ):
            with self.assertRaises(m.SourceProvenanceRefused):
                m.protection(policy(), ruleset() | change)
        wrong = ruleset()
        wrong["rules"][1]["parameters"]["strict_required_status_checks_policy"] = 1
        with self.assertRaises(m.SourceProvenanceRefused):
            m.protection(policy(), wrong)

    def test_pagination_duplicates_missing_and_pending_results_refuse(self):
        for change in (
            {"sha": "f" * 40},
            {"total_count": 101},
            {"total_count": True},
            {"total_count": 2, "statuses": statuses()["statuses"][:2]},
            {
                "total_count": 4,
                "statuses": statuses()["statuses"] + [statuses()["statuses"][0]],
            },
        ):
            with self.assertRaises(m.SourceProvenanceRefused):
                m.statuses(
                    statuses() | change, {"total_count": 0, "check_runs": []}, HEAD
                )
        value = statuses()
        value["statuses"][0]["state"] = "pending"
        with self.assertRaises(m.SourceProvenanceRefused):
            m.statuses(value, {"total_count": 0, "check_runs": []}, HEAD)

    def test_closed_arguments_reject_mutations_and_arbitrary_git_paths(self):
        git_prefix = [str(m.GIT), "--no-replace-objects"]
        for args in (
            ["fetch", "origin"],
            ["push"],
            ["status"],
            ["cat-file", "blob", HEAD + ":secret"],
            ["cat-file", "blob", HEAD + ":scripts/../secret"],
            ["remote", "set-url", "origin", "bad"],
        ):
            with self.subTest(args=args):
                with self.assertRaises(m.SourceProvenanceRefused):
                    m.closed_arguments(git_prefix + args)
        gh_prefix = [
            str(m.GH),
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
        ]
        m.closed_arguments(gh_prefix + [m.API + "/branches/master"])
        for route in (
            m.API + "/actions/secrets",
            "repos/other/repo/branches/master",
            m.API + "/pulls/1?extra=yes",
        ):
            with self.assertRaises(m.SourceProvenanceRefused):
                m.closed_arguments(gh_prefix + [route])
        gh_prefix[5] = "POST"
        with self.assertRaises(m.SourceProvenanceRefused):
            m.closed_arguments(gh_prefix + [m.API + "/branches/master"])

    def test_invalid_json_never_produces_record(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b"{", b"\xff"):
            with self.assertRaises(m.SourceProvenanceRefused):
                m.record(raw)


class BoundedReadTests(unittest.TestCase):
    def test_group_read_and_tool_revalidation_cannot_outlive_deadline(self):
        for stage in ("process", "tool_hash"):
            for end in (101.999, 102.0, 102.001):
                with self.subTest(stage=stage, end=end):
                    calls = []
                    with patch.object(m.time, "monotonic", return_value=100.0) as clock:

                        def identity(_path):
                            calls.append("hash")
                            if stage == "tool_hash" and len(calls) == 2:
                                clock.return_value = end
                            return ("invented",)

                        def run(*_args, **_kwargs):
                            if stage == "process":
                                clock.return_value = end
                            return subprocess.CompletedProcess([], 1, stdout=b"")

                        with (
                            patch.object(m, "tool_identity", side_effect=identity),
                            patch.object(m.subprocess, "run", side_effect=run),
                        ):
                            if end < 102:
                                self.assertEqual(m.group_members(123, 102), ())
                            else:
                                with self.assertRaises(m.SourceProvenanceRefused):
                                    m.group_members(123, 102)

    def test_final_group_result_requires_post_call_absolute_deadline(self):
        class Child:
            pid, returncode, waits = 123, None, 0

            def wait(self, **_kwargs):
                self.returncode, self.waits = 0, self.waits + 1

        for end in (101.999, 102.0, 102.001):
            child = Child()
            with patch.object(m.time, "monotonic", return_value=100.0) as clock:

                def observe(_pid, deadline):
                    self.assertEqual(deadline, 102)
                    if child.returncode is None:
                        return ((123, "Z"),)
                    clock.return_value = end
                    return ()

                with (
                    patch.object(m, "group_members", side_effect=observe),
                    patch.object(m.os, "killpg") as kill,
                ):
                    if end < 102:
                        m.finish_group(child)
                    else:
                        with self.assertRaises(m.SourceProvenanceRefused):
                            m.finish_group(child)
                self.assertEqual(child.waits, 1)
                kill.assert_not_called()

    def test_successful_leader_cannot_leave_closed_stdout_descendant(self):
        source = (
            "import os,time\n"
            "child=os.fork()\n"
            "if child==0:\n"
            " os.close(0);os.close(1);os.close(2);time.sleep(2);os._exit(0)\n"
            "print(str(os.getpid())+':'+str(child),flush=True)\n"
            "os._exit(0)\n"
        )
        code, raw = m.read_command(
            [sys.executable, "-I", "-S", "-B", "-c", source],
            Path.cwd().resolve(),
            {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            time.monotonic() + 3,
            64,
        )
        leader, child = (int(value) for value in raw.strip().split(b":"))
        self.assertEqual(code, 0)
        self.assertEqual(m.group_members(leader, time.monotonic() + 2), ())
        with self.assertRaises(ProcessLookupError):
            os.getpgid(child)

    def test_cleanup_cannot_signal_after_reaping_leader(self):
        calls = []

        class Child:
            pid = 123
            returncode = None

            def wait(self, **_kwargs):
                calls.append("reap")
                self.returncode = 0

        def kill(_pid, _signal):
            self.assertNotIn("reap", calls)
            calls.append("kill")

        with (
            patch.object(
                m,
                "group_members",
                side_effect=[((123, "Z"), (124, "S")), ((123, "Z"),), ()],
            ),
            patch.object(m.os, "killpg", side_effect=kill),
        ):
            m.finish_group(Child())
        self.assertEqual(calls, ["kill", "reap"])

    def test_ambiguous_pre_reap_membership_cancels_once_and_refuses(self):
        for members in ((), ((123, "Z"), (123, "Z")), ((124, "S"),)):
            for active_survivor in (False, True):
                with self.subTest(members=members, active_survivor=active_survivor):
                    calls = []
                    case = self

                    class Child:
                        pid = 123
                        returncode = None
                        survivor_active = active_survivor

                        def wait(self, **_kwargs):
                            case.assertFalse(self.survivor_active)
                            calls.append("reap")
                            self.returncode = 0

                    child = Child()

                    def kill(pid, sig):
                        self.assertEqual((pid, sig), (123, m.signal.SIGKILL))
                        self.assertIsNone(child.returncode)
                        child.survivor_active = False
                        calls.append("kill")

                    with (
                        patch.object(m, "group_members", return_value=members),
                        patch.object(m.os, "killpg", side_effect=kill),
                    ):
                        with self.assertRaises(m.SourceProvenanceRefused):
                            m.finish_group(child)
                    self.assertEqual(calls, ["kill", "reap"])

    def test_inspection_failure_or_post_reap_survivor_never_signals_again(self):
        for observations in (
            [
                m.SourceProvenanceRefused("invented denied inspection"),
                ((123, "Z"),),
                (),
            ],
            [((123, "Z"), (124, "S")), ((123, "Z"),), ((124, "S"),)],
        ):
            with self.subTest(observations=observations):
                calls = []

                class Child:
                    pid = 123
                    returncode = None

                    def wait(self, **_kwargs):
                        calls.append("reap")
                        self.returncode = 0

                with (
                    patch.object(m, "group_members", side_effect=observations),
                    patch.object(
                        m.os, "killpg", side_effect=lambda *_: calls.append("kill")
                    ),
                ):
                    with self.assertRaises(m.SourceProvenanceRefused):
                        m.finish_group(Child())
                self.assertEqual(calls, ["kill", "reap"])

    def test_proven_lone_zombie_is_reaped_without_a_signal(self):
        class Child:
            pid = 123
            returncode = None

            def wait(self, **_kwargs):
                self.returncode = 0

        with (
            patch.object(m, "group_members", side_effect=[((123, "Z"),), ()]),
            patch.object(m.os, "killpg") as kill,
        ):
            child = Child()
            m.finish_group(child)
            self.assertEqual(child.returncode, 0)
            kill.assert_not_called()

    def test_tool_pin_identity_content_type_and_mode(self):
        with tempfile.TemporaryDirectory(prefix="invented-public-tool-") as tmp:
            leaf = Path(tmp) / "tool"
            leaf.write_bytes(b"invented public tool")
            leaf.chmod(0o500)
            with patch.dict(m.TOOL_PINS, {leaf: (20, m.sha(b"invented public tool"))}):
                first = m.tool_identity(leaf)
                self.assertEqual(m.tool_identity(leaf), first)
                leaf.chmod(0o700)
                leaf.write_bytes(b"changed  public tool")
                leaf.chmod(0o500)
                with self.assertRaises(m.SourceProvenanceRefused):
                    m.tool_identity(leaf)
                leaf.chmod(0o520)
                with self.assertRaises(m.SourceProvenanceRefused):
                    m.tool_identity(leaf)
            link = Path(tmp) / "link"
            link.symlink_to(leaf)
            with patch.dict(m.TOOL_PINS, {link: (20, m.sha(b"invented public tool"))}):
                with self.assertRaises(m.SourceProvenanceRefused):
                    m.tool_identity(link)

    def test_real_dedicated_child_output_and_timeout_cleanup(self):
        root = Path.cwd().resolve()
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        actual = subprocess.Popen
        children = []

        def start(*args, **kwargs):
            process = actual(*args, **kwargs)
            children.append(process)
            return process

        with patch.object(m.subprocess, "Popen", side_effect=start):
            code, raw = m.read_command(
                [sys.executable, "-I", "-S", "-B", "-c", "print('public fixture')"],
                root,
                environment,
                time.monotonic() + 3,
                64,
            )
            self.assertEqual((code, raw), (0, b"public fixture\n"))
            for source, maximum, seconds in (
                ("print('x' * 100000)", 64, 3),
                ("import time; time.sleep(10)", 64, 0.1),
            ):
                with self.assertRaises(m.SourceProvenanceRefused):
                    m.read_command(
                        [sys.executable, "-I", "-S", "-B", "-c", source],
                        root,
                        environment,
                        time.monotonic() + seconds,
                        maximum,
                    )
        self.assertTrue(all(process.returncode is not None for process in children))
        self.assertTrue(all(process.stdout.closed for process in children))

    def test_closed_reader_does_not_inherit_token_or_proxy(self):
        with tempfile.TemporaryDirectory(prefix="invented-read-only-root-") as tmp:
            with (
                patch.object(m, "tool_identity", return_value=("invented",)),
                patch.dict(
                    os.environ, {"GH_TOKEN": "invented", "https_proxy": "invented"}
                ),
            ):
                reader = m.ReadOnlySource(Path(tmp).resolve())
                self.assertNotIn("GH_TOKEN", reader.environment)
                self.assertNotIn("https_proxy", reader.environment)
                with patch.object(
                    m, "read_command", return_value=(0, b'{"fixture":true}')
                ) as run:
                    self.assertEqual(
                        reader.github("fixture.json", m.API + "/branches/master"),
                        {"fixture": True},
                    )
                    argv = run.call_args.args[0]
                    self.assertEqual(argv[5], "GET")
                    self.assertEqual(argv[3], "github.com")
                    with self.assertRaises(m.SourceProvenanceRefused):
                        reader.git("bad.txt", "fetch", "origin")
                    self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
