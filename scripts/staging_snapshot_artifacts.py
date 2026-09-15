"""Retain exact reviewed public snapshot artifacts; no preparation or execution.

The human launch command MUST obtain its two expected hashes from the actual
independent exact handoff review. A locally self-authored hash, review summary,
or provenance document is not independent authority. This reader establishes
consistency and detects replacement; it neither contacts/authenticates GitHub
nor performs a review. The reviewed producer owes the actual provider evidence.
No receipt is generated, refreshed, claimed, consumed or deleted here.
"""

from __future__ import annotations

import hashlib
import re
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from staging_snapshot_capture import CaptureInputs, Rule, rules_by_name, valid_sha
from staging_snapshot_identity import read_object
from staging_snapshot_native_files import FileObservation, NativeFiles
from staging_snapshot_operations import IDENTITY_KEYS, IMAGE, canonical
from staging_snapshot_pg_io import require_capture_process
from staging_snapshot_session import expected_identity

if TYPE_CHECKING:
    from staging_snapshot_bootstrap import SourceRuntimeBinding

POLICY = "cosmaneura-staging-human-snapshot-v1"
ENDPOINT = "https://cosmascode.openai.azure.com/openai/v1/responses"
PACKET_PARENT = Path("/private/tmp/cosmaneura-staging-baseline-20260914.adNnmt")
MANUAL_APPROVAL_SHA = "989fd8d45c5cfebf71c16dc89c2604133fea038f8f5daa72a6fc616fd81cdea0"
IP_APPROVAL_SHA = "2a8bdf2eb217c3ad9fee21bfdb3dd00b34efee160de6d77f82e6f43f23b023aa"
APPROVAL_ROOT = Path(
    "/Users/jw/Desktop/Cosmas/staging-private-foundation-command-checkpoint-20260911"
)
CATALOG = {
    "handoff.json": 32768,
    "plan.json": 65536,
    "identity.json": 16384,
    "source-provenance.json": 256 * 1024,
    **{
        f"{kind}-review-{part}.{extension}": limit
        for kind in ("source", "plan", "handoff")
        for part, extension, limit in (
            ("summary", "json", 16384),
            ("request", "json", 500000),
            ("response", "json", 2 * 1024**2),
            ("text", "md", 128 * 1024),
        )
    },
}


class SnapshotArtifactsRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SnapshotArtifactsRefused("snapshot_artifacts_refused")


def sha(value: object) -> str:
    require(type(value) is str and valid_sha(value))
    return str(value)


def commit(value: object) -> str:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", str(value)))
    return str(value)


def record(value: object, fields: str) -> dict[str, Any]:
    require(type(value) is dict and set(value) == set(fields.split()))
    return value  # type: ignore[return-value]


def utc(value: object) -> datetime:
    require(type(value) is str and str(value).endswith("Z"))
    parsed = datetime.fromisoformat(str(value))
    require(parsed.tzinfo is UTC)
    require(parsed.isoformat().replace("+00:00", "Z") == value)
    return parsed


def exact(value: dict[str, Any], expected: dict[str, Any]) -> None:
    # Canonical bytes also distinguish JSON true from 1, including nested facts.
    require(all(key in value for key in expected))
    require(canonical({key: value[key] for key in expected}) == canonical(expected))


class ReviewedArtifacts:
    """Caller owns one active absolute timer. Retained checks never renew it."""

    catalog = CATALOG

    def __init__(
        self,
        source: SourceRuntimeBinding,
        root: Path,
        expected_handoff_sha256: str,
        expected_review_summary_sha256: str,
    ):
        self._source, self._root = source, root
        self._retained: dict[Path, FileObservation] = {}
        self._start_utc, self._start_monotonic = datetime.now(UTC), time.monotonic()
        self._types()
        require(
            root.parent == PACKET_PARENT
            and re.fullmatch(r"human-snapshot-[0-9a-f]{32}", root.name)
        )
        self.handoff_sha256 = sha(expected_handoff_sha256)
        self.review_sha256 = sha(expected_review_summary_sha256)
        self._load()
        self.revalidate()
        self.require_start()

    def _types(self) -> None:
        require_capture_process()
        require(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
        require(
            type(self._source)
            is getattr(sys.modules["__main__"], "SourceRuntimeBinding", None)
        )
        require(type(self._source.files) is NativeFiles)

    def _read(self, name: str, expected: str) -> bytes:
        require(name in self.catalog)
        observed = self._source.files.read(
            self._root / name, mode=0o400, maximum=self.catalog[name]
        )
        require(hashlib.sha256(observed.content).hexdigest() == sha(expected))
        previous = self._retained.setdefault(self._root / name, observed)
        require(observed == previous)
        return observed.content

    def _json(self, name: str, expected: str) -> dict[str, Any]:
        return read_object(self._read(name, expected), self.catalog[name])

    def _source_files(self) -> dict[str, str]:
        return {
            "staging_snapshot_bootstrap.py": self._source.loader_sha256,
            **{
                name + ".py": hashlib.sha256(observation.content).hexdigest()
                for name, observation in self._source.sources.items()
            },
        }

    def _load(self) -> None:
        h = record(
            self._json("handoff.json", self.handoff_sha256),
            "schema_version policy_id source_commit source_bundle_sha256 "
            "source_provenance_sha256 plan_sha256 identity_file_sha256 "
            "identity_evidence_sha256 workspace_manifest_sha256 "
            "source_review_summary_sha256 plan_review_summary_sha256 "
            "created_at expires_at human_presence_confirmed_at operator_uid "
            "execution_authorized deployment_authorized",
        )
        exact(
            h,
            {
                "schema_version": 1,
                "policy_id": POLICY,
                "operator_uid": self._source.loader_source.file_identity[2],
                "execution_authorized": False,
                "deployment_authorized": False,
            },
        )
        self.handoff_created, self.handoff_expires = (
            utc(h["created_at"]),
            utc(h["expires_at"]),
        )
        require(self.handoff_expires == self.handoff_created + timedelta(minutes=45))
        presence = utc(h["human_presence_confirmed_at"])
        require(
            self.handoff_created - timedelta(minutes=5)
            <= presence
            <= self.handoff_created
        )
        self.source_commit = commit(h["source_commit"])
        files = self._source_files()
        self.source_bundle_sha256 = hashlib.sha256(canonical(files)).hexdigest()
        require(h["source_bundle_sha256"] == self.source_bundle_sha256)
        self.provenance = self._provenance(
            h["source_provenance_sha256"], files, self.handoff_created
        )
        p = record(
            self._json("plan.json", h["plan_sha256"]),
            "schema_version policy_id source_commit source_bundle_sha256 "
            "created_at expires_at identity_evidence_sha256 workspace_root "
            "workspace_manifest_sha256 workspace_source_sha256 hdiutil_sha256 "
            "diskutil_sha256 application_binding_sha256 image source_revision "
            "rule preexisting_rules manual_window_approval_sha256 "
            "public_ip_approval_sha256 access_seconds capture_seconds "
            "max_archive_bytes synthetic_clinical_data_confirmed "
            "credentials_and_workforce_data_remain_sensitive "
            "privileged_database_role_risk_disclosed "
            "concurrent_firewall_edit_risk_accepted firewall_management "
            "native_firewall_expiry_proved execution_authorized deployment_authorized",
        )
        exact(
            p,
            {
                "schema_version": 1,
                "policy_id": POLICY,
                "source_commit": self.source_commit,
                "source_bundle_sha256": self.source_bundle_sha256,
                "identity_evidence_sha256": sha(h["identity_evidence_sha256"]),
                "workspace_manifest_sha256": sha(h["workspace_manifest_sha256"]),
                "workspace_source_sha256": files["staging_encrypted_workspace.py"],
                "image": IMAGE,
                "source_revision": "89c31f0ae275",
                "manual_window_approval_sha256": MANUAL_APPROVAL_SHA,
                "public_ip_approval_sha256": IP_APPROVAL_SHA,
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
            },
        )
        self.plan_created, self.plan_expires = (
            utc(p["created_at"]),
            utc(p["expires_at"]),
        )
        require(self.plan_expires == self.plan_created + timedelta(hours=4))
        require(self.plan_created <= self.handoff_created)
        require(type(p["workspace_root"]) is str)
        self.workspace_root = Path(p["workspace_root"])
        require(
            self.workspace_root.parent == Path("/private/tmp")
            and re.fullmatch(
                r"cosmaneura-staging-snapshot-[a-z0-9_]{8}", self.workspace_root.name
            )
        )
        for key in ("hdiutil_sha256", "diskutil_sha256", "application_binding_sha256"):
            sha(p[key])
        rule = record(p["rule"], "name start end")
        require(all(type(value) is str for value in rule.values()))
        self.inputs = CaptureInputs(
            Rule(**rule),
            self.source_commit,
            sha(h["plan_sha256"]),
            sha(h["identity_evidence_sha256"]),
            sha(h["workspace_manifest_sha256"]),
            MANUAL_APPROVAL_SHA,
            self.plan_created,
            self.plan_expires,
            self.handoff_created,
            self.handoff_expires,
        )
        require(
            type(p["preexisting_rules"]) is list and len(p["preexisting_rules"]) <= 100
        )
        self.baseline = rules_by_name(
            tuple(
                Rule(**record(item, "name start end"))
                for item in p["preexisting_rules"]
            )
        )
        require(self.inputs.rule.name.casefold() not in self.baseline)
        self.identity = self._identity(h)
        self.plan = p
        self._approval(
            "STAGING-SUPERVISED-PORTAL-WINDOW-APPROVAL-20260915.json",
            MANUAL_APPROVAL_SHA,
        )
        self._approval("STAGING-IPIFY-APPROVAL-20260914.json", IP_APPROVAL_SHA)
        self._review(
            "source",
            h["source_review_summary_sha256"],
            self.source_bundle_sha256,
            datetime(2026, 9, 14, tzinfo=UTC),
            self.plan_created,
        )
        self._review(
            "plan",
            h["plan_review_summary_sha256"],
            self.inputs.plan_sha256,
            self.plan_created,
            self.handoff_created,
        )
        self._review(
            "handoff",
            self.review_sha256,
            self.handoff_sha256,
            self.handoff_created,
            self.handoff_expires,
        )

    def _provenance(
        self, expected: str, files: dict[str, str], at: datetime
    ) -> dict[str, Any]:
        value = record(
            self._json("source-provenance.json", expected),
            "schema_version repository protected_branch source_commit observed_at "
            "required_checks enforce_admins allow_force_pushes allow_deletions "
            "files pull_requests",
        )
        exact(
            value,
            {
                "schema_version": 1,
                "repository": "CosmaNeura/cosmaneura",
                "protected_branch": "master",
                "source_commit": self.source_commit,
                "required_checks": ["Backend", "Frontend", "change-budget"],
                "enforce_admins": True,
                "allow_force_pushes": False,
                "allow_deletions": False,
                "files": files,
            },
        )
        observed = utc(value["observed_at"])
        require(at - timedelta(minutes=30) <= observed <= at)
        require(
            type(value["pull_requests"]) is list
            and 1 <= len(value["pull_requests"]) <= 30
        )
        covered, numbers = {}, set()
        for item in value["pull_requests"]:
            pr = record(item, "number head_commit merge_commit state reviewed_files")
            require(
                type(pr["number"]) is int
                and pr["number"] > 0
                and pr["number"] not in numbers
            )
            numbers.add(pr["number"])
            commit(pr["head_commit"])
            commit(pr["merge_commit"])
            require(pr["state"] == "MERGED" and type(pr["reviewed_files"]) is dict)
            for name, digest in pr["reviewed_files"].items():
                require(
                    name in files and name not in covered and sha(digest) == files[name]
                )
                covered[name] = digest
        require(covered == files)
        return value

    def _identity(self, handoff: dict[str, Any]) -> dict[str, Any]:
        value = record(
            self._json("identity.json", handoff["identity_file_sha256"]),
            "configured_home_account_linked_to_exact_arm_token configured_username_sha256 "
            "current_token_oid_sha256 home_account_id_sha256 exact_arm_token_sha256 "
            "token_expires_epoch observed_at_epoch jwt_signature_independently_verified "
            "authenticated_same_token_read_required historical_apply_oid_attested "
            "human_apply_authorized not_a_plan_handoff_or_receipt authenticated_same_token_arm_read "
            "arm_read_response_epoch arm_read_body_sha256",
        )
        require(
            hashlib.sha256(canonical(value)).hexdigest()
            == self.inputs.identity_evidence_sha256
        )
        for key in (*IDENTITY_KEYS, "arm_read_body_sha256"):
            sha(value[key])
        exact(
            value,
            {
                "configured_username_sha256": expected_identity().configured_username_sha256,
                "configured_home_account_linked_to_exact_arm_token": True,
                "jwt_signature_independently_verified": False,
                "authenticated_same_token_read_required": False,
                "historical_apply_oid_attested": False,
                "human_apply_authorized": False,
                "not_a_plan_handoff_or_receipt": True,
                "authenticated_same_token_arm_read": True,
            },
        )
        for key in (
            "token_expires_epoch",
            "observed_at_epoch",
            "arm_read_response_epoch",
        ):
            require(type(value[key]) is int and value[key] > 0)
        require(
            abs(value["observed_at_epoch"] - value["arm_read_response_epoch"]) <= 120
        )
        observed = datetime.fromtimestamp(value["observed_at_epoch"], UTC)
        require(
            self.plan_created - timedelta(minutes=2) <= observed <= self.handoff_created
        )
        return value

    def _approval(self, name: str, expected: str) -> None:
        path = APPROVAL_ROOT / name
        observed = self._source.files.read(path, mode=0o400, maximum=16384)
        require(hashlib.sha256(observed.content).hexdigest() == expected)
        self._retained[path] = observed

    def _review(
        self,
        kind: str,
        expected: str,
        subject: str,
        not_before: datetime,
        not_after: datetime,
    ) -> None:
        summary = record(
            self._json(kind + "-review-summary.json", expected),
            "schema_version policy_id kind subject_sha256 source_bundle_sha256 "
            "provider request_sha256 response_sha256 text_sha256 response_id "
            "completed_at verdict findings execution_authorized deployment_authorized",
        )
        exact(
            summary,
            {
                "schema_version": 1,
                "policy_id": POLICY,
                "kind": kind,
                "subject_sha256": sha(subject),
                "source_bundle_sha256": self.source_bundle_sha256,
                "verdict": "PASS",
                "findings": {"p0": [], "p1": [], "p2": []},
                "execution_authorized": False,
                "deployment_authorized": False,
            },
        )
        completed = utc(summary["completed_at"])
        require(not_before <= completed <= self._start_utc and completed < not_after)
        request = self._json(kind + "-review-request.json", summary["request_sha256"])
        response = self._json(
            kind + "-review-response.json", summary["response_sha256"]
        )
        text = self._read(kind + "-review-text.md", summary["text_sha256"])
        require(type(request.get("metadata")) is dict)
        exact(
            request["metadata"],
            {
                "review_kind": kind,
                "subject_sha256": subject,
                "source_bundle_sha256": self.source_bundle_sha256,
            },
        )
        require(summary["provider"] == ENDPOINT)
        require(request.get("model") == "codex-auto-review")
        require(
            type(summary["response_id"]) is str
            and re.fullmatch(r"resp_[0-9a-f]+", summary["response_id"])
        )
        exact(response, {"id": summary["response_id"], "status": "completed"})
        require(response.get("error") is None)
        require(response.get("incomplete_details") is None)
        require(type(response.get("output")) is list)
        pieces = []
        for item in response["output"]:
            require(type(item) is dict)
            require(item.get("type") in {"message", "reasoning"})
            if item.get("type") == "message":
                require(
                    item.get("role") == "assistant"
                    and item.get("status") == "completed"
                    and type(item.get("content")) is list
                )
                for part in item["content"]:
                    require(
                        type(part) is dict
                        and part.get("type") == "output_text"
                        and type(part.get("text")) is str
                    )
                    pieces.append(part["text"])
        require(pieces and "\n".join(pieces).encode() == text)
        # Exact single decision lines: a PASS substring inside a quotation or
        # alongside a second FAIL/findings section must not clear this gate.
        lines = text.decode("utf-8").splitlines()
        require(lines and lines[0] == "# Verdict: **PASS**")
        decision_lines = [
            line
            for line in lines
            if line.startswith("# Verdict:") or re.match(r"^- \*\*P[012]:", line)
        ]
        require(
            decision_lines
            == [
                "# Verdict: **PASS**",
                "- **P0:** None.",
                "- **P1:** None.",
                "- **P2:** None.",
            ]
        )

    def revalidate(self) -> None:
        """Unchanged retained bytes/metadata only; no lifetime renewal or claim."""
        self._types()
        self._source.revalidate()
        for path, previous in self._retained.items():
            maximum = self.catalog[path.name] if path.parent == self._root else 16384
            require(
                self._source.files.read(path, mode=0o400, maximum=maximum) == previous
            )
        elapsed = time.monotonic() - self._start_monotonic
        wall = (datetime.now(UTC) - self._start_utc).total_seconds()
        require(elapsed >= 0 and abs(wall - elapsed) <= 5)

    def require_start(self) -> None:
        self._types()
        self.inputs.validate(datetime.now(UTC))
        require(self.identity["token_expires_epoch"] > int(time.time()) + 1830)


class PreparationArtifacts(ReviewedArtifacts):
    """Source-only READ-ONLY preparation. Never fabricate a capture plan/handoff.

    The externally reviewed human command pins preparation and source-review
    hashes. The original independent source review and normally protected merge
    evidence remain mandatory. No CaptureInputs or capture authority is exposed.
    """

    catalog = {
        "preparation.json": 16384,
        **{name: size for name, size in CATALOG.items() if name.startswith("source-")},
    }

    def __init__(
        self,
        source: SourceRuntimeBinding,
        root: Path,
        expected_preparation_sha256: str,
        expected_source_review_sha256: str,
    ):
        self._source, self._root = source, root
        self._retained = {}
        self._start_utc, self._start_monotonic = datetime.now(UTC), time.monotonic()
        self._types()
        require(
            root.parent == PACKET_PARENT
            and re.fullmatch(r"human-preparation-[0-9a-f]{32}", root.name)
        )
        self.preparation_sha256 = sha(expected_preparation_sha256)
        self.review_sha256 = sha(expected_source_review_sha256)
        p = record(
            self._json("preparation.json", self.preparation_sha256),
            "schema_version policy_id source_commit source_bundle_sha256 "
            "source_provenance_sha256 source_review_summary_sha256 created_at expires_at "
            "human_presence_confirmed_at operator_uid workspace_root workspace_manifest_sha256 "
            "workspace_source_sha256 hdiutil_sha256 diskutil_sha256 observation_root "
            "public_ip_approval_sha256 preparation_only database_credentials_read "
            "firewall_mutated execution_authorized deployment_authorized",
        )
        files = self._source_files()
        self.source_commit = commit(p["source_commit"])
        self.source_bundle_sha256 = hashlib.sha256(canonical(files)).hexdigest()
        exact(
            p,
            {
                "schema_version": 1,
                "policy_id": POLICY,
                "source_bundle_sha256": self.source_bundle_sha256,
                "source_review_summary_sha256": self.review_sha256,
                "operator_uid": source.loader_source.file_identity[2],
                "workspace_source_sha256": files["staging_encrypted_workspace.py"],
                "public_ip_approval_sha256": IP_APPROVAL_SHA,
                "preparation_only": True,
                "database_credentials_read": False,
                "firewall_mutated": False,
                "execution_authorized": False,
                "deployment_authorized": False,
            },
        )
        self.created, self.expires = utc(p["created_at"]), utc(p["expires_at"])
        require(self.expires == self.created + timedelta(minutes=30))
        require(
            self.created - timedelta(minutes=5)
            <= utc(p["human_presence_confirmed_at"])
            <= self.created
        )
        self.provenance = self._provenance(
            p["source_provenance_sha256"], files, self.created
        )
        self._review(
            "source",
            self.review_sha256,
            self.source_bundle_sha256,
            datetime(2026, 9, 14, tzinfo=UTC),
            self.created,
        )
        for field in ("workspace_manifest_sha256", "hdiutil_sha256", "diskutil_sha256"):
            sha(p[field])
        require(type(p["workspace_root"]) is str and type(p["observation_root"]) is str)
        self.workspace_root, self.observation_root = (
            Path(p["workspace_root"]),
            Path(p["observation_root"]),
        )
        require(
            self.workspace_root.parent == Path("/private/tmp")
            and re.fullmatch(
                r"cosmaneura-staging-snapshot-[a-z0-9_]{8}", self.workspace_root.name
            )
            and self.observation_root.parent == PACKET_PARENT
            and re.fullmatch(
                r"human-observation-[0-9a-f]{32}", self.observation_root.name
            )
        )
        self.preparation = p
        self._approval("STAGING-IPIFY-APPROVAL-20260914.json", IP_APPROVAL_SHA)
        self.revalidate()
        self.require_start()

    def require_start(self) -> None:
        self._types()
        now = datetime.now(UTC)
        require(self.created <= now < self.expires)
        require(
            now - timedelta(minutes=30) <= utc(self.provenance["observed_at"]) <= now
        )


if __name__ == "__main__":
    raise SystemExit("snapshot_artifacts_requires_reviewed_human_launcher")
