"""Wholly INVENTED future-dated public evidence; no live artifact or consumer.

One semantic integration calls the real reader's _load with memory reads and
runtime/approval I/O explicitly mocked; no real native binding or execution.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


review_module = load(
    "staging_snapshot_review_record", ROOT / "scripts/staging_snapshot_review_record.py"
)
source_module = load(
    "staging_snapshot_source_provenance",
    ROOT / "scripts/staging_snapshot_source_provenance.py",
)
module = load(
    "staging_snapshot_public_plan", ROOT / "scripts/staging_snapshot_public_plan.py"
)
NOW = datetime(2030, 1, 1, tzinfo=UTC)  # Invented, never an actual return/plan time.
TEXT = b"# Verdict: **PASS**\n- **P0:** None.\n- **P1:** None.\n- **P2:** None.\nInvented review only."
COMMIT = "a" * 40
RULE = "cosmaneura-snapshot-0123456789abcdef"


def encoded(value):
    return module.canonical(value)


def source(at=NOW):
    content = tuple(
        (name, ("invented " + name).encode())
        for name in sorted(source_module.SOURCE_NAMES)
    )
    files = {name: module.sha(raw) for name, raw in content}
    value = {
        "schema_version": 1,
        "repository": "CosmaNeura/cosmaneura",
        "protected_branch": "master",
        "source_commit": COMMIT,
        "observed_at": module.encoded_time(at),
        "required_checks": ["Backend", "Frontend", "change-budget"],
        "enforce_admins": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "files": files,
        "pull_requests": [
            {
                "number": 999,
                "head_commit": "b" * 40,
                "merge_commit": COMMIT,
                "state": "MERGED",
                "reviewed_files": files,
            }
        ],
    }
    evidence = (("invented-provider.json", b'{"invented":true}'),)
    return source_module.SourceCollection(
        encoded(value),
        evidence,
        encoded({name: module.sha(raw) for name, raw in evidence}),
        content,
    )


def review(kind, subject, bundle, at):
    metadata = {
        "review_kind": kind,
        "subject_sha256": subject,
        "source_bundle_sha256": bundle,
    }
    request = encoded(
        {
            "model": "codex-auto-review",
            "metadata": metadata,
            "stream": True,
            "background": False,
        }
    )
    identity = "resp_" + {"source": "a", "plan": "b", "handoff": "c"}[kind] * 16
    response = encoded(
        {
            "id": identity,
            "status": "completed",
            "metadata": metadata,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": TEXT.decode()}],
                }
            ],
        }
    )
    summary = encoded(
        {
            "schema_version": 1,
            "policy_id": module.POLICY,
            "kind": kind,
            "subject_sha256": subject,
            "source_bundle_sha256": bundle,
            "provider": module.ENDPOINT,
            "request_sha256": module.sha(request),
            "response_sha256": module.sha(response),
            "text_sha256": module.sha(TEXT),
            "response_id": identity,
            "completed_at": module.encoded_time(at),
            "verdict": "PASS",
            "findings": {"p0": [], "p1": [], "p2": []},
            "execution_authorized": False,
            "deployment_authorized": False,
        }
    )
    return review_module.ReviewRecord(
        request,
        response,
        TEXT,
        summary,
        encoded({name: "f" * 64 for name in review_module.LIMITS}),
    )


def workspace(files):
    root = "/private/tmp/cosmaneura-staging-snapshot-abcdefgh"
    return encoded(
        {
            "workspace": root,
            "image": root + "/staging.sparseimage",
            "mount": root + "/mounted",
            "device": "/dev/disk9s1",
            "image_inode": 100,
            "image_device": 1,
            "mount_device": 2,
            "mount_inode": 101,
            "capacity_bytes": 8 * 1024**3,
            "encrypted_image_bound_to_mounted_apfs": True,
            "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
            "capture_authorized": False,
            "live_action_authorized": False,
            "payload_device": 2,
            "payload_inode": 102,
            "noindex_marker_inode": 103,
            "empty_payload_verified": True,
            "noindex_marker_verified": True,
            "schema_version": 1,
            "empty_workspace_prepared": True,
            "source_sha256": files["staging_encrypted_workspace.py"],
            "creation_command_sha256": "4" * 64,
            "requested_encryption": "AES-256",
            "live_data_copied": False,
            "not_a_receipt_or_permanent_readiness_proof": True,
        }
    )


class PublicPlanTests(unittest.TestCase):
    def setUp(self):
        self.source = source()
        self.files = json.loads(self.source.provenance)["files"]
        self.bundle = module.sha(encoded(self.files))
        self.source_review = review(
            "source", self.bundle, self.bundle, NOW - timedelta(seconds=1)
        )
        self.workspace = workspace(self.files)
        self.observation = {
            "schema_version": 1,
            "source_commit": COMMIT,
            "source_bundle_sha256": self.bundle,
            "operator_uid": 501,
            "observed_at": module.encoded_time(NOW),
            "identity": {
                "configured_home_account_linked_to_exact_arm_token": True,
                "configured_username_sha256": module.USERNAME_SHA,
                "current_token_oid_sha256": "2" * 64,
                "home_account_id_sha256": "3" * 64,
                "exact_arm_token_sha256": "4" * 64,
                "token_expires_epoch": int(NOW.timestamp()) + 3600,
                "observed_at_epoch": int(NOW.timestamp()),
                "jwt_signature_independently_verified": False,
                "authenticated_same_token_read_required": False,
                "historical_apply_oid_attested": False,
                "human_apply_authorized": False,
                "not_a_plan_handoff_or_receipt": True,
                "authenticated_same_token_arm_read": True,
                "arm_read_response_epoch": int(NOW.timestamp()),
                "arm_read_body_sha256": "5" * 64,
            },
            "workspace_manifest_sha256": module.sha(self.workspace),
            "hdiutil_sha256": "6" * 64,
            "diskutil_sha256": "7" * 64,
            "application_binding_sha256": "8" * 64,
            "image": module.IMAGE,
            "public_ipv4": "8.8.8.8",
            "preexisting_rules": [
                {"name": "Existing", "start": "0.0.0.0", "end": "0.0.0.0"}
            ],
            "preparation_only": True,
            "database_credentials_read": False,
            "firewall_mutated": False,
        }

    def plan(self, **kwargs):
        raw = encoded(self.observation)
        arguments = {
            "expected_source_sha256": module.sha(self.source.provenance),
            "expected_source_review_sha256": module.sha(self.source_review.summary),
            "expected_observation_sha256": module.sha(raw),
            "rule_name": RULE,
        }
        arguments.update(kwargs)
        with patch.object(module, "now_utc", return_value=NOW):
            return module.create_plan(
                self.source, self.source_review, raw, self.workspace, **arguments
            )

    def handoff(
        self,
        plan=None,
        *,
        at=NOW + timedelta(seconds=30),
        presence=None,
        fresh=None,
        **kwargs,
    ):
        plan = plan or self.plan()
        pr = review(
            "plan", module.sha(plan.plan), self.bundle, NOW + timedelta(seconds=20)
        )
        fresh = fresh or source(at)
        arguments = {
            "expected_plan_sha256": module.sha(plan.plan),
            "expected_plan_review_sha256": module.sha(pr.summary),
            "expected_source_sha256": module.sha(fresh.provenance),
            "human_presence_confirmed_at": presence or at,
        }
        arguments.update(kwargs)
        with patch.object(module, "now_utc", return_value=at):
            return module.create_handoff(plan, pr, fresh, **arguments)

    def packet(self, handoff=None, *, at=NOW + timedelta(seconds=40), **kwargs):
        handoff = handoff or self.handoff()
        hr = review(
            "handoff",
            module.sha(handoff.handoff),
            self.bundle,
            NOW + timedelta(seconds=35),
        )
        arguments = {
            "expected_handoff_sha256": module.sha(handoff.handoff),
            "expected_handoff_review_sha256": module.sha(hr.summary),
        }
        arguments.update(kwargs)
        with patch.object(module, "now_utc", return_value=at):
            return module.packet_files(handoff, hr, **arguments)

    def test_complete_invented_chain_keeps_exact_lifetimes_and_no_authority(self):
        plan = self.plan()
        handoff = self.handoff(plan)
        files = dict(self.packet(handoff))
        p, h = json.loads(plan.plan), json.loads(handoff.handoff)
        self.assertEqual(
            module.timestamp(p["expires_at"]) - module.timestamp(p["created_at"]),
            timedelta(hours=4),
        )
        self.assertEqual(
            module.timestamp(h["expires_at"]) - module.timestamp(h["created_at"]),
            timedelta(minutes=45),
        )
        self.assertEqual(files["plan.json"], plan.plan)
        self.assertEqual(len(files), 16)
        for record in (p, h):
            self.assertIs(record["execution_authorized"], False)
            self.assertIs(record["deployment_authorized"], False)
        with self.assertRaises(FrozenInstanceError):
            plan.plan = b"changed"

    def test_external_anchors_are_mandatory(self):
        for key in (
            "expected_source_sha256",
            "expected_source_review_sha256",
            "expected_observation_sha256",
        ):
            with self.subTest(key=key), self.assertRaises(module.PublicPlanRefused):
                self.plan(**{key: "0" * 64})
        with self.assertRaises(module.PublicPlanRefused):
            self.handoff(expected_plan_sha256="0" * 64)
        with self.assertRaises(module.PublicPlanRefused):
            self.packet(expected_handoff_review_sha256="0" * 64)

    def test_identity_linkage_privilege_claims_and_remaining_life_refuse(self):
        original = copy.deepcopy(self.observation)
        for key, value in (
            ("configured_username_sha256", "0" * 64),
            ("authenticated_same_token_arm_read", False),
            ("human_apply_authorized", True),
            ("jwt_signature_independently_verified", True),
            ("historical_apply_oid_attested", True),
            ("token_expires_epoch", int(NOW.timestamp()) + module.MIN_TOKEN_SECONDS),
            ("observed_at_epoch", int(NOW.timestamp()) - 121),
        ):
            self.observation = copy.deepcopy(original)
            self.observation["identity"][key] = value
            with self.subTest(key=key), self.assertRaises(module.PublicPlanRefused):
                self.plan()

    def test_unknown_sensitive_fields_bool_coercion_and_mutation_claim_refuse(self):
        original = copy.deepcopy(self.observation)
        for key, value in (
            ("unexpected_secret", "invented-secret"),
            ("preparation_only", 1),
            ("database_credentials_read", True),
            ("firewall_mutated", True),
            ("operator_uid", True),
        ):
            self.observation = copy.deepcopy(original)
            self.observation[key] = value
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(
                    module.PublicPlanRefused, "^snapshot_public_plan_refused$"
                ),
            ):
                self.plan()

    def test_fixed_image_public_ipv4_rules_and_observation_age(self):
        original = copy.deepcopy(self.observation)
        for key, value in (
            ("image", "invented:latest"),
            ("public_ipv4", "127.0.0.1"),
            ("public_ipv4", "2001:4860:4860::8888"),
            ("public_ipv4", "invented secret"),
            ("observed_at", module.encoded_time(NOW - timedelta(seconds=121))),
            ("observed_at", module.encoded_time(NOW + timedelta(seconds=1))),
            (
                "preexisting_rules",
                [{"name": RULE, "start": "8.8.8.8", "end": "8.8.8.8"}],
            ),
        ):
            self.observation = copy.deepcopy(original)
            self.observation[key] = value
            with self.subTest(key=key), self.assertRaises(module.PublicPlanRefused):
                self.plan()

    def test_workspace_operation_records_preserve_legacy_and_completed_manifests(self):
        original = json.loads(self.workspace)
        for extra in (
            {},
            {"setup_operation": "prepare-empty", "image_created_in_this_process": True},
            {
                "setup_operation": "complete-empty",
                "image_created_in_this_process": False,
            },
        ):
            raw = encoded(original | extra)
            result = module.workspace_record(
                raw, module.sha(raw), self.files["staging_encrypted_workspace.py"], NOW
            )
            self.assertEqual(result, original | extra)
            self.assertFalse(result["capture_authorized"])

    def test_workspace_operation_records_reject_partial_contradictory_or_extra_fields(
        self,
    ):
        original = json.loads(self.workspace)
        for extra in (
            {"setup_operation": "complete-empty"},
            {"image_created_in_this_process": False},
            {
                "setup_operation": "complete-empty",
                "image_created_in_this_process": True,
            },
            {
                "setup_operation": "prepare-empty",
                "image_created_in_this_process": False,
            },
            {"setup_operation": "complete-empty", "image_created_in_this_process": 0},
            {"setup_operation": "retry", "image_created_in_this_process": False},
            {"approval": True},
        ):
            raw = encoded(original | extra)
            with self.subTest(extra=extra), self.assertRaises(module.PublicPlanRefused):
                module.workspace_record(
                    raw,
                    module.sha(raw),
                    self.files["staging_encrypted_workspace.py"],
                    NOW,
                )

    def test_workspace_exact_manifest_binding_and_paths(self):
        original = json.loads(self.workspace)
        for key, value in (
            ("requested_encryption", "none"),
            ("encrypted_image_bound_to_mounted_apfs", False),
            ("source_sha256", "0" * 64),
            ("workspace", "/private/tmp/unrelated"),
            ("payload_device", 99),
            ("live_data_copied", True),
            ("observed_at", (NOW + timedelta(seconds=1)).isoformat()),
        ):
            updated = dict(original, **{key: value})
            self.workspace = encoded(updated)
            self.observation["workspace_manifest_sha256"] = module.sha(self.workspace)
            with self.subTest(key=key), self.assertRaises(module.PublicPlanRefused):
                self.plan()

    def test_source_file_coverage_policy_and_provider_bytes_refuse(self):
        original = self.source
        for field, replacement in (
            ("source_files", original.source_files[:-1]),
            ("evidence_hashes", b"{}"),
            ("evidence", ()),
        ):
            self.source = replace(original, **{field: replacement})
            with self.subTest(field=field), self.assertRaises(module.PublicPlanRefused):
                self.plan()
        self.source = original
        value = json.loads(original.provenance)
        value["allow_force_pushes"] = True
        self.source = replace(original, provenance=encoded(value))
        with self.assertRaises(module.PublicPlanRefused):
            self.plan()

    def test_review_text_and_subject_and_timestamp_cannot_be_relabeled(self):
        original = self.source_review
        for key, value in (
            ("subject_sha256", "0" * 64),
            ("kind", "plan"),
            ("completed_at", module.encoded_time(NOW)),
            ("execution_authorized", True),
        ):
            summary = json.loads(original.summary)
            summary[key] = value
            self.source_review = replace(original, summary=encoded(summary))
            with self.subTest(key=key), self.assertRaises(module.PublicPlanRefused):
                self.plan()
        self.source_review = replace(original, text=TEXT.replace(b"PASS", b"FAIL"))
        with self.assertRaises(module.PublicPlanRefused):
            self.plan()

    def test_handoff_needs_actual_recent_presence_and_fresh_same_source(self):
        at = NOW + timedelta(seconds=30)
        for presence in (at - timedelta(seconds=301), at + timedelta(seconds=1)):
            with (
                self.subTest(presence=presence),
                self.assertRaises(module.PublicPlanRefused),
            ):
                self.handoff(at=at, presence=presence)
        with self.assertRaises(module.PublicPlanRefused):
            self.handoff(fresh=source(at - timedelta(seconds=1801)))
        fresh = source(at)
        value = json.loads(fresh.provenance)
        value["source_commit"] = "d" * 40
        with self.assertRaises(module.PublicPlanRefused):
            self.handoff(fresh=replace(fresh, provenance=encoded(value)))

    def test_plan_cannot_be_changed_or_have_its_lifetime_renewed(self):
        plan = self.plan()
        original_hash = module.sha(plan.plan)
        value = json.loads(plan.plan)
        value["expires_at"] = module.encoded_time(NOW + timedelta(hours=5))
        altered = replace(plan, plan=encoded(value))
        with self.assertRaises(module.PublicPlanRefused):
            self.handoff(altered, expected_plan_sha256=original_hash)
        with self.assertRaises(module.PublicPlanRefused):
            self.handoff(altered)
        with self.assertRaises(module.PublicPlanRefused):
            self.handoff(plan, at=NOW + timedelta(hours=3, minutes=30))

    def test_packet_checks_start_lifetime_and_does_not_extend_handoff(self):
        # Keep token expiry independent so this exercises the handoff boundary.
        self.observation["identity"]["token_expires_epoch"] = int(NOW.timestamp()) + 7200
        handoff = self.handoff()
        original = handoff.handoff
        self.packet(handoff, at=NOW + timedelta(seconds=2609))
        self.assertEqual(original, handoff.handoff)
        with self.assertRaises(module.PublicPlanRefused):
            self.packet(handoff, at=NOW + timedelta(seconds=2610))
        h = json.loads(original)
        h["expires_at"] = module.encoded_time(NOW + timedelta(hours=1))
        with self.assertRaises(module.PublicPlanRefused):
            self.packet(replace(handoff, handoff=encoded(h)))

    def test_token_headroom_cannot_be_extended_via_handoff(self):
        self.observation["identity"]["token_expires_epoch"] = (
            int(NOW.timestamp()) + module.MIN_TOKEN_SECONDS + 30
        )
        plan = self.plan()
        with self.assertRaises(module.PublicPlanRefused):
            self.handoff(plan)

    def test_source_evidence_metadata_bounds(self):
        original = self.source
        for replacement in (
            replace(original, evidence=original.evidence * 129),
            replace(original, evidence_hashes=b" " * 32769),
        ):
            self.source = replacement
            with self.assertRaises(module.PublicPlanRefused):
                self.plan()

    def test_packet_matches_existing_real_reader_semantics_with_io_mocked(self):
        # Load the existing fixture definitions ONLY; do not discover/run its tests.
        fixture = load(
            "public_plan_existing_reader_fixture",
            ROOT / "tests/infrastructure/test_staging_snapshot_artifacts.py",
        )
        art = fixture.art
        handoff = self.handoff()
        files = dict(self.packet(handoff))
        reader = art.ReviewedArtifacts.__new__(art.ReviewedArtifacts)
        reader._source = SimpleNamespace(
            loader_sha256=self.files["staging_snapshot_bootstrap.py"],
            loader_source=SimpleNamespace(file_identity=(0, 0, 501)),
            sources={
                name.removesuffix(".py"): SimpleNamespace(content=raw)
                for name, raw in self.source.source_files
                if name != "staging_snapshot_bootstrap.py"
            },
        )
        reader._start_utc = NOW + timedelta(seconds=40)
        reader.handoff_sha256 = module.sha(files["handoff.json"])
        reader.review_sha256 = module.sha(files["handoff-review-summary.json"])

        def read(name, expected):
            self.assertEqual(module.sha(files[name]), expected)
            return files[name]

        reader._read = read
        reader._approval = lambda *_: None  # Explicitly invented approval/runtime I/O.
        reader._load()
        reader.inputs.validate(NOW + timedelta(seconds=40))
        self.assertEqual(reader.plan["source_commit"], COMMIT)
        self.assertEqual(set(files), set(art.CATALOG))
        self.assertEqual(reader.source_bundle_sha256, self.bundle)


if __name__ == "__main__":
    unittest.main()
