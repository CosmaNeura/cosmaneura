"""Invented artifact chains, real private-file reads; no consumer or authority.

Every review/provider/provenance/source/identity/approval fact is fabricated for
these tests. Bootstrap attestation is explicitly mocked. No real Azure/GitHub,
credentials, Terminal, encrypted workspace or claim is read or executed.
"""

import atexit
import functools
import hashlib
import importlib.util
import locale
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

for name in (
    "staging_encrypted_workspace",
    "staging_snapshot_capture",
    "staging_snapshot_identity",
    "staging_snapshot_pg_io",
    "staging_snapshot_native_files",
    "staging_snapshot_runtime",
    "staging_snapshot_postgresql_runtime",
    "staging_snapshot_workspace",
    "staging_snapshot_configuration",
    "staging_snapshot_cli",
    "staging_snapshot_session",
    "staging_snapshot_azure",
    "staging_snapshot_console",
    "staging_snapshot_public_ip",
    "staging_snapshot_operations",
    "staging_snapshot_artifacts",
):
    if name in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[2] / "scripts" / (name + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
art = sys.modules["staging_snapshot_artifacts"]
pg = sys.modules["staging_snapshot_pg_io"]
native = sys.modules["staging_snapshot_native_files"]
FILES = None
TEXT = "# Verdict: **PASS**\n## Findings\n- **P0:** None.\n- **P1:** None.\n- **P2:** None.\n## Basis\nInvented fixture only."


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    return value.isoformat().replace("+00:00", "Z")


class SourceRuntimeBinding:
    files: Any
    sources: Any
    loader_sha256: str
    loader_source: Any

    def revalidate(self):
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0


def isolated_tests(cls):
    for name, method in list(vars(cls).items()):
        if not name.startswith("test_"):
            continue

        @functools.wraps(method)
        def run(self, _method=method, _name=name):
            if FILES is not None:
                return _method(self)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    __file__,
                    cls.__name__ + "." + _name,
                ],
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


@isolated_tests
class ArtifactTests(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(
                tempfile.TemporaryDirectory(prefix="snapshot-invented-artifacts-")
            )
        ).resolve()
        self.packet = self.root / ("human-snapshot-" + "a" * 32)
        self.packet.mkdir(mode=0o700)
        self.approvals = self.root / "approvals"
        self.approvals.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(art, "PACKET_PARENT", self.root))
        self.stack.enter_context(patch.object(art, "APPROVAL_ROOT", self.approvals))
        self.stack.enter_context(
            patch.object(
                art,
                "expected_identity",
                lambda: SimpleNamespace(configured_username_sha256="1" * 64),
            )
        )
        for attribute, name, raw in (
            (
                "MANUAL_APPROVAL_SHA",
                "STAGING-SUPERVISED-PORTAL-WINDOW-APPROVAL-20260915.json",
                b"invented manual permission",
            ),
            (
                "IP_APPROVAL_SHA",
                "STAGING-IPIFY-APPROVAL-20260914.json",
                b"invented ip permission",
            ),
        ):
            self.stack.enter_context(patch.object(art, attribute, digest(raw)))
            path = self.approvals / name
            path.write_bytes(raw)
            path.chmod(0o400)
        self.source = SourceRuntimeBinding()
        self.source.files = FILES
        self.source.loader_sha256 = digest(b"invented loader source")
        self.source.loader_source = SimpleNamespace(file_identity=(0, 0, os.geteuid()))
        self.source.sources = {
            name: SimpleNamespace(content=b"invented " + name.encode())
            for name in ("staging_encrypted_workspace", "staging_snapshot_artifacts")
        }
        self.files = {
            "staging_snapshot_bootstrap.py": self.source.loader_sha256,
            **{
                name + ".py": digest(value.content)
                for name, value in self.source.sources.items()
            },
        }
        self.bundle_sha = digest(art.canonical(self.files))
        self.now = datetime.now(UTC)
        self.plan_time = self.now - timedelta(minutes=10)
        self.handoff_time = self.now - timedelta(seconds=10)
        self.source_head = "c" * 40
        self.source_commit = "d" * 40
        self.plan_changes, self.handoff_changes = {}, {}
        self.identity_changes, self.provenance_changes = {}, {}
        self.review_changes, self.response_changes, self.request_changes = {}, {}, {}
        self.review_texts = {}
        self.cursor = False

    def write(self, name, value, *, raw=False):
        content = value if raw else art.canonical(value) + b"\n"
        path = self.packet / name
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(content)
        path.chmod(0o400)
        return digest(content)

    def review(self, kind, subject, completed):
        review_text = self.review_texts.get(kind, TEXT)
        request = {
            "model": "codex-auto-review",
            "metadata": {
                "review_kind": kind,
                "subject_sha256": subject,
                "source_bundle_sha256": self.bundle_sha,
            },
            "input": "Invented review request, not transmitted.",
        }
        response = {
            "id": "resp_" + {"source": "a", "plan": "b", "handoff": "c"}[kind] * 32,
            "status": "completed",
            "error": None,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": review_text}],
                }
            ],
        }
        provider = art.ENDPOINT
        response_id = response["id"]
        if kind == "source" and self.cursor:
            provider, response_id = "github-cursor-bugbot", "github-check-runs"
            request.update({"full_diff_review": True, "autofix_enabled": False})
            response = {
                "check_runs": [
                    {
                        "name": "Cursor Bugbot",
                        "app": {"id": 1210556},
                        "status": "completed",
                        "conclusion": "success",
                        "head_sha": self.source_head,
                    }
                ]
            }
        request.update(self.request_changes.get(kind, {}))
        response.update(self.response_changes.get(kind, {}))
        summary = {
            "schema_version": 1,
            "policy_id": art.POLICY,
            "kind": kind,
            "subject_sha256": subject,
            "source_bundle_sha256": self.bundle_sha,
            "provider": provider,
            "request_sha256": self.write(kind + "-review-request.json", request),
            "response_sha256": self.write(kind + "-review-response.json", response),
            "text_sha256": self.write(
                kind + "-review-text.md", review_text.encode(), raw=True
            ),
            "response_id": response_id,
            "completed_at": timestamp(completed),
            "verdict": "PASS",
            "findings": {"p0": [], "p1": [], "p2": []},
            "execution_authorized": False,
            "deployment_authorized": False,
        }
        summary.update(self.review_changes.get(kind, {}))
        return self.write(kind + "-review-summary.json", summary)

    def bundle(self):
        provenance = {
            "schema_version": 1,
            "repository": "CosmaNeura/cosmaneura",
            "protected_branch": "master",
            "source_commit": self.source_commit,
            "observed_at": timestamp(self.now - timedelta(seconds=20)),
            "required_checks": ["Backend", "Frontend", "change-budget"],
            "enforce_admins": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
            "files": self.files,
            "pull_requests": [
                {
                    "number": 999,
                    "head_commit": self.source_head,
                    "merge_commit": self.source_commit,
                    "state": "MERGED",
                    "reviewed_files": self.files,
                }
            ],
        }
        provenance.update(self.provenance_changes)
        provenance_sha = self.write("source-provenance.json", provenance)
        identity = {
            "configured_home_account_linked_to_exact_arm_token": True,
            "configured_username_sha256": "1" * 64,
            "current_token_oid_sha256": "2" * 64,
            "home_account_id_sha256": "3" * 64,
            "exact_arm_token_sha256": "4" * 64,
            "token_expires_epoch": int(self.now.timestamp()) + 3600,
            "observed_at_epoch": int(self.plan_time.timestamp()),
            "jwt_signature_independently_verified": False,
            "authenticated_same_token_read_required": False,
            "historical_apply_oid_attested": False,
            "human_apply_authorized": False,
            "not_a_plan_handoff_or_receipt": True,
            "authenticated_same_token_arm_read": True,
            "arm_read_response_epoch": int(self.plan_time.timestamp()),
            "arm_read_body_sha256": "5" * 64,
        }
        identity.update(self.identity_changes)
        identity_file_sha = self.write("identity.json", identity)
        identity_sha = digest(art.canonical(identity))
        plan = {
            "schema_version": 1,
            "policy_id": art.POLICY,
            "source_commit": self.source_commit,
            "source_bundle_sha256": self.bundle_sha,
            "created_at": timestamp(self.plan_time),
            "expires_at": timestamp(self.plan_time + timedelta(hours=4)),
            "identity_evidence_sha256": identity_sha,
            "workspace_root": "/private/tmp/cosmaneura-staging-snapshot-abcdefgh",
            "workspace_manifest_sha256": "6" * 64,
            "workspace_source_sha256": self.files["staging_encrypted_workspace.py"],
            "hdiutil_sha256": "7" * 64,
            "diskutil_sha256": "8" * 64,
            "application_binding_sha256": "9" * 64,
            "image": art.IMAGE,
            "source_revision": "89c31f0ae275",
            "rule": {
                "name": "cosmaneura-snapshot-0123456789abcdef",
                "start": "8.8.8.8",
                "end": "8.8.8.8",
            },
            "preexisting_rules": [
                {"name": "Existing", "start": "0.0.0.0", "end": "0.0.0.0"}
            ],
            "manual_window_approval_sha256": art.MANUAL_APPROVAL_SHA,
            "public_ip_approval_sha256": art.IP_APPROVAL_SHA,
            "access_seconds": 1800,
            "capture_seconds": 1200,
            "max_archive_bytes": 1024**3,
            "synthetic_clinical_data_confirmed": True,
            "credentials_and_workforce_data_remain_sensitive": True,
            "privileged_database_role_risk_disclosed": True,
            "concurrent_firewall_edit_risk_accepted": True,
            "firewall_management": "operator_supervised_portal",
            "native_firewall_expiry_proved": False,
            "execution_authorized": False,
            "deployment_authorized": False,
        }
        plan.update(self.plan_changes)
        plan_sha = self.write("plan.json", plan)
        source_review = self.review(
            "source", self.bundle_sha, self.plan_time - timedelta(seconds=1)
        )
        plan_review = self.review("plan", plan_sha, self.now - timedelta(seconds=30))
        handoff = {
            "schema_version": 1,
            "policy_id": art.POLICY,
            "source_commit": self.source_commit,
            "source_bundle_sha256": self.bundle_sha,
            "source_provenance_sha256": provenance_sha,
            "plan_sha256": plan_sha,
            "identity_file_sha256": identity_file_sha,
            "identity_evidence_sha256": identity_sha,
            "workspace_manifest_sha256": "6" * 64,
            "source_review_summary_sha256": source_review,
            "plan_review_summary_sha256": plan_review,
            "created_at": timestamp(self.handoff_time),
            "expires_at": timestamp(self.handoff_time + timedelta(minutes=45)),
            "human_presence_confirmed_at": timestamp(
                self.handoff_time - timedelta(seconds=1)
            ),
            "operator_uid": os.geteuid(),
            "execution_authorized": False,
            "deployment_authorized": False,
        }
        handoff.update(self.handoff_changes)
        self.handoff_sha = self.write("handoff.json", handoff)
        self.review_sha = self.review(
            "handoff", self.handoff_sha, self.now - timedelta(seconds=5)
        )

    def load(self):
        return art.ReviewedArtifacts(
            self.source, self.packet, self.handoff_sha, self.review_sha
        )

    def rejected(self):
        self.bundle()
        with pg.capture_deadline(time.monotonic() + 5), self.assertRaises(Exception):
            self.load()

    def test_complete_chain_retains_only_invented_public_files(self):
        self.bundle()
        before = sorted(
            str(path.relative_to(self.root)) for path in self.root.rglob("*")
        )
        with pg.capture_deadline(time.monotonic() + 5):
            result = self.load()
            result.revalidate()
            result.require_start()
        self.assertEqual(len(result._retained), 18)
        self.assertEqual(result.inputs.source_commit, self.source_commit)
        self.assertEqual(result.source_bundle_sha256, self.bundle_sha)
        self.assertEqual(set(result.baseline), {"existing"})
        self.assertFalse(result.plan["execution_authorized"])
        self.assertNotEqual(
            result.inputs.identity_evidence_sha256,
            digest((self.packet / "identity.json").read_bytes()),
        )
        self.assertEqual(
            before,
            sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")),
        )

    def test_missing_external_handoff_anchor_refused(self):
        self.bundle()
        self.handoff_sha = "0" * 64
        with (
            pg.capture_deadline(time.monotonic() + 5),
            self.assertRaises(art.SnapshotArtifactsRefused),
        ):
            self.load()

    def test_missing_external_review_anchor_refused(self):
        self.bundle()
        self.review_sha = "0" * 64
        with (
            pg.capture_deadline(time.monotonic() + 5),
            self.assertRaises(art.SnapshotArtifactsRefused),
        ):
            self.load()

    def test_duplicate_json_key_refused_even_with_matching_file_hash(self):
        self.bundle()
        raw = (self.packet / "handoff.json").read_bytes().rstrip()
        raw = raw[:-1] + b',"schema_version":1}'
        self.handoff_sha = self.write("handoff.json", raw, raw=True)
        with pg.capture_deadline(time.monotonic() + 5), self.assertRaises(Exception):
            self.load()

    def test_packet_outside_exact_namespace_refused(self):
        self.bundle()
        self.packet = self.root
        with (
            pg.capture_deadline(time.monotonic() + 5),
            self.assertRaises(art.SnapshotArtifactsRefused),
        ):
            self.load()

    def test_modified_retained_file_refused(self):
        self.bundle()
        with pg.capture_deadline(time.monotonic() + 5):
            result = self.load()
            self.write("plan.json", {"invented": "changed"})
            with self.assertRaises(art.SnapshotArtifactsRefused):
                result.revalidate()

    def test_identical_bytes_new_inode_refused(self):
        self.bundle()
        with pg.capture_deadline(time.monotonic() + 5):
            result = self.load()
            path = self.packet / "plan.json"
            replacement = self.packet / "replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o400)
            os.replace(replacement, path)
            with self.assertRaises(art.SnapshotArtifactsRefused):
                result.revalidate()

    def test_retention_checks_do_not_reopen_expired_start_gate(self):
        self.bundle()
        with pg.capture_deadline(time.monotonic() + 5):
            result = self.load()
            result.inputs = replace(
                result.inputs,
                handoff_created_at=self.now - timedelta(seconds=601),
                handoff_expires_at=self.now - timedelta(seconds=1),
            )
            result.revalidate()
            with self.assertRaises(Exception):
                result.require_start()

    def test_fresh_handoff_cannot_extend_expired_plan(self):
        self.plan_time = self.now - timedelta(hours=4, seconds=1)
        self.rejected()

    def test_extended_plan_lifetime_refused(self):
        self.plan_changes["expires_at"] = timestamp(self.plan_time + timedelta(hours=5))
        self.rejected()

    def test_extended_handoff_lifetime_refused(self):
        self.handoff_changes["expires_at"] = timestamp(
            self.handoff_time + timedelta(minutes=11)
        )
        self.rejected()

    def test_stale_human_presence_refused(self):
        self.handoff_changes["human_presence_confirmed_at"] = timestamp(
            self.handoff_time - timedelta(minutes=6)
        )
        self.rejected()

    def test_self_authorizing_handoff_refused(self):
        self.handoff_changes["execution_authorized"] = True
        self.rejected()

    def test_integer_cannot_replace_false_authority_flag(self):
        self.plan_changes["execution_authorized"] = 0
        self.rejected()

    def test_wrong_operator_refused(self):
        self.handoff_changes["operator_uid"] = os.geteuid() + 1
        self.rejected()

    def test_wrong_source_bytes_refused(self):
        self.provenance_changes["files"] = {
            **self.files,
            "staging_snapshot_bootstrap.py": "0" * 64,
        }
        self.rejected()

    def test_unmerged_source_refused(self):
        self.provenance_changes["pull_requests"] = [
            {
                "number": 999,
                "head_commit": self.source_head,
                "merge_commit": self.source_commit,
                "state": "OPEN",
                "reviewed_files": self.files,
            }
        ]
        self.rejected()

    def test_missing_reviewed_file_coverage_refused(self):
        self.provenance_changes["pull_requests"] = [
            {
                "number": 999,
                "head_commit": self.source_head,
                "merge_commit": self.source_commit,
                "state": "MERGED",
                "reviewed_files": {},
            }
        ]
        self.rejected()

    def test_weakened_protection_refused(self):
        self.provenance_changes["required_checks"] = ["Backend", "Frontend"]
        self.rejected()

    def test_stale_protected_source_readback_refused(self):
        self.provenance_changes["observed_at"] = timestamp(
            self.handoff_time - timedelta(minutes=31)
        )
        self.rejected()

    def test_identity_without_authenticated_same_token_read_refused(self):
        self.identity_changes["authenticated_same_token_arm_read"] = False
        self.rejected()

    def test_unexpected_configured_user_refused(self):
        self.identity_changes["configured_username_sha256"] = "9" * 64
        self.rejected()

    def test_inadequate_token_lifetime_refused(self):
        self.identity_changes["token_expires_epoch"] = int(self.now.timestamp()) + 1800
        self.rejected()

    def test_missing_risk_disclosure_refused(self):
        self.plan_changes["privileged_database_role_risk_disclosed"] = False
        self.rejected()

    def test_legacy_human_only_plan_cannot_authorize_delegated_portal_steps(self):
        self.plan_changes["firewall_management"] = "human_only"
        self.rejected()

    def test_legacy_approval_file_cannot_replace_supervised_portal_approval(self):
        current = (
            self.approvals / "STAGING-SUPERVISED-PORTAL-WINDOW-APPROVAL-20260915.json"
        )
        current.rename(
            self.approvals / "STAGING-HUMAN-FIREWALL-WINDOW-APPROVAL-20260914.json"
        )
        self.rejected()

    def test_rule_already_in_baseline_refused(self):
        self.plan_changes["preexisting_rules"] = [
            {
                "name": "cosmaneura-snapshot-0123456789abcdef",
                "start": "8.8.8.8",
                "end": "8.8.8.8",
            }
        ]
        self.rejected()

    def test_plan_review_completed_after_handoff_refused(self):
        self.review_changes["plan"] = {
            "completed_at": timestamp(self.now - timedelta(seconds=5))
        }
        self.rejected()

    def test_outstanding_finding_refused(self):
        self.review_changes["handoff"] = {
            "findings": {"p0": [], "p1": [], "p2": ["invented unresolved"]}
        }
        self.rejected()

    def test_response_must_match_exact_summary_identity(self):
        self.response_changes["handoff"] = {"id": "resp_" + "9" * 32}
        self.rejected()

    def test_response_text_must_equal_retained_text(self):
        self.response_changes["handoff"] = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "# Verdict: **FAIL**"}],
                }
            ]
        }
        self.rejected()

    def test_review_request_must_bind_actual_subject(self):
        self.request_changes["handoff"] = {
            "metadata": {
                "review_kind": "handoff",
                "subject_sha256": "0" * 64,
                "source_bundle_sha256": self.bundle_sha,
            }
        }
        self.rejected()

    def test_cursor_success_cannot_replace_exact_azure_source_review(self):
        self.cursor = True
        self.rejected()

    def test_pass_markers_inside_quoted_text_are_not_a_verdict(self):
        self.review_texts["handoff"] = "\n".join(
            "> " + line for line in TEXT.splitlines()
        )
        self.rejected()

    def test_duplicate_or_conflicting_verdict_refused(self):
        self.review_texts["handoff"] = TEXT + "\n# Verdict: **FAIL**"
        self.rejected()

    def test_later_findings_cannot_be_hidden_by_initial_none_markers(self):
        self.review_texts["plan"] = TEXT + "\n- **P1:** Invented blocking finding."
        self.rejected()

    def test_pass_inside_fenced_example_is_not_a_verdict(self):
        self.review_texts["handoff"] = "```markdown\n" + TEXT + "\n```"
        self.rejected()

    def test_incomplete_response_cannot_supply_a_successful_review(self):
        self.response_changes["handoff"] = {
            "incomplete_details": {"reason": "max_output_tokens"}
        }
        self.rejected()

    def test_incomplete_message_cannot_supply_a_successful_review(self):
        self.response_changes["handoff"] = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "incomplete",
                    "content": [{"type": "output_text", "text": TEXT}],
                }
            ]
        }
        self.rejected()

    def test_cursor_neutral_is_not_a_passing_source_review(self):
        self.cursor = True
        self.response_changes["source"] = {
            "check_runs": [
                {
                    "name": "Cursor Bugbot",
                    "app": {"id": 1210556},
                    "status": "completed",
                    "conclusion": "neutral",
                    "head_sha": self.source_head,
                }
            ]
        }
        self.rejected()

    def test_cursor_review_of_different_head_refused(self):
        self.cursor = True
        self.response_changes["source"] = {
            "check_runs": [
                {
                    "name": "Cursor Bugbot",
                    "app": {"id": 1210556},
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": "0" * 40,
                }
            ]
        }
        self.rejected()

    def test_cursor_incremental_claim_refused(self):
        self.cursor = True
        self.request_changes["source"] = {"full_diff_review": False}
        self.rejected()

    def test_cursor_cannot_substitute_for_exact_plan_review(self):
        self.review_changes["plan"] = {"provider": "github-cursor-bugbot"}
        self.rejected()


if __name__ == "__main__":
    assert callable(atexit.register)
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
