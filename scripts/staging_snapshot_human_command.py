"""Publish reviewed PUBLIC source and render (NEVER RUN) a human command draft.

No human runtime imports, subprocess, credentials, network, claim or receipt.
Original source/review/provider evidence and this helper must be authenticated
by the actual caller. Hashes/dataclasses are consistency checks, not authority.
Every command draft STILL needs independent exact review and immediate read-only
revalidation before human use. This is not a malicious same-UID/root sandbox.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import staging_snapshot_packet_publication as publication
import staging_snapshot_public_plan as plan
from staging_snapshot_review_record import ReviewRecord, canonical, record, sha
from staging_snapshot_source_provenance import SOURCE_NAMES, SourceCollection

# Exact runtime independently passed reviews47/48; never derive trust at launch.
BUNDLE_SHA256 = "0c79667e6e7088c926b609afb6991ff093098ea725bcd8f7842bfc1760711d50"
LOADER_SHA256 = "2f69e3a66c6ce9339bcb86586b8a59b32a2734215d537d3cdc8276b46cbc3d9a"
RUNTIME_ROOT = Path(
    "/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/"
    ".trusted-runtime-bin/homebrew"
)
PYTHON_PREFIX = RUNTIME_ROOT / (
    "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14"
)
PYTHON = PYTHON_PREFIX / "bin/python3.14"
# Reviewed runtime's executable pin, NOT a digest of whatever currently exists.
PYTHON_SHA256 = "e8179bf1ac3710499f164f877a3713bbaa34b5921beb70881f5dcd4c45a17827"
PYTHON_BYTES = 52448
SOURCE_LIMITS = {name: 128 * 1024 for name in SOURCE_NAMES}


class HumanCommandRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise HumanCommandRefused("snapshot_human_command_refused")


@dataclass(frozen=True)
class SourcePacket:
    source: SourceCollection
    review: ReviewRecord
    expected_source_sha256: str
    expected_review_sha256: str


@dataclass(frozen=True)
class CommandDraft:
    """Unreviewed text only: never a launch receipt or execution authorization."""

    command: bytes
    command_sha256: str
    not_after: str
    source_bundle_sha256: str
    artifact_sha256: str
    review_summary_sha256: str
    independent_exact_command_review_required: bool = True


def source_check(packet: SourcePacket) -> dict[str, bytes]:
    require(type(packet) is SourcePacket)
    now = plan.now_utc()
    value = plan.source_record(packet.source, packet.expected_source_sha256, now)
    files = publication.file_map(packet.source.source_files, SOURCE_LIMITS)
    require(sha(canonical(value["files"])) == BUNDLE_SHA256)
    require(sha(files["staging_snapshot_bootstrap.py"]) == LOADER_SHA256)
    plan.checked_review(
        packet.review,
        packet.expected_review_sha256,
        "source",
        BUNDLE_SHA256,
        BUNDLE_SHA256,
        datetime(2026, 9, 14, tzinfo=UTC),
        now,
    )
    return files


def publish_source(
    packet: SourcePacket,
    *,
    source_token: str,
    revalidate: publication.Publication | None = None,
) -> publication.Publication:
    """18 reviewed0400 files; bootstrap LAST; no execution packet/presence invented.

    Protected-source observation must be fresh on BOTH publication/revalidation.
    Refreshing that public evidence cannot renew a plan, handoff or receipt.
    """
    try:
        return publication.publish(
            lambda: source_check(packet), "source", source_token, None, revalidate
        )
    except BaseException:
        raise HumanCommandRefused("snapshot_human_command_refused") from None


def token(value: publication.Publication, kind: str) -> str:
    require(type(value) is publication.Publication)
    require(value.packet.parent == publication.PACKET_PARENT)
    match = re.fullmatch("human-" + kind + "-([0-9a-f]{32})", value.packet.name)
    require(match is not None)
    assert match is not None
    return match[1]


def startup_environment(uid: int) -> dict[str, str]:
    """Exact frozen-loader environment; never import/execute the human loader."""
    require(type(uid) is int and uid > 0)
    libraries = [
        PYTHON_PREFIX,
        *(
            RUNTIME_ROOT / "opt" / name / "lib"
            for name in (
                "libsodium",
                "libyaml",
                "lz4",
                "mpdecimal",
                "openssl@3",
                "readline",
                "sqlite",
                "xz",
                "zstd",
            )
        ),
    ]
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/dev/null",
        "LANG": "C",
        "LC_ALL": "C",
        "__CF_USER_TEXT_ENCODING": f"0x{uid:X}:0x0:0x0",
        "OPENSSL_CONF": "/dev/null",
        "OPENSSL_MODULES": str(RUNTIME_ROOT / "opt/openssl@3/lib/ossl-modules"),
        "DYLD_LIBRARY_PATH": ":".join(map(str, libraries)),
    }


def shell_text(
    source_path: Path,
    packet_path: Path,
    sources: dict[str, bytes],
    files: dict[str, bytes],
    artifact_sha: str,
    review_sha: str,
    preparing: bool,
    uid: int,
    not_before: int,
    not_after: int,
) -> bytes:
    """Internal text formatter only. No current-file hash can establish a pin.

    /usr/bin/env clears ambient settings BEFORE the shell and AGAIN before Python.
    -c carries shell text as an argument: NEVER steal Terminal stdin via a pipe
    or heredoc. Per-file hash subprocesses inherit no Terminal input.
    """
    q = shlex.quote
    lines = [
        "set -eu",
        "umask 077",
        "ulimit -c 0",
        "refuse() { /usr/bin/printf '%s\\n' staging_snapshot_launch_refused >&2; exit 2; }",
        "[ -t 0 ] && [ -t 1 ] && [ -t 2 ] || refuse",
        f'[ "$(/usr/bin/id -u)" = {q(str(uid))} ] || refuse',
        f'[ "$(/usr/bin/id -ru)" = {q(str(uid))} ] || refuse',
        "check_time() {",
        "  snapshot_epoch=$(/bin/date -u +%s) || refuse",
        "  case $snapshot_epoch in ''|*[!0-9]*) refuse ;; esac",
        f'  [ "$snapshot_epoch" -ge {not_before} ] && [ "$snapshot_epoch" -lt {not_after} ] || refuse',
        "}",
        "check_file() {",
        '  [ -f "$1" ] && [ ! -L "$1" ] && [ -r "$1" ] || refuse',
        '  snapshot_meta=$(/usr/bin/stat -f "%z:%Lp:%u:%l" "$1") || refuse',
        '  [ "$snapshot_meta" = "$3:$4:$5:1" ] || refuse',
        '  snapshot_sum=$(/usr/bin/shasum -a 256 -- "$1" </dev/null) || refuse',
        '  [ "$snapshot_sum" = "$2  $1" ] || refuse',
        "}",
        "check_time",
    ]
    checks = [(PYTHON, PYTHON_SHA256, PYTHON_BYTES, "500")]
    checks += [
        (root / name, sha(raw), len(raw), "400")
        for root, values in ((source_path, sources), (packet_path, files))
        for name, raw in sorted(values.items())
    ]
    for path, digest, size, mode in checks:
        lines.append(
            shlex.join(["check_file", str(path), digest, str(size), mode, str(uid)])
        )
    lines += ["check_time", "[ -t 0 ] && [ -t 1 ] && [ -t 2 ] || refuse"]
    arguments = [
        "/usr/bin/env",
        "-i",
        *(f"{key}={value}" for key, value in startup_environment(uid).items()),
        str(PYTHON),
        "-I",
        "-S",
        "-B",
        str(source_path / "staging_snapshot_bootstrap.py"),
        *(["prepare"] if preparing else []),
        LOADER_SHA256,
        str(packet_path),
        artifact_sha,
        review_sha,
    ]
    lines.append("exec " + shlex.join(arguments))
    command = (
        shlex.join(
            [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/bin:/bin",
                "LANG=C",
                "LC_ALL=C",
                "/bin/sh",
                "-c",
                "\n".join(lines),
            ]
        )
        + "\n"
    )
    require(len(command.encode()) <= 32768)
    return command.encode()


def draft_command(
    source: SourcePacket,
    source_publication: publication.Publication,
    packet_publication: publication.Publication,
    files: dict[str, bytes],
    artifact_sha: str,
    review_sha: str,
    presence: datetime,
    preparing: bool,
) -> CommandDraft:
    publication.presence(presence)
    sources = source_check(source)
    require(files["source-provenance.json"] == source.source.provenance)
    require(files["source-review-summary.json"] == source.review.summary)
    now = plan.now_utc()
    artifact = record(files["preparation.json" if preparing else "handoff.json"])
    uid = artifact["operator_uid"]
    require(type(uid) is int and uid == os.geteuid())
    cutoffs = [
        presence + timedelta(minutes=5),
        plan.timestamp(record(source.source.provenance)["observed_at"])
        + timedelta(minutes=10),
        plan.timestamp(artifact["expires_at"])
        - timedelta(seconds=0 if preparing else 120),
    ]
    if not preparing:
        cutoffs += [
            plan.timestamp(record(files["plan.json"])["expires_at"])
            - timedelta(seconds=1800),
            datetime.fromtimestamp(
                record(files["identity.json"])["token_expires_epoch"]
                - plan.MIN_TOKEN_SECONDS,
                UTC,
            ),
        ]
    not_after = min(cutoffs).replace(microsecond=0)
    require(now + timedelta(seconds=30) < not_after)
    # A lower bound derived from original content keeps identical text stable on
    # read-only revalidation, while rejecting a wall clock before that content.
    not_before = max(
        plan.timestamp(artifact["created_at"]),
        presence,
        plan.timestamp(record(source.source.provenance)["observed_at"]),
        plan.timestamp(record(source.review.summary)["completed_at"]),
        plan.timestamp(
            record(
                files[
                    "source-review-summary.json"
                    if preparing
                    else "handoff-review-summary.json"
                ]
            )["completed_at"]
        ),
    )
    command = shell_text(
        source_publication.packet,
        packet_publication.packet,
        sources,
        files,
        artifact_sha,
        review_sha,
        preparing,
        uid,
        int(not_before.timestamp()) + 1,
        int(not_after.timestamp()),
    )
    return CommandDraft(
        command,
        sha(command),
        plan.encoded_time(not_after),
        BUNDLE_SHA256,
        artifact_sha,
        review_sha,
    )


def preparation_command(
    source: SourcePacket,
    source_publication: publication.Publication,
    packet: publication.PreparationPacket,
    packet_publication: publication.Publication,
    *,
    expected_preparation_sha256: str,
    expected_source_review_sha256: str,
    human_presence_confirmed_at: datetime,
) -> CommandDraft:
    """READ-ONLY revalidate both publications, then render an UNREVIEWED draft."""
    try:
        publication.presence(human_presence_confirmed_at)
        publish_source(
            source,
            source_token=token(source_publication, "source"),
            revalidate=source_publication,
        )
        publication.publish_preparation(
            packet,
            expected_preparation_sha256=expected_preparation_sha256,
            expected_source_review_sha256=expected_source_review_sha256,
            packet_token=token(packet_publication, "preparation"),
            human_presence_confirmed_at=human_presence_confirmed_at,
            revalidate=packet_publication,
        )
        return draft_command(
            source,
            source_publication,
            packet_publication,
            publication.preparation_check(
                packet, expected_preparation_sha256, expected_source_review_sha256
            ),
            expected_preparation_sha256,
            expected_source_review_sha256,
            human_presence_confirmed_at,
            True,
        )
    except BaseException:
        raise HumanCommandRefused("snapshot_human_command_refused") from None


def capture_command(
    source: SourcePacket,
    source_publication: publication.Publication,
    draft: plan.HandoffDraft,
    handoff_review: ReviewRecord,
    packet_publication: publication.Publication,
    *,
    expected_handoff_sha256: str,
    expected_handoff_review_sha256: str,
    human_presence_confirmed_at: datetime,
) -> CommandDraft:
    """Independent exact plan/handoff reviews REQUIRED; command review still owed."""
    try:
        publication.presence(human_presence_confirmed_at)
        publish_source(
            source,
            source_token=token(source_publication, "source"),
            revalidate=source_publication,
        )
        publication.publish_capture(
            draft,
            handoff_review,
            expected_handoff_sha256=expected_handoff_sha256,
            expected_handoff_review_sha256=expected_handoff_review_sha256,
            packet_token=token(packet_publication, "snapshot"),
            human_presence_confirmed_at=human_presence_confirmed_at,
            revalidate=packet_publication,
        )
        files = dict(
            plan.packet_files(
                draft,
                handoff_review,
                expected_handoff_sha256=expected_handoff_sha256,
                expected_handoff_review_sha256=expected_handoff_review_sha256,
            )
        )
        return draft_command(
            source,
            source_publication,
            packet_publication,
            files,
            expected_handoff_sha256,
            expected_handoff_review_sha256,
            human_presence_confirmed_at,
            False,
        )
    except BaseException:
        raise HumanCommandRefused("snapshot_human_command_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_human_command_requires_authenticated_reviewed_inputs")
