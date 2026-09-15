"""Public snapshot plan/handoff assembly from independently anchored evidence.

No network, files, human consumer, credential, permission, claim or receipt I/O.
Only invented fixtures may call this while the operator is absent. Real callers
must FIRST authenticate the source review, protected-source collection and exact
reviewed human preparation output. Hash consistency is NOT their authentication.
Every returned plan/handoff still needs its own independent exact review.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import PurePosixPath
from typing import Any, ParamSpec, TypeVar, cast

from staging_snapshot_review_record import (
    ENDPOINT,
    POLICY,
    ReviewRecord,
    canonical,
    record,
    review_text,
    sha,
    valid_sha,
)
from staging_snapshot_source_provenance import SOURCE_NAMES, SourceCollection

IMAGE = "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:475908dbb4c6820f91ca6ccd626312ef4f7591c8555923f02949520fcd4208d7"
MANUAL_APPROVAL = "80c0a64759b887b172d3ab7187836dcc251c265c48a614f554728265451735e7"
IP_APPROVAL = "2a8bdf2eb217c3ad9fee21bfdb3dd00b34efee160de6d77f82e6f43f23b023aa"
USERNAME_SHA = "acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a"
# Retain the session's35min floor plus the loader90s and human preparation240s.
# Reviews and human delay consume additional time; never promise token renewal.
MIN_TOKEN_SECONDS = 2100 + 90 + 240
PACKET_PARENT = PurePosixPath(
    "/private/tmp/cosmaneura-staging-baseline-20260914.adNnmt"
)


class PublicPlanRefused(RuntimeError):
    pass


P = ParamSpec("P")
T = TypeVar("T")


def content_free(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except BaseException:
            raise PublicPlanRefused("snapshot_public_plan_refused") from None

    return wrapped


def require(condition: object) -> None:
    if not condition:
        raise PublicPlanRefused("snapshot_public_plan_refused")


def now_utc() -> datetime:
    return datetime.now(UTC)


def encoded_time(value: datetime) -> str:
    require(type(value) is datetime and value.tzinfo is UTC)
    return value.isoformat().replace("+00:00", "Z")


def timestamp(value: object) -> datetime:
    require(type(value) is str and len(value) <= 40)
    parsed = datetime.fromisoformat(cast(str, value))
    require(encoded_time(parsed) == value)
    return parsed


def obj(raw: bytes, fields: str, maximum: int = 32768) -> dict[str, Any]:
    require(type(raw) is bytes and 0 < len(raw) <= maximum)
    value = record(raw)
    require(set(value) == set(fields.split()))
    return value


def exact(value: dict[str, Any], expected: dict[str, Any]) -> None:
    require(set(expected) <= set(value))
    require(canonical({key: value[key] for key in expected}) == canonical(expected))


def commit(value: object) -> str:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value))
    return cast(str, value)


def source_record(
    source: SourceCollection, expected: str, at: datetime
) -> dict[str, Any]:
    require(type(source) is SourceCollection and valid_sha(expected))
    require(sha(source.provenance) == expected)
    value = obj(
        source.provenance,
        "schema_version repository protected_branch source_commit observed_at "
        "required_checks enforce_admins allow_force_pushes allow_deletions files pull_requests",
        256 * 1024,
    )
    exact(
        value,
        {
            "schema_version": 1,
            "repository": "CosmaNeura/cosmaneura",
            "protected_branch": "master",
            "required_checks": ["Backend", "Frontend", "change-budget"],
            "enforce_admins": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
        },
    )
    commit(value["source_commit"])
    observed = timestamp(value["observed_at"])
    require(at - timedelta(minutes=10) <= observed <= at)
    require(
        type(source.source_files) is tuple
        and len(source.source_files) == len(SOURCE_NAMES)
    )
    files = {}
    for name, raw in source.source_files:
        require(name in SOURCE_NAMES and name not in files and type(raw) is bytes)
        require(0 < len(raw) <= 128 * 1024)
        files[name] = sha(raw)
    require(set(files) == SOURCE_NAMES and value["files"] == files)
    require(
        type(value["pull_requests"]) is list and 1 <= len(value["pull_requests"]) <= 30
    )
    covered, numbers = {}, set()
    for item in value["pull_requests"]:
        require(
            type(item) is dict
            and set(item)
            == {"number", "head_commit", "merge_commit", "state", "reviewed_files"}
        )
        item = cast(dict[str, Any], item)
        require(
            type(item["number"]) is int
            and item["number"] > 0
            and item["number"] not in numbers
        )
        numbers.add(item["number"])
        commit(item["head_commit"])
        commit(item["merge_commit"])
        require(item["state"] == "MERGED" and type(item["reviewed_files"]) is dict)
        for name, digest in item["reviewed_files"].items():
            require(name in files and name not in covered and digest == files[name])
            covered[name] = digest
    require(covered == files)
    require(type(source.evidence) is tuple and 0 < len(source.evidence) <= 128)
    require(
        type(source.evidence_hashes) is bytes
        and 0 < len(source.evidence_hashes) <= 32768
    )
    hashes = {}
    for name, raw in source.evidence:
        require(type(name) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name))
        require(name not in hashes and type(raw) is bytes and len(raw) <= 8 * 1024**2)
        hashes[name] = sha(raw)
    require(record(source.evidence_hashes) == hashes)
    return value


def checked_review(
    value: ReviewRecord,
    expected: str,
    kind: str,
    subject: str,
    bundle: str,
    before: datetime,
    after: datetime,
) -> None:
    require(
        type(value) is ReviewRecord
        and valid_sha(expected)
        and sha(value.summary) == expected
    )
    summary = obj(
        value.summary,
        "schema_version policy_id kind subject_sha256 source_bundle_sha256 provider "
        "request_sha256 response_sha256 text_sha256 response_id completed_at verdict "
        "findings execution_authorized deployment_authorized",
        16384,
    )
    exact(
        summary,
        {
            "schema_version": 1,
            "policy_id": POLICY,
            "kind": kind,
            "subject_sha256": subject,
            "source_bundle_sha256": bundle,
            "provider": ENDPOINT,
            "verdict": "PASS",
            "findings": {"p0": [], "p1": [], "p2": []},
            "execution_authorized": False,
            "deployment_authorized": False,
        },
    )
    require(before <= timestamp(summary["completed_at"]) < after)
    for name, limit in (
        ("request", 500000),
        ("response", 2 * 1024**2),
        ("text", 128 * 1024),
    ):
        raw = getattr(value, name)
        require(
            type(raw) is bytes
            and 0 < len(raw) <= limit
            and sha(raw) == summary[name + "_sha256"]
        )
    request, response = record(value.request), record(value.response)
    require(
        request.get("model") == "codex-auto-review"
        and type(request.get("metadata")) is dict
    )
    exact(
        request["metadata"],
        {
            "review_kind": kind,
            "subject_sha256": subject,
            "source_bundle_sha256": bundle,
        },
    )
    require(canonical(response.get("metadata")) == canonical(request["metadata"]))
    require(
        type(summary["response_id"]) is str
        and re.fullmatch(r"resp_[0-9a-f]+", summary["response_id"])
    )
    require(
        response.get("id") == summary["response_id"]
        and review_text(response) == value.text
    )
    originals = obj(
        value.original_evidence_sha256,
        "request response text summary stream identity diagnostic",
        16384,
    )
    require(all(valid_sha(digest) for digest in originals.values()))


def identity_record(value: object, at: datetime) -> dict[str, Any]:
    require(type(value) is dict)
    result = obj(
        canonical(value),
        "configured_home_account_linked_to_exact_arm_token configured_username_sha256 "
        "current_token_oid_sha256 home_account_id_sha256 exact_arm_token_sha256 token_expires_epoch "
        "observed_at_epoch jwt_signature_independently_verified authenticated_same_token_read_required "
        "historical_apply_oid_attested human_apply_authorized not_a_plan_handoff_or_receipt "
        "authenticated_same_token_arm_read arm_read_response_epoch arm_read_body_sha256",
        16384,
    )
    exact(
        result,
        {
            "configured_username_sha256": USERNAME_SHA,
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
        "current_token_oid_sha256",
        "home_account_id_sha256",
        "exact_arm_token_sha256",
        "arm_read_body_sha256",
    ):
        require(valid_sha(result[key]))
    for key in ("observed_at_epoch", "arm_read_response_epoch", "token_expires_epoch"):
        require(type(result[key]) is int and result[key] > 0)
    require(abs(result["observed_at_epoch"] - result["arm_read_response_epoch"]) <= 120)
    require(at.timestamp() - 120 <= result["observed_at_epoch"] <= at.timestamp())
    require(result["arm_read_response_epoch"] <= at.timestamp() + 5)
    require(result["token_expires_epoch"] > at.timestamp() + MIN_TOKEN_SECONDS)
    return result


def workspace_record(
    raw: bytes, expected: str, source_sha: str, at: datetime
) -> dict[str, Any]:
    require(valid_sha(expected) and sha(raw) == expected)
    require(type(raw) is bytes and 0 < len(raw) <= 32768)
    value = record(raw)
    fields = set(
        (
            "workspace image mount device image_inode image_device mount_device mount_inode "
            "capacity_bytes encrypted_image_bound_to_mounted_apfs observed_at capture_authorized "
            "live_action_authorized payload_device payload_inode noindex_marker_inode empty_payload_verified "
            "noindex_marker_verified schema_version empty_workspace_prepared source_sha256 "
            "creation_command_sha256 requested_encryption live_data_copied not_a_receipt_or_permanent_readiness_proof"
        ).split()
    )
    setup_fields = {"setup_operation", "image_created_in_this_process"}
    require(set(value) in (fields, fields | setup_fields))
    if setup_fields <= set(value):
        require(value["setup_operation"] in ("prepare-empty", "complete-empty"))
        require(
            value["image_created_in_this_process"]
            is (value["setup_operation"] == "prepare-empty")
        )
    exact(
        value,
        {
            "schema_version": 1,
            "empty_workspace_prepared": True,
            "source_sha256": source_sha,
            "requested_encryption": "AES-256",
            "capacity_bytes": 8 * 1024**3,
            "encrypted_image_bound_to_mounted_apfs": True,
            "empty_payload_verified": True,
            "noindex_marker_verified": True,
            "capture_authorized": False,
            "live_action_authorized": False,
            "live_data_copied": False,
            "not_a_receipt_or_permanent_readiness_proof": True,
        },
    )
    require(
        type(value["workspace"]) is str
        and re.fullmatch(
            r"/private/tmp/cosmaneura-staging-snapshot-[a-z0-9_]{8}", value["workspace"]
        )
    )
    root = PurePosixPath(value["workspace"])
    exact(
        value,
        {"image": str(root / "staging.sparseimage"), "mount": str(root / "mounted")},
    )
    require(valid_sha(value["creation_command_sha256"]))
    require(
        type(value["device"]) is str
        and re.fullmatch(r"/dev/disk[0-9]+s[0-9]+", value["device"])
    )
    for name in (
        "image_inode",
        "image_device",
        "mount_device",
        "mount_inode",
        "payload_device",
        "payload_inode",
        "noindex_marker_inode",
    ):
        require(type(value[name]) is int and value[name] > 0)
    require(value["mount_device"] == value["payload_device"] != value["image_device"])
    require(type(value["observed_at"]) is str)
    observed = datetime.fromisoformat(value["observed_at"])
    require(
        observed.tzinfo is UTC
        and observed.isoformat() == value["observed_at"]
        and observed <= at
    )
    return value


def rules(value: object) -> list[dict[str, str]]:
    require(type(value) is list and len(value) <= 100)
    rows, names = [], set()
    for item in cast(list[Any], value):
        require(type(item) is dict and set(item) == {"name", "start", "end"})
        require(all(type(field) is str for field in item.values()))
        item = cast(dict[str, str], item)
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["name"]))
        first, last = (
            ipaddress.IPv4Address(item["start"]),
            ipaddress.IPv4Address(item["end"]),
        )
        require(
            str(first) == item["start"] and str(last) == item["end"] and first <= last
        )
        require(item["name"].casefold() not in names)
        names.add(item["name"].casefold())
        rows.append(dict(item))
    return sorted(rows, key=lambda item: item["name"].casefold())


def preparation_content(
    source: SourceCollection,
    source_review: ReviewRecord,
    workspace_manifest: bytes,
    *,
    expected_source_sha256: str,
    expected_source_review_sha256: str,
    expected_workspace_manifest_sha256: str,
    hdiutil_sha256: str,
    diskutil_sha256: str,
    operator_uid: int,
    observation_root: str,
    human_presence_confirmed_at: datetime,
    created: datetime,
) -> tuple[tuple[str, bytes], ...]:
    """Canonical content at its ORIGINAL creation time; never renew an artifact.

    Only preparation_files creates new content at the actual current time. The
    publisher reconstructs these bytes at their original time for comparison.
    """
    encoded_time(created)
    value = source_record(source, expected_source_sha256, created)
    bundle = sha(canonical(value["files"]))
    checked_review(
        source_review,
        expected_source_review_sha256,
        "source",
        bundle,
        bundle,
        datetime(2026, 9, 14, tzinfo=UTC),
        created,
    )
    manifest = workspace_record(
        workspace_manifest,
        expected_workspace_manifest_sha256,
        value["files"]["staging_encrypted_workspace.py"],
        created,
    )
    require(type(operator_uid) is int and operator_uid > 0)
    require(valid_sha(hdiutil_sha256) and valid_sha(diskutil_sha256))
    require(
        type(observation_root) is str
        and str(PurePosixPath(observation_root)) == observation_root
        and PurePosixPath(observation_root).parent == PACKET_PARENT
        and re.fullmatch(
            r"human-observation-[0-9a-f]{32}", PurePosixPath(observation_root).name
        )
    )
    presence = timestamp(encoded_time(human_presence_confirmed_at))
    require(created - timedelta(minutes=5) <= presence <= created)
    preparation = canonical(
        {
            "schema_version": 1,
            "policy_id": POLICY,
            "source_commit": value["source_commit"],
            "source_bundle_sha256": bundle,
            "source_provenance_sha256": expected_source_sha256,
            "source_review_summary_sha256": expected_source_review_sha256,
            "created_at": encoded_time(created),
            "expires_at": encoded_time(created + timedelta(minutes=30)),
            "human_presence_confirmed_at": encoded_time(presence),
            "operator_uid": operator_uid,
            "workspace_root": manifest["workspace"],
            "workspace_manifest_sha256": expected_workspace_manifest_sha256,
            "workspace_source_sha256": manifest["source_sha256"],
            "hdiutil_sha256": hdiutil_sha256,
            "diskutil_sha256": diskutil_sha256,
            "observation_root": observation_root,
            "public_ip_approval_sha256": IP_APPROVAL,
            "preparation_only": True,
            "database_credentials_read": False,
            "firewall_mutated": False,
            "execution_authorized": False,
            "deployment_authorized": False,
        }
    )
    return (
        ("preparation.json", preparation),
        ("source-provenance.json", source.provenance),
        ("source-review-summary.json", source_review.summary),
        ("source-review-request.json", source_review.request),
        ("source-review-response.json", source_review.response),
        ("source-review-text.md", source_review.text),
    )


@content_free
def preparation_files(
    source: SourceCollection,
    source_review: ReviewRecord,
    workspace_manifest: bytes,
    *,
    expected_source_sha256: str,
    expected_source_review_sha256: str,
    expected_workspace_manifest_sha256: str,
    hdiutil_sha256: str,
    diskutil_sha256: str,
    operator_uid: int,
    observation_root: str,
    human_presence_confirmed_at: datetime,
) -> tuple[tuple[str, bytes], ...]:
    """Create six public files at actual time; no writer, command or authority.

    Actual presence and original authenticated source/review/workspace anchors
    remain caller prerequisites. An exact human-command review is still owed.
    """
    return preparation_content(
        source,
        source_review,
        workspace_manifest,
        expected_source_sha256=expected_source_sha256,
        expected_source_review_sha256=expected_source_review_sha256,
        expected_workspace_manifest_sha256=expected_workspace_manifest_sha256,
        hdiutil_sha256=hdiutil_sha256,
        diskutil_sha256=diskutil_sha256,
        operator_uid=operator_uid,
        observation_root=observation_root,
        human_presence_confirmed_at=human_presence_confirmed_at,
        created=now_utc(),
    )


@dataclass(frozen=True)
class PlanDraft:
    plan: bytes
    identity: bytes
    observation: bytes
    workspace_manifest: bytes
    source: SourceCollection
    source_review: ReviewRecord


def plan_content(
    source: SourceCollection,
    source_review: ReviewRecord,
    observation: bytes,
    workspace_manifest: bytes,
    source_sha: str,
    review_sha: str,
    observation_sha: str,
    rule_name: str,
    created: datetime,
) -> tuple[bytes, bytes]:
    value = source_record(source, source_sha, created)
    bundle = sha(canonical(value["files"]))
    checked_review(
        source_review,
        review_sha,
        "source",
        bundle,
        bundle,
        datetime(2026, 9, 14, tzinfo=UTC),
        created,
    )
    require(valid_sha(observation_sha) and sha(observation) == observation_sha)
    observed = obj(
        observation,
        "schema_version source_commit source_bundle_sha256 operator_uid observed_at "
        "identity workspace_manifest_sha256 hdiutil_sha256 diskutil_sha256 application_binding_sha256 "
        "image public_ipv4 preexisting_rules preparation_only database_credentials_read firewall_mutated",
    )
    exact(
        observed,
        {
            "schema_version": 1,
            "source_commit": value["source_commit"],
            "source_bundle_sha256": bundle,
            "image": IMAGE,
            "preparation_only": True,
            "database_credentials_read": False,
            "firewall_mutated": False,
        },
    )
    require(type(observed["operator_uid"]) is int and observed["operator_uid"] > 0)
    require(
        created - timedelta(seconds=120)
        <= timestamp(observed["observed_at"])
        <= created
    )
    identity = identity_record(observed["identity"], created)
    workspace = workspace_record(
        workspace_manifest,
        observed["workspace_manifest_sha256"],
        value["files"]["staging_encrypted_workspace.py"],
        created,
    )
    for key in ("hdiutil_sha256", "diskutil_sha256", "application_binding_sha256"):
        require(valid_sha(observed[key]))
    require(type(observed["public_ipv4"]) is str)
    address = ipaddress.IPv4Address(observed["public_ipv4"])
    require(address.is_global and str(address) == observed["public_ipv4"])
    require(
        type(rule_name) is str
        and re.fullmatch(r"cosmaneura-snapshot-[0-9a-f]{16}", rule_name)
    )
    baseline = rules(observed["preexisting_rules"])
    require(rule_name.casefold() not in {item["name"].casefold() for item in baseline})
    plan = {
        "schema_version": 1,
        "policy_id": POLICY,
        "source_commit": value["source_commit"],
        "source_bundle_sha256": bundle,
        "created_at": encoded_time(created),
        "expires_at": encoded_time(created + timedelta(hours=4)),
        "identity_evidence_sha256": sha(canonical(identity)),
        "workspace_root": workspace["workspace"],
        "workspace_manifest_sha256": sha(workspace_manifest),
        "workspace_source_sha256": workspace["source_sha256"],
        **{
            key: observed[key]
            for key in (
                "hdiutil_sha256",
                "diskutil_sha256",
                "application_binding_sha256",
            )
        },
        "image": IMAGE,
        "source_revision": "89c31f0ae275",
        "rule": {"name": rule_name, "start": str(address), "end": str(address)},
        "preexisting_rules": baseline,
        "manual_window_approval_sha256": MANUAL_APPROVAL,
        "public_ip_approval_sha256": IP_APPROVAL,
        "access_seconds": 1800,
        "capture_seconds": 1200,
        "max_archive_bytes": 1024**3,
        "synthetic_clinical_data_confirmed": True,
        "credentials_and_workforce_data_remain_sensitive": True,
        "privileged_database_role_risk_disclosed": True,
        "concurrent_firewall_edit_risk_accepted": True,
        "firewall_management": "human_only",
        "native_firewall_expiry_proved": False,
        "execution_authorized": False,
        "deployment_authorized": False,
    }
    return canonical(plan), canonical(identity)


@content_free
def create_plan(
    source: SourceCollection,
    source_review: ReviewRecord,
    observation: bytes,
    workspace_manifest: bytes,
    *,
    expected_source_sha256: str,
    expected_source_review_sha256: str,
    expected_observation_sha256: str,
    rule_name: str,
) -> PlanDraft:
    """Real creation time only. Source review and human observations precede this.

    External hashes must come from authenticated collection/transport/human output,
    never an implementer's invented source record. The result is NOT reviewed yet.
    """
    created = now_utc()
    plan, identity = plan_content(
        source,
        source_review,
        observation,
        workspace_manifest,
        expected_source_sha256,
        expected_source_review_sha256,
        expected_observation_sha256,
        rule_name,
        created,
    )
    return PlanDraft(
        plan, identity, observation, workspace_manifest, source, source_review
    )


def check_plan(draft: PlanDraft, expected: str, at: datetime) -> dict[str, Any]:
    require(
        type(draft) is PlanDraft and valid_sha(expected) and sha(draft.plan) == expected
    )
    plan = record(draft.plan)
    created = timestamp(plan["created_at"])
    require(
        created <= at and at + timedelta(seconds=1800) < timestamp(plan["expires_at"])
    )
    regenerated = plan_content(
        draft.source,
        draft.source_review,
        draft.observation,
        draft.workspace_manifest,
        sha(draft.source.provenance),
        sha(draft.source_review.summary),
        sha(draft.observation),
        plan["rule"]["name"],
        created,
    )
    require(regenerated == (draft.plan, draft.identity))
    return plan


@dataclass(frozen=True)
class HandoffDraft:
    handoff: bytes
    plan: PlanDraft
    fresh_source: SourceCollection
    plan_review: ReviewRecord


def handoff_content(
    draft: PlanDraft,
    plan_review: ReviewRecord,
    fresh_source: SourceCollection,
    expected_plan: str,
    expected_review: str,
    expected_source: str,
    presence: datetime,
    created: datetime,
) -> bytes:
    plan = check_plan(draft, expected_plan, created)
    require(created - timedelta(minutes=5) <= presence <= created)
    encoded_time(presence)
    require(
        record(draft.identity)["token_expires_epoch"]
        > created.timestamp() + MIN_TOKEN_SECONDS
    )
    fresh = source_record(fresh_source, expected_source, created)
    exact(
        fresh,
        {
            "source_commit": plan["source_commit"],
            "files": record(draft.source.provenance)["files"],
        },
    )
    checked_review(
        plan_review,
        expected_review,
        "plan",
        expected_plan,
        plan["source_bundle_sha256"],
        timestamp(plan["created_at"]),
        created,
    )
    return canonical(
        {
            "schema_version": 1,
            "policy_id": POLICY,
            "source_commit": plan["source_commit"],
            "source_bundle_sha256": plan["source_bundle_sha256"],
            "source_provenance_sha256": expected_source,
            "plan_sha256": expected_plan,
            "identity_file_sha256": sha(draft.identity),
            "identity_evidence_sha256": plan["identity_evidence_sha256"],
            "workspace_manifest_sha256": plan["workspace_manifest_sha256"],
            "source_review_summary_sha256": sha(draft.source_review.summary),
            "plan_review_summary_sha256": expected_review,
            "created_at": encoded_time(created),
            "expires_at": encoded_time(created + timedelta(minutes=10)),
            "human_presence_confirmed_at": encoded_time(presence),
            "operator_uid": record(draft.observation)["operator_uid"],
            "execution_authorized": False,
            "deployment_authorized": False,
        }
    )


@content_free
def create_handoff(
    draft: PlanDraft,
    plan_review: ReviewRecord,
    fresh_source: SourceCollection,
    *,
    expected_plan_sha256: str,
    expected_plan_review_sha256: str,
    expected_source_sha256: str,
    human_presence_confirmed_at: datetime,
) -> HandoffDraft:
    """Only AFTER actual same-task Terminal presence; NEVER infer presence or renew.

    The caller supplies the timestamp of the actual confirmation, not a scheduled
    return estimate. This helper cannot independently establish who confirmed it.
    """
    raw = handoff_content(
        draft,
        plan_review,
        fresh_source,
        expected_plan_sha256,
        expected_plan_review_sha256,
        expected_source_sha256,
        human_presence_confirmed_at,
        now_utc(),
    )
    return HandoffDraft(raw, draft, fresh_source, plan_review)


@content_free
def packet_files(
    draft: HandoffDraft,
    handoff_review: ReviewRecord,
    *,
    expected_handoff_sha256: str,
    expected_handoff_review_sha256: str,
) -> tuple[tuple[str, bytes], ...]:
    """Return exact16public files, no write/command/claim; preserve all originals.

    External handoff and review hashes MUST be obtained from its real independent
    exact review. No expected hash is derived here to establish that authority.
    """
    now = now_utc()
    require(type(draft) is HandoffDraft and valid_sha(expected_handoff_sha256))
    require(sha(draft.handoff) == expected_handoff_sha256)
    handoff = record(draft.handoff)
    created, expires = (
        timestamp(handoff["created_at"]),
        timestamp(handoff["expires_at"]),
    )
    require(created <= now and now + timedelta(seconds=120) < expires)
    require(
        record(draft.plan.identity)["token_expires_epoch"]
        > now.timestamp() + MIN_TOKEN_SECONDS
    )
    check_plan(draft.plan, handoff["plan_sha256"], now)
    expected = handoff_content(
        draft.plan,
        draft.plan_review,
        draft.fresh_source,
        handoff["plan_sha256"],
        handoff["plan_review_summary_sha256"],
        handoff["source_provenance_sha256"],
        timestamp(handoff["human_presence_confirmed_at"]),
        created,
    )
    require(expected == draft.handoff)
    checked_review(
        handoff_review,
        expected_handoff_review_sha256,
        "handoff",
        expected_handoff_sha256,
        handoff["source_bundle_sha256"],
        created,
        min(expires, now + timedelta(microseconds=1)),
    )
    output = {
        "handoff.json": draft.handoff,
        "plan.json": draft.plan.plan,
        "identity.json": draft.plan.identity,
        "source-provenance.json": draft.fresh_source.provenance,
    }
    for kind, review in (
        ("source", draft.plan.source_review),
        ("plan", draft.plan_review),
        ("handoff", handoff_review),
    ):
        for part, extension in (
            ("summary", "json"),
            ("request", "json"),
            ("response", "json"),
            ("text", "md"),
        ):
            output[f"{kind}-review-{part}.{extension}"] = getattr(review, part)
    require(len(output) == 16)
    return tuple(sorted(output.items()))


if __name__ == "__main__":
    raise SystemExit("snapshot_public_plan_requires_authenticated_human_preparation")
