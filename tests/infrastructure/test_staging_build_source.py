"""Invented read-only transport fixtures; no Git/GitHub/build/native execution."""

import hashlib
import importlib
import json
import stat
import sys
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    m = importlib.import_module("staging_build_source")

CANDIDATE = "1" * 40
CONTROLLER = "2" * 40
REVIEWED = "3" * 40
USER = {"id": 17, "login": "JohnClem", "type": "User"}
USER_SHA = m.sha(m.source.canonical(USER))
ARCHIVE = b"INVENTED_NOT_AN_EXECUTABLE_SOURCE_ARCHIVE"


def request():
    return m.SourceRequest(
        m.candidate.HumanCandidateContext(
            CANDIDATE, "385", "4" * 12, CONTROLLER, m.source.REPOSITORY
        ),
        USER_SHA,
        m.sha(ARCHIVE),
        419,
        REVIEWED,
        101,
        102,
        "b" * 64,
        tuple((name, "a" * 64) for name in sorted(m.MODULES)),
    )


def reader():
    obj = object.__new__(m.BuildSourceReader)
    obj.root, obj.request = ROOT, request()
    obj.started_utc = datetime.now(UTC)
    obj.deadline = time.monotonic() + 180
    obj.tools = {}
    obj.environment = {"PATH": "/usr/bin:/bin", "HOME": "/invented/user"}
    obj.sequence, obj.response_hashes = 0, []
    return obj


class RequestTests(unittest.TestCase):
    def test_exact_request_and_closed_module_set(self):
        original = request()
        m.validate_request(original)
        cases = [
            replace(original, controller_pr=True),
            replace(original, controller_review_id=0),
            replace(original, controller_check_id=10**15),
            replace(original, controller_reviewed_head="HEAD"),
            replace(original, source_archive_sha256="x" * 64),
            replace(original, github_user_sha256=""),
            replace(original, controller_review_sha256=""),
            replace(original, production_files=original.production_files[:-1]),
            replace(
                original,
                production_files=original.production_files
                + original.production_files[:1],
            ),
            replace(original, production_files=(([], "a" * 64),)),
            replace(
                original,
                context=replace(original.context, repository="wrong/repository"),
            ),
            replace(
                original, context=replace(original.context, candidate_sha="master")
            ),
            replace(
                original,
                context=replace(original.context, approval_pr_number="385/merge"),
            ),
            replace(
                original,
                context=replace(original.context, expected_schema_after="heads"),
            ),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(m.BuildSourceRefused):
                m.validate_request(value)

    def test_only_fixed_read_only_routes_and_argv(self):
        obj = reader()
        for args in (
            ["remote", "get-url", "origin"],
            ["rev-parse", "HEAD"],
            ["show", CANDIDATE + ":alembic.ini"],
            ["show", CANDIDATE + ":migrations/versions/a_123.py"],
            ["show", CONTROLLER + ":scripts/staging_build_source.py"],
            ["cat-file", "-e", CANDIDATE + "^{commit}"],
        ):
            obj.allowed_git(args)
        for args in (
            ["fetch", "origin"],
            ["reset", "--hard"],
            ["show", CANDIDATE + ":.env"],
            ["show", CANDIDATE + ":migrations/versions/../secret.py"],
            ["show", "HEAD:scripts/staging_build_source.py"],
            ["archive", "--remote=anything", CANDIDATE],
        ):
            with self.subTest(args=args), self.assertRaises(m.BuildSourceRefused):
                obj.allowed_git(args)
        for route in (
            "user",
            m.source.API + "/branches/master",
            m.source.API + "/pulls/419/reviews/101",
        ):
            obj.allowed_route(route)
        for route in (
            "user/keys",
            "repos/other/repo/branches/master",
            m.source.API + "/pulls/419/merge",
            m.source.API + "/actions/workflows",
        ):
            with self.subTest(route=route), self.assertRaises(m.BuildSourceRefused):
                obj.allowed_route(route)


class TransportTests(unittest.TestCase):
    def test_real_adapter_supplies_clean_fixed_get_only_transport(self):
        obj = reader()
        with patch.object(
            m.source, "read_command", return_value=(0, b'{"enabled":false}')
        ) as run:
            self.assertEqual(
                obj.gh(m.source.API + "/actions/permissions"), {"enabled": False}
            )
        argv, cwd, env, deadline, maximum = run.call_args.args
        self.assertEqual(argv[0], str(m.source.GH))
        self.assertEqual(argv[4:6], ["--method", "GET"])
        self.assertEqual(cwd, ROOT)
        self.assertEqual(env, obj.environment)
        self.assertLessEqual(deadline, obj.deadline)
        self.assertEqual(maximum, 8 * 1024**2)
        self.assertEqual(
            obj.response_hashes, [("observation-1", m.sha(b'{"enabled":false}'))]
        )

    def test_mutating_or_ambiguous_commands_never_reach_process_runner(self):
        obj = reader()
        with patch.object(m.source, "read_command") as run:
            for argv in (
                [str(m.source.GIT), "--no-replace-objects", "fetch", "origin"],
                [str(m.source.GH), "api", "--method", "POST", "user"],
                ["/bin/sh", "-c", "anything"],
                [str(m.source.GH), "auth", "token"],
            ):
                with self.subTest(argv=argv), self.assertRaises(m.BuildSourceRefused):
                    obj.read(argv)
            run.assert_not_called()

    def test_changed_tools_nonzero_reads_and_expiry_refuse(self):
        obj = reader()
        with patch.object(m.source, "read_command", return_value=(1, b"failure")):
            with self.assertRaises(m.BuildSourceRefused):
                obj.git_bytes("rev-parse", "HEAD")
        obj.tools = {m.source.GIT: ("expected",)}
        with (
            patch.object(m.source, "tool_identity", return_value=("changed",)),
            patch.object(m.source, "read_command") as run,
        ):
            with self.assertRaises(m.BuildSourceRefused):
                obj.git_bytes("rev-parse", "HEAD")
            run.assert_not_called()
        obj.deadline = time.monotonic() - 1
        with self.assertRaises(m.BuildSourceRefused):
            obj.tools_check()

    def test_configured_user_is_verified_and_minimized_not_presence(self):
        obj = reader()
        user = USER | {
            "private_email": "invented-fixture@example.invalid",
            "other": "not-retained",
        }
        with patch.object(obj, "gh", return_value=user):
            raw = obj.stdout(
                ["gh", "api", "--hostname", "github.com", "--method", "GET", "user"]
            )
        self.assertEqual(json.loads(raw), USER)
        for value in (
            USER | {"id": True},
            USER | {"id": 18},
            USER | {"login": "other"},
            USER | {"type": "Bot"},
        ):
            with (
                patch.object(obj, "gh", return_value=value),
                self.assertRaises(m.BuildSourceRefused),
            ):
                obj.stdout(["gh", "api", "--hostname", "github.com", "user"])

    def test_git_archive_is_generated_by_fixed_command_and_hash_bound(self):
        obj = reader()
        owner = Mock(child=None, cleanup_failed=False)
        owner.run.side_effect = lambda argv, env, maximum, sink, **kw: sink(ARCHIVE)
        with (
            patch.object(m.resource, "getrlimit", return_value=(0, 0)),
            patch.object(m.process, "BuildProcesses", return_value=owner),
        ):
            self.assertEqual(obj.archive(), ARCHIVE)
        args = owner.run.call_args.args
        self.assertEqual(
            args[0],
            [
                str(m.source.GIT),
                "--no-replace-objects",
                "archive",
                "--format=tar",
                CANDIDATE,
            ],
        )
        self.assertEqual(args[2], m.inputs.MAX_SOURCE_BYTES)
        self.assertEqual(owner.run.call_args.kwargs, {"cwd": ROOT})
        for changed, dirty in ((b"changed", False), (ARCHIVE, True)):
            owner.cleanup_failed = dirty
            owner.run.side_effect = lambda argv, env, maximum, sink, **kw: sink(changed)
            with (
                patch.object(m.resource, "getrlimit", return_value=(0, 0)),
                patch.object(m.process, "BuildProcesses", return_value=owner),
                self.assertRaises(m.BuildSourceRefused),
            ):
                obj.archive()

    def test_archive_never_runs_with_core_dumps_enabled(self):
        with (
            patch.object(m.resource, "getrlimit", return_value=(1, 1)),
            patch.object(m.process, "BuildProcesses") as runner,
        ):
            with self.assertRaises(m.BuildSourceRefused):
                reader().archive()
            runner.assert_not_called()

    def test_final_boundary_rejects_master_move_and_changed_identity(self):
        obj = reader()
        good_branch = {
            "name": "master",
            "protected": True,
            "commit": {"sha": CONTROLLER},
        }

        def response(argv):
            if argv[0] == "gh":
                return json.dumps(USER)
            if argv[1:3] == ["remote", "get-url"]:
                return "https://github.com/" + m.source.REPOSITORY + ".git"
            return CONTROLLER if argv[1] == "rev-parse" else ""

        with (
            patch.object(obj, "local_closure"),
            patch.object(obj, "stdout", side_effect=response),
            patch.object(obj, "gh", return_value=good_branch),
        ):
            obj.final_boundaries()
        for branch in (
            good_branch | {"protected": False},
            good_branch | {"commit": {"sha": "f" * 40}},
        ):
            with (
                patch.object(obj, "local_closure"),
                patch.object(obj, "stdout", side_effect=response),
                patch.object(obj, "gh", return_value=branch),
                self.assertRaises(m.BuildSourceRefused),
            ):
                obj.final_boundaries()


class ControllerProofTests(unittest.TestCase):
    def fixture(self):
        obj = reader()
        blobs = {name: ("INVENTED SOURCE " + name).encode() for name in m.MODULES}
        obj.request = replace(
            obj.request,
            production_files=tuple(
                (name, m.sha(raw)) for name, raw in sorted(blobs.items())
            ),
        )
        head = REVIEWED
        commit = (
            "tree "
            + "6" * 40
            + "\nparent "
            + "4" * 40
            + "\nparent "
            + head
            + "\n\nfixture\n"
        ).encode()
        merged = hashlib.sha1(
            b"commit " + str(len(commit)).encode() + b"\0" + commit
        ).hexdigest()
        pull = {
            "number": 419,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": merged,
            "base": {"ref": "master", "repo": {"full_name": m.source.REPOSITORY}},
            "head": {"sha": head, "repo": {"full_name": m.source.REPOSITORY}},
        }
        review = {
            "id": 101,
            "commit_id": REVIEWED,
            "user": {"login": "cursor[bot]", "type": "Bot"},
            "state": "COMMENTED",
            "body": (
                "<!-- BUGBOT_REVIEW -->\n"
                "Bugbot reviewed your changes and found no new issues!\n"
                f"Reviewed by Cursor Bugbot for commit {REVIEWED}."
            ),
        }
        obj.request = replace(
            obj.request, controller_review_sha256=m.sha(m.source.canonical(review))
        )
        check = {
            "id": 102,
            "head_sha": REVIEWED,
            "name": "Cursor Bugbot",
            "app": {"slug": "cursor", "id": 1210556},
            "status": "completed",
            "conclusion": "success",
        }
        values = {
            "pulls/419": pull,
            "pulls/419/reviews/101": review,
            "check-runs/102": check,
        }

        def gh(route):
            suffix = route.removeprefix(m.source.API + "/")
            return values.get(suffix, {"invented": True})

        def git(*args):
            if args[:2] == ("cat-file", "commit"):
                return commit
            if args[:2] == ("merge-base", "--is-ancestor"):
                return b""
            return blobs[args[-1].partition(":scripts/")[2]]

        obj.gh = Mock(side_effect=gh)
        obj.git_bytes = Mock(side_effect=git)
        return obj, values, blobs

    def test_normal_merge_exact_review_and_entire_declared_closure(self):
        obj, _, blobs = self.fixture()
        with (
            patch.object(m.source, "statuses") as statuses,
            patch.object(
                Path,
                "read_bytes",
                autospec=True,
                side_effect=lambda path: blobs[path.name],
            ),
        ):
            obj.controller()
        statuses.assert_called_once()
        self.assertEqual(
            len(
                [
                    call
                    for call in obj.git_bytes.call_args_list
                    if call.args[0] == "show"
                ]
            ),
            2 * len(m.MODULES),
        )

    def test_squash_merge_requires_identical_complete_reviewed_tree(self):
        for failure in (None, "tree", "object", "parent_count", "bugbot", "drift"):
            obj, values, blobs = self.fixture()
            reviewed = (
                "tree " + "6" * 40 + "\nparent " + "4" * 40 + "\n\nreview\n"
            ).encode()
            head = hashlib.sha1(
                b"commit " + str(len(reviewed)).encode() + b"\0" + reviewed
            ).hexdigest()
            tree = "5" * 40 if failure == "tree" else "6" * 40
            parents = ("parent " + "4" * 40 + "\n") * (
                3 if failure == "parent_count" else 1
            )
            merged = ("tree " + tree + "\n" + parents + "\nsquash\n").encode()
            merged_id = hashlib.sha1(
                b"commit " + str(len(merged)).encode() + b"\0" + merged
            ).hexdigest()
            pull = values["pulls/419"]
            pull["head"]["sha"], pull["merge_commit_sha"] = head, merged_id
            report = "Implementer review; synthetic fixture only.\n"
            metadata = dict(
                reviewer="Codex",
                reviewed_head=head,
                result="accepted",
                after_final_substantive_push=True,
                report_sha256=m.sha(report.encode()),
            )
            body = (
                "<!-- COSMANEURA_LOCAL_AGENT_REVIEW "
                + json.dumps(metadata)
                + " -->\n"
                + report
            )
            values["issues/comments/101"] = dict(
                id=101,
                user=USER,
                issue_url="https://api.github.com/repos/CosmaNeura/cosmaneura/issues/419",
                body=body,
            )
            obj.request = replace(
                obj.request,
                controller_reviewed_head=head,
                review_kind="bugbot" if failure == "bugbot" else "local_cursor",
                controller_check_id=0,
                controller_review_sha256=m.sha(body.encode()),
            )

            def git(*args):
                if args[:2] == ("cat-file", "commit"):
                    if args[2] == head:
                        return reviewed + (b"tampered" if failure == "object" else b"")
                    return merged
                if args[0] == "diff":
                    return b"app/changed.py\n" if failure == "drift" else b""
                if args[0] == "merge-base":
                    return b""
                return blobs[args[-1].partition(":scripts/")[2]]

            obj.git_bytes = Mock(side_effect=git)
            with (
                self.subTest(failure=failure),
                patch.object(
                    Path,
                    "read_bytes",
                    autospec=True,
                    side_effect=lambda path: blobs[path.name],
                ),
                patch.object(m.source, "statuses") as statuses,
            ):
                if failure:
                    with self.assertRaises(m.BuildSourceRefused):
                        obj.controller()
                else:
                    obj.controller()
                statuses.assert_not_called()

    def test_wrong_review_scope_pending_check_or_unmerged_controller_refuses(self):
        cases = [
            ("pulls/419", "merged", False),
            ("pulls/419", "number", 420),
            ("pulls/419/reviews/101", "commit_id", "f" * 40),
            ("pulls/419/reviews/101", "user", {"login": "JohnClem"}),
            ("pulls/419/reviews/101", "state", "DISMISSED"),
            (
                "pulls/419/reviews/101",
                "body",
                "Changed review with unresolved findings",
            ),
            ("pulls/419/reviews/101", "body", ""),
            ("pulls/419/reviews/101", "user", {"login": "cursor[bot]", "type": "User"}),
            ("check-runs/102", "app", {"slug": "other"}),
            ("check-runs/102", "app", {"slug": "cursor", "id": 7}),
            ("check-runs/102", "name", "Other check"),
            ("check-runs/102", "status", "in_progress"),
            ("check-runs/102", "conclusion", "neutral"),
            ("check-runs/102", "head_sha", "f" * 40),
        ]
        for route, key, value in cases:
            obj, values, blobs = self.fixture()
            values[route][key] = value
            with (
                self.subTest(route=route, key=key),
                patch.object(m.source, "statuses"),
                patch.object(
                    Path,
                    "read_bytes",
                    autospec=True,
                    side_effect=lambda path: blobs[path.name],
                ),
                self.assertRaises(m.BuildSourceRefused),
            ):
                obj.controller()

    def test_hash_bound_finding_review_and_nonexact_pr_head_refuse(self):
        obj, values, blobs = self.fixture()
        review = values["pulls/419/reviews/101"]
        review["body"] = "<!-- BUGBOT_REVIEW -->\nBugbot found a high severity issue."
        obj.request = replace(
            obj.request,
            controller_review_sha256=m.sha(m.source.canonical(review)),
        )
        with (
            patch.object(m.source, "statuses"),
            patch.object(
                Path,
                "read_bytes",
                autospec=True,
                side_effect=lambda path: blobs[path.name],
            ),
            self.assertRaises(m.BuildSourceRefused),
        ):
            obj.controller()

        obj, values, blobs = self.fixture()
        values["pulls/419"]["head"]["sha"] = "5" * 40
        with (
            patch.object(m.source, "statuses"),
            patch.object(
                Path,
                "read_bytes",
                autospec=True,
                side_effect=lambda path: blobs[path.name],
            ),
            self.assertRaises(m.BuildSourceRefused),
        ):
            obj.controller()

    def test_changed_local_closure_and_failed_required_statuses_refuse(self):
        obj, _, _ = self.fixture()
        with (
            patch.object(m.source, "statuses"),
            patch.object(Path, "read_bytes", return_value=b"CHANGED"),
            self.assertRaises(m.BuildSourceRefused),
        ):
            obj.controller()
        obj, _, blobs = self.fixture()
        with (
            patch.object(
                m.source,
                "statuses",
                side_effect=m.source.SourceProvenanceRefused("fixture"),
            ),
            patch.object(
                Path,
                "read_bytes",
                autospec=True,
                side_effect=lambda path: blobs[path.name],
            ),
            self.assertRaises(m.source.SourceProvenanceRefused),
        ):
            obj.controller()


class CollectionTests(unittest.TestCase):
    def test_full_collection_brackets_archive_and_never_grants_execution(self):
        obj = reader()
        order = []

        def git(*args):
            return (
                b"false\n"
                if args[-1] == "--is-shallow-repository"
                else b"/private/tmp\n"
            )

        obj.git_bytes = Mock(side_effect=git)
        obj.protections = Mock(
            side_effect=lambda: order.append("protections") or "b" * 64
        )
        obj.controller = Mock(side_effect=lambda: order.append("controller"))
        obj.archive = Mock(side_effect=lambda: order.append("archive") or ARCHIVE)
        obj.final_boundaries = Mock(side_effect=lambda: order.append("final"))
        verified = {"github_user_sha256": USER_SHA, "execution_authorized": False}
        with (
            patch.object(m.resource, "getrlimit", return_value=(0, 0)),
            patch.object(m, "BuildSourceReader", return_value=obj),
            patch.object(
                m.candidate,
                "verify_human_candidate",
                side_effect=lambda *a, **k: order.append("candidate") or verified,
            ),
        ):
            result = m.collect_build_source(ROOT, obj.request)
        self.assertEqual(
            order,
            [
                "protections",
                "controller",
                "candidate",
                "archive",
                "candidate",
                "protections",
                "controller",
                "final",
            ],
        )
        self.assertEqual(result.source_archive, ARCHIVE)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.deployment_authorized)
        self.assertEqual(result.github_user_sha256, USER_SHA)


class ClosureTests(unittest.TestCase):
    def test_owned_stable_closure_and_read_time_replacement(self):
        obj = reader()
        blob = b"INVENTED SOURCE ONLY"
        obj.request = replace(
            obj.request,
            production_files=tuple((name, m.sha(blob)) for name in sorted(m.MODULES)),
        )
        metadata = dict(
            st_dev=1,
            st_ino=2,
            st_uid=m.os.geteuid(),
            st_mode=stat.S_IFREG | 0o644,
            st_nlink=1,
            st_size=len(blob),
            st_mtime_ns=3,
            st_ctime_ns=4,
        )
        with (
            patch.object(Path, "resolve", autospec=True, side_effect=lambda path: path),
            patch.object(Path, "read_bytes", return_value=blob),
            patch.object(Path, "lstat", return_value=SimpleNamespace(**metadata)),
        ):
            obj.local_closure()
        for changes in (
            {"st_uid": m.os.geteuid() + 1},
            {"st_mode": stat.S_IFREG | 0o666},
            {"st_nlink": 2},
            {"st_size": 0},
            {"st_ino": 99},
            {"st_ctime_ns": 99},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(
                    Path, "resolve", autospec=True, side_effect=lambda path: path
                ),
                patch.object(Path, "read_bytes", return_value=blob),
                patch.object(
                    Path,
                    "lstat",
                    side_effect=[
                        SimpleNamespace(**metadata),
                        SimpleNamespace(**(metadata | changes)),
                    ],
                ),
                self.assertRaises(m.BuildSourceRefused),
            ):
                obj.local_closure()


class CandidateIntegrationTests(unittest.TestCase):
    def fixture(self):
        obj = reader()
        calls = []
        values = {
            "user": USER,
            m.source.API + "/branches/master": {
                "name": "master",
                "protected": True,
                "commit": {"sha": CONTROLLER},
            },
            m.source.API + "/pulls/385": {
                "state": "closed",
                "merged_at": "2030-01-01T00:00:00Z",
                "base": {"ref": "master"},
                "merge_commit_sha": CANDIDATE,
            },
        }
        git = {
            ("remote", "get-url", "origin"): (
                "https://github.com/" + m.source.REPOSITORY + ".git\n"
            ).encode(),
            ("status", "--porcelain=v1", "--untracked-files=all"): b"",
            ("rev-parse", "HEAD"): (CONTROLLER + "\n").encode(),
            ("cat-file", "-e", CANDIDATE + "^{commit}"): b"",
            ("merge-base", "--is-ancestor", CANDIDATE, CONTROLLER): b"",
            (
                "show",
                CANDIDATE + ":alembic.ini",
            ): b"[alembic]\nscript_location = migrations\n",
            ("ls-tree", "-z", CANDIDATE + ":migrations/versions"): (
                "100644 blob " + "9" * 40 + "\ta_123.py\0"
            ).encode(),
            (
                "show",
                CANDIDATE + ":migrations/versions/a_123.py",
            ): b"revision = '444444444444'\ndown_revision = None\n",
        }

        def transport(argv, _root, _env, _deadline, _maximum):
            calls.append(argv)
            if argv[0] == str(m.source.GH):
                return 0, m.source.canonical(values[argv[-1]])
            return 0, git[tuple(argv[2:])]

        return obj, values, git, calls, transport

    def test_real_candidate_verifier_through_real_adapter(self):
        obj, _, _, calls, transport = self.fixture()
        with patch.object(m.source, "read_command", side_effect=transport):
            result = m.candidate.verify_human_candidate(
                obj.request.context,
                stdout=obj.stdout,
                commit_exists=obj.commit_exists,
                is_ancestor=obj.is_ancestor,
            )
        self.assertEqual(result["candidate_sha"], CANDIDATE)
        self.assertEqual(result["schema_head"], "4" * 12)
        self.assertEqual(result["github_user_sha256"], USER_SHA)
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(len(calls), len(obj.response_hashes))
        self.assertEqual(sum(argv[-1] == "user" for argv in calls), 2)

    def test_real_candidate_path_rejects_provider_and_schema_changes(self):
        for change in ("user", "master", "candidate", "schema"):
            obj, values, git, _, transport = self.fixture()
            if change == "user":
                values["user"] = USER | {"id": 18}
            elif change == "master":
                values[m.source.API + "/branches/master"]["commit"] = {"sha": "f" * 40}
            elif change == "candidate":
                values[m.source.API + "/pulls/385"]["merge_commit_sha"] = "f" * 40
            else:
                git[("show", CANDIDATE + ":migrations/versions/a_123.py")] = (
                    b"revision = '555555555555'\ndown_revision = None\n"
                )
            with (
                self.subTest(change=change),
                patch.object(m.source, "read_command", side_effect=transport),
                self.assertRaises(
                    (m.BuildSourceRefused, m.candidate.CandidateVerificationError)
                ),
            ):
                m.candidate.verify_human_candidate(
                    obj.request.context,
                    stdout=obj.stdout,
                    commit_exists=obj.commit_exists,
                    is_ancestor=obj.is_ancestor,
                )


if __name__ == "__main__":
    unittest.main()


class LocalCursorReviewTests(unittest.TestCase):
    def test_agent_review_preserves_identity_source_and_report_binding(self):
        report = "Independent final-source review.\n"
        metadata = {
            "reviewer": "Codex",
            "reviewed_head": REVIEWED,
            "result": "accepted",
            "after_final_substantive_push": True,
            "report_sha256": m.sha(report.encode()),
        }

        def verify(meta, *, prefix="AGENT", **changes):
            comment = {
                "id": 101,
                "user": {"login": "JohnClem", "type": "User"},
                "issue_url": "https://api.github.com/repos/CosmaNeura/cosmaneura/issues/419",
                "body": "<!-- COSMANEURA_LOCAL_"
                + prefix
                + "_REVIEW "
                + json.dumps(meta)
                + " -->\n"
                + report,
            }
            comment.update(changes)
            selected = replace(
                request(),
                review_kind="local_cursor",
                controller_check_id=0,
                controller_review_sha256=m.sha(comment["body"].encode()),
            )
            m.validate_request(selected)
            m.validate_local_cursor_review(comment, selected)

        for reviewer in ("Codex", "Claude Code"):
            with self.subTest(reviewer=reviewer):
                verify({**metadata, "reviewer": reviewer})
        for key, value in (
            ("reviewer", "Cursor"),
            ("reviewer", "unknown"),
            ("reviewer", []),
            ("reviewed_head", "f" * 40),
            ("result", "changes_requested"),
            ("after_final_substantive_push", False),
            ("report_sha256", "a" * 64),
            ("extra_field", True),
        ):
            with (
                self.subTest(key=key, value=value),
                self.assertRaises(m.BuildSourceRefused),
            ):
                verify({**metadata, key: value})
        for changes in (
            {"id": 102},
            {"user": {"login": "someone-else", "type": "User"}},
            {"user": {"login": "JohnClem", "type": "Bot"}},
            {
                "issue_url": "https://api.github.com/repos/CosmaNeura/cosmaneura/issues/420"
            },
        ):
            with self.subTest(changes=changes), self.assertRaises(m.BuildSourceRefused):
                verify(metadata, **changes)
        with self.assertRaises(m.BuildSourceRefused):
            verify(metadata, prefix="CURSOR")

    def test_local_review_binds_report_head_author_and_pull_request(self):
        metadata = {
            "reviewer": "Cursor",
            "reviewed_head": REVIEWED,
            "result": "accepted",
            "after_final_substantive_push": True,
            "report_sha256": m.sha(b"Independent report.\n"),
        }

        def comment(meta):
            return {
                "id": 101,
                "user": {"login": "JohnClem", "type": "User"},
                "issue_url": "https://api.github.com/repos/CosmaNeura/cosmaneura/issues/419",
                "body": "<!-- COSMANEURA_LOCAL_CURSOR_REVIEW "
                + json.dumps(meta)
                + " -->\nIndependent report.\n",
            }

        good = comment(metadata)
        selected = replace(
            request(),
            review_kind="local_cursor",
            controller_check_id=0,
            controller_review_sha256=m.sha(good["body"].encode()),
        )
        m.validate_request(selected)
        m.validate_local_cursor_review(good, selected)
        for key, value in (
            ("reviewed_head", "f" * 40),
            ("result", "changes_requested"),
            ("after_final_substantive_push", False),
            ("report_sha256", "a" * 64),
        ):
            changed = comment({**metadata, key: value})
            with self.subTest(key=key), self.assertRaises(m.BuildSourceRefused):
                m.validate_local_cursor_review(
                    changed,
                    replace(
                        selected,
                        controller_review_sha256=m.sha(changed["body"].encode()),
                    ),
                )
        for key, value in (
            (
                "issue_url",
                "https://api.github.com/repos/CosmaNeura/cosmaneura/issues/420",
            ),
            ("user", {"login": "someone-else", "type": "User"}),
            ("body", good["body"] + "Edited report."),
        ):
            with self.subTest(key=key), self.assertRaises(m.BuildSourceRefused):
                m.validate_local_cursor_review({**good, key: value}, selected)
