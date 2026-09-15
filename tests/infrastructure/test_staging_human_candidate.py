"""Pure invented source/identity responses; no CLI, account, network or release."""

import importlib.util
import io
import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "human_candidate_fixture",
    Path(__file__).resolve().parents[2] / "scripts/verify_staging_candidate.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
CANDIDATE, MASTER, SCHEMA = "a" * 40, "b" * 40, "c" * 12


class HumanSourceTests(unittest.TestCase):
    def setUp(self):
        self.context = m.HumanCandidateContext(
            CANDIDATE, "385", SCHEMA, MASTER, m.REPOSITORY
        )
        self.user = {"login": m.FOUNDER_LOGIN, "id": 123, "type": "User"}
        self.commands = []
        self.user_reads = self.status_reads = 0
        self.hook = lambda _command: None
        self.ancestor = True
        self.pull = {
            "state": "closed",
            "merged_at": "2030-01-01T00:00:00Z",
            "merge_commit_sha": CANDIDATE,
            "base": {"ref": "master"},
        }
        schema = patch.object(m, "_candidate_schema_head", return_value=SCHEMA)
        schema.start()
        self.addCleanup(schema.stop)

    def read(self, command):
        self.commands.append(command)
        override = self.hook(command)
        if override is not None:
            return override
        if command[-1] == "user":
            self.user_reads += 1
            return json.dumps(self.user)
        if command[:3] == ["git", "remote", "get-url"]:
            return "https://github.com/" + m.REPOSITORY + ".git"
        if command[:2] == ["git", "status"]:
            self.status_reads += 1
            return ""
        if command == ["git", "rev-parse", "HEAD"]:
            return MASTER
        if command[-1].endswith("/branches/master"):
            return json.dumps(
                {"name": "master", "protected": True, "commit": {"sha": MASTER}}
            )
        if command[-1].endswith("/pulls/385"):
            return json.dumps(self.pull)
        raise AssertionError("unexpected invented command")

    def verify(self, **changes):
        return m.verify_human_candidate(
            replace(self.context, **changes),
            stdout=self.read,
            commit_exists=lambda _sha: True,
            is_ancestor=lambda _a, _b: self.ancestor,
        )

    def test_exact_candidate_and_same_user_are_non_authorizing(self):
        result = self.verify()
        self.assertEqual(
            (result["candidate_sha"], result["master_sha"]), (CANDIDATE, MASTER)
        )
        self.assertEqual((self.user_reads, self.status_reads), (2, 2))
        self.assertEqual(self.commands.count(["git", "rev-parse", "HEAD"]), 2)
        self.assertEqual(self.commands.count(["git", "remote", "get-url", "origin"]), 2)
        self.assertEqual(len(result["github_user_sha256"]), 64)
        self.assertIs(result["execution_authorized"], False)
        self.assertIs(result["deployment_authorized"], False)
        self.assertNotIn("login", result)
        self.assertNotIn("id", result)
        self.assertEqual(self.commands[0][-3:], ["--method", "GET", "user"])

    def test_final_clean_controller_head_and_origin_changes_refuse(self):
        for command, changed, message in (
            (["git", "rev-parse", "HEAD"], "d" * 40, "HEAD changed"),
            (
                ["git", "remote", "get-url", "origin"],
                "https://foreign.invalid/repo.git",
                "origin changed",
            ),
        ):
            self.commands.clear()
            self.hook = (
                lambda current: changed
                if current == command and self.commands.count(command) == 2
                else None
            )
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(m.CandidateVerificationError, message),
            ):
                self.verify()
            self.assertEqual(self.commands.count(command), 2)

    def test_wrong_user_bot_bool_or_missing_id_refuses(self):
        for change in (
            {"login": "wrong"},
            {"type": "Bot"},
            {"id": True},
            {"id": 0},
            {"id": None},
        ):
            original = dict(self.user)
            self.user.update(change)
            with (
                self.subTest(change=change),
                self.assertRaises(m.CandidateVerificationError),
            ):
                self.verify()
            self.user = original

    def test_duplicate_or_nonobject_identity_refuses(self):
        for raw in (
            "[]",
            "null",
            '{"login":"wrong","login":"JohnClem","id":123,"type":"User"}',
        ):
            self.hook = lambda command, raw=raw: raw if command[-1] == "user" else None
            with self.subTest(raw=raw), self.assertRaises(m.CandidateVerificationError):
                self.verify()

    def test_github_environment_cannot_substitute_for_user(self):
        self.user["login"] = "wrong"
        with patch.dict(
            os.environ,
            {"GITHUB_ACTOR": m.FOUNDER_LOGIN, "GITHUB_EVENT_NAME": "workflow_dispatch"},
        ):
            with self.assertRaises(m.CandidateVerificationError):
                self.verify()

    def test_identity_change_at_end_refuses(self):
        def change(command):
            if command[-1] == "user" and self.user_reads:
                return json.dumps(self.user | {"id": 456})

        self.hook = change
        with self.assertRaisesRegex(m.CandidateVerificationError, "identity changed"):
            self.verify()

    def test_dirty_initial_or_final_checkout_refuses(self):
        for final in (False, True):
            self.status_reads = 0
            self.hook = (
                lambda command: "?? changed"
                if command[:2] == ["git", "status"] and (not final or self.status_reads)
                else None
            )
            with (
                self.subTest(final=final),
                self.assertRaises(m.CandidateVerificationError),
            ):
                self.verify()

    def test_foreign_origin_and_repository_refuse(self):
        with self.assertRaises(m.CandidateVerificationError):
            self.verify(repository="other/repo")
        self.assertEqual(self.commands, [])
        self.hook = (
            lambda command: "https://example.invalid/repo.git"
            if command[:2] == ["git", "remote"]
            else None
        )
        with self.assertRaisesRegex(m.CandidateVerificationError, "origin"):
            self.verify()

    def test_wrong_controller_candidate_or_schema_refuses(self):
        for change in (
            {"controller_sha": "d" * 40},
            {"candidate_sha": "master"},
            {"expected_schema_after": "e" * 12},
        ):
            with (
                self.subTest(change=change),
                self.assertRaises(m.CandidateVerificationError),
            ):
                self.verify(**change)

    def test_nonancestor_or_unmerged_candidate_refuses(self):
        self.ancestor = False
        with self.assertRaises(m.CandidateVerificationError):
            self.verify()
        self.ancestor = True
        self.pull["merged_at"] = None
        with self.assertRaises(m.CandidateVerificationError):
            self.verify()

    def test_no_default_transport_or_human_cli_bypass(self):
        with patch.object(m.subprocess, "run") as run:
            with self.assertRaises(TypeError):
                m.verify_human_candidate(self.context)
            run.assert_not_called()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys, "stderr", io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            m._parse_args(
                [
                    "--candidate-sha",
                    CANDIDATE,
                    "--approval-pr-number",
                    "385",
                    "--expected-schema-after",
                    SCHEMA,
                    "--workflow-sha",
                    MASTER,
                    "--repository",
                    m.REPOSITORY,
                    "--event-name",
                    "workflow_dispatch",
                    "--ref",
                    m.MASTER_REF,
                    "--ref-protected",
                    "true",
                    "--actor",
                    m.FOUNDER_LOGIN,
                    "--triggering-actor",
                    m.FOUNDER_LOGIN,
                    "--human-release",
                ]
            )


if __name__ == "__main__":
    unittest.main()
