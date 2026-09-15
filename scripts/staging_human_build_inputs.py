"""Prepare public build inputs in an owned workspace; never build or publish.

The reviewed, explicitly authorized controller authenticates source/config/archive
hashes before calling this module. Hash-shaped caller data is NOT provenance.
Delegated agent execution must be recorded as such, never as human presence.
No ambient environment, credentials, process runner, network, or CLI is used.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import stat
import tarfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast

NIXPACKS_VERSION = "1.41.0"
NIXPACKS_ARCHIVE_SHA256 = (
    "0c6799690ad0b7328691fe3e457ec93ddc250ab229b7422fe8d8cb6ecc72a70c"
)
NIXPACKS_ARCHIVE_BYTES = 3299388
NIXPACKS_BINARY_SHA256 = (
    "412128c77876b8c96bdde0c01c0fa411d935ae80bea8bec523d65692904a14fc"
)
NIXPACKS_BINARY_BYTES = 8194488
SOURCE_REPOSITORY = "https://github.com/CosmaNeura/cosmaneura"
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_CANONICAL_PATHS = 20000
MAX_SOURCE_COMPONENTS = 100000
PUBLIC_KEYS = frozenset(
    {
        "NODE_ENV",
        "OPENAPI_JSON_PATH",
        "VITE_CLERK_PUBLIC_KEY",
        "VITE_INTAKE_CHAT_DOMAIN",
        "VITE_POSTHOG_HOST",
        "VITE_POSTHOG_KEY",
        "VITE_PYTHON_URL",
        "VITE_SENTRY_DSN",
        "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED",
        "VITE_SOVEREIGN_PROVIDER_URL",
    }
)
SHARED_ENVIRONMENT = {
    "PYTHONFAULTHANDLER": "1",
    "PYTHONUNBUFFERED": "1",
    "PYTHONHASHSEED": "random",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONSAFEPATH": "1",
    "PYTHONIOENCODING": "UTF-8",
    "TZ": "UTC",
}
SHARED_ENV_FILE_SHA256 = (
    "157edd861fe248057d5103cd4fa1256837062aa5b86736d4ef5c7da0664175a2"
)


class BuildInputRefused(RuntimeError):
    """Content-free refusal; a partial workspace is preserved, never retried."""


def require(value: object) -> None:
    if not value:
        raise BuildInputRefused("staging_build_input_refused")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: str) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def public_configuration(raw: bytes, expected_sha256: str) -> dict[str, str]:
    """Validate an already resolved public file, not a secret resolver."""
    require(type(raw) is bytes and 0 < len(raw) <= 16384)
    require(digest(expected_sha256) and sha(raw) == expected_sha256)

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise BuildInputRefused("staging_build_input_refused") from None
    require(type(value) is dict and set(value) == PUBLIC_KEYS)
    require(
        all(
            type(v) is str and 0 < len(v) <= 4096 and v.isprintable()
            for v in value.values()
        )
    )
    config = cast(dict[str, str], value)
    require(config["NODE_ENV"] == "staging")
    require(config["OPENAPI_JSON_PATH"] == "openapi.json")
    require(config["VITE_PYTHON_URL"] == "https://staging-app.cosmaneura.com/")
    require(config["VITE_INTAKE_CHAT_DOMAIN"] == "staging-chat.cosmaneura.com")
    require(config["VITE_CLERK_PUBLIC_KEY"].startswith("pk_test_"))
    require(len(config["VITE_CLERK_PUBLIC_KEY"]) > len("pk_test_"))
    require(config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] in {"true", "false"})
    return config


def native_builder_bytes(raw: bytes) -> bytes:
    """Verify the exact public arm64 Mac asset without executing its contents."""
    require(type(raw) is bytes and len(raw) == NIXPACKS_ARCHIVE_BYTES)
    require(sha(raw) == NIXPACKS_ARCHIVE_SHA256)
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            require(len(members) == 1)
            member = members[0]
            require(member.name == "nixpacks" and member.isfile())
            require(not member.linkname and member.size == NIXPACKS_BINARY_BYTES)
            require(member.mode == 0o755)
            stream = archive.extractfile(member)
            require(stream is not None)
            assert stream is not None
            binary = stream.read(NIXPACKS_BINARY_BYTES + 1)
    except (tarfile.TarError, OSError):
        raise BuildInputRefused("staging_build_input_refused") from None
    require(
        len(binary) == NIXPACKS_BINARY_BYTES and sha(binary) == NIXPACKS_BINARY_SHA256
    )
    require(binary[:4] == b"\xcf\xfa\xed\xfe")
    require(int.from_bytes(binary[4:8], "little") == 0x0100000C)
    return binary


@dataclass(frozen=True)
class BuildInputs:
    candidate_sha: str
    source_archive_sha256: str
    frontend_configuration_sha256: str
    created_at: str


@dataclass(frozen=True)
class PreparedBuild:
    root: Path
    inputs: BuildInputs
    files: tuple[tuple[str, str], ...]
    frontend: tuple[tuple[str, str], ...]

    @property
    def image(self) -> str:
        return (
            "cosmaneura-build-"
            + self.root.name.removeprefix("staging-build-")
            + ":"
            + self.inputs.candidate_sha
        )

    @property
    def extraction_container(self) -> str:
        return "cosmaneura-assets-" + self.root.name.removeprefix("staging-build-")


def canonical_name(name: str) -> str:
    """Conservative macOS caseless/canonical comparison; retain original names."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


@dataclass
class _PathNode:
    spelling: str
    children: dict[str, _PathNode] = field(default_factory=dict)
    is_file: bool = False


class SourcePathIndex:
    """Bounded component trie; no repeated construction of cumulative prefixes."""

    def __init__(self) -> None:
        self.root = _PathNode("")
        self.components: dict[str, str] = {}
        self.observations = 0
        self.nodes = 0

    def observe(self, path: PurePosixPath, *, is_file: bool) -> str:
        require(path.parts and type(is_file) is bool)
        self.observations += len(path.parts)
        require(self.observations <= MAX_SOURCE_COMPONENTS)
        node = self.root
        canonical_parts: list[str] = []
        for part in path.parts:
            require(not node.is_file)
            canonical = self.components.get(part)
            if canonical is None:
                canonical = canonical_name(part)
                self.components[part] = canonical
            require(canonical not in {".", "..", ".git", ".nixpacks"})
            require(canonical_parts or canonical != "public")
            canonical_parts.append(canonical)
            child = node.children.get(canonical)
            if child is None:
                self.nodes += 1
                require(self.nodes <= MAX_CANONICAL_PATHS)
                child = _PathNode(part)
                node.children[canonical] = child
            require(child.spelling == part)
            node = child
        require(not node.is_file)
        if is_file:
            require(not node.children)
            node.is_file = True
        return "/".join(canonical_parts)


def source_member_name(raw: bytes, member: tarfile.TarInfo) -> str:
    """Check original POSIX Git-archive names before tarfile's slash rewriting."""
    require(member.type in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE})
    require(member.sparse is None and 512 <= member.offset_data <= len(raw))
    header = raw[member.offset_data - 512 : member.offset_data]
    require(header[257:265] == tarfile.POSIX_MAGIC and header[156:157] == member.type)
    field = header[:100].split(b"\0", 1)[0].decode("utf-8")
    prefix = header[345:500].split(b"\0", 1)[0].decode("utf-8")
    header_name = prefix + "/" + field if prefix else field

    def checked(value: str) -> str:
        require(type(value) is str)
        if member.isdir() and value.endswith("/"):
            value = value[:-1]
        require(not value.endswith("/"))
        return value

    # PAX path is authoritative; the legacy name may be truncated mid-component.
    name = checked(member.pax_headers.get("path", header_name))
    require(name == member.name)
    return name


def source_entries(raw: bytes, inputs: BuildInputs) -> list[tuple[str, bytes, bool]]:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_SOURCE_BYTES)
    require(type(inputs) is BuildInputs)
    require(type(inputs.candidate_sha) is str and type(inputs.created_at) is str)
    require(re.fullmatch(r"[0-9a-f]{40}", inputs.candidate_sha) is not None)
    require(
        digest(inputs.source_archive_sha256)
        and sha(raw) == inputs.source_archive_sha256
    )
    require(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", inputs.created_at)
        is not None
    )
    try:
        datetime.strptime(inputs.created_at, "%Y-%m-%dT%H:%M:%SZ")
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            require((archive.pax_headers or {}).get("comment") == inputs.candidate_sha)
            members = archive.getmembers()
            require(0 < len(members) <= 20000)
            names: set[str] = set()
            paths = SourcePathIndex()
            files: list[tuple[str, bytes, bool]] = []
            size = 0
            for member in members:
                name = source_member_name(raw, member)
                path = PurePosixPath(name)
                require(len(name) <= 1024 and name.isprintable())
                require(name == str(path) and not path.is_absolute())
                canonical = paths.observe(path, is_file=member.isfile())
                require(canonical not in names and not member.linkname)
                require(member.mode & 0o7000 == 0)
                names.add(canonical)
                if member.isdir():
                    continue
                size += member.size
                require(
                    0 <= member.size <= 64 * 1024 * 1024 and size <= MAX_SOURCE_BYTES
                )
                stream = archive.extractfile(member)
                require(stream is not None)
                assert stream is not None
                contents = stream.read(member.size + 1)
                require(len(contents) == member.size)
                files.append((name, contents, bool(member.mode & 0o111)))
    except (tarfile.TarError, OSError, ValueError):
        raise BuildInputRefused("staging_build_input_refused") from None
    file_names = {name for name, _, _ in files}
    require(
        {
            ".env",
            "web/package.json",
            "web/openapi.json",
            "pyproject.toml",
            ".dockerignore",
            "web/.dockerignore",
            "web/build-environment.mjs",
            "web/scripts/build-production.mjs",
        }
        <= file_names
    )
    shared = next(contents for name, contents, _ in files if name == ".env")
    require(sha(shared) == SHARED_ENV_FILE_SHA256)
    return files


def write_exclusive(path: Path, raw: bytes, mode: int) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    require(path.read_bytes() == raw)


def prepare_build_inputs(
    parent: Path,
    inputs: BuildInputs,
    *,
    source_archive: bytes,
    frontend_json: bytes,
    native_archive: bytes,
) -> PreparedBuild:
    """Create a fresh owned scratch layout; never modify a checkout/container.

    No cleanup on failure. This is input preparation, not a build runner or a
    host sandbox. OS/same-user integrity is trusted. The later authorized reviewed
    executor must bind tool/runtime/source identity and block credentials.
    """
    require(type(inputs) is BuildInputs)
    frontend = public_configuration(frontend_json, inputs.frontend_configuration_sha256)
    binary = native_builder_bytes(native_archive)
    entries = source_entries(source_archive, inputs)
    info = parent.lstat()
    require(parent.is_absolute() and parent.resolve() == parent)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid())
    require(stat.S_IMODE(info.st_mode) == 0o700)
    root = parent / ("staging-build-" + secrets.token_hex(16))
    root.mkdir(mode=0o700)
    for name in ("source", "home", "docker-config", "bin", "artifacts"):
        (root / name).mkdir(mode=0o700)
    manifest: list[tuple[str, str]] = []
    for name, raw, executable in entries:
        target = root / "source" / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_exclusive(target, raw, 0o700 if executable else 0o600)
        manifest.append((name, sha(raw)))
    write_exclusive(root / "bin/nixpacks", binary, 0o500)
    write_exclusive(root / "frontend.json", frontend_json, 0o400)
    write_exclusive(root / "docker-config/config.json", b"{}\n", 0o400)
    return PreparedBuild(
        root, inputs, tuple(sorted(manifest)), tuple(sorted(frontend.items()))
    )


def build_arguments(prepared: PreparedBuild, *, javascript: bool) -> tuple[str, ...]:
    """Exact argv only, no shell/environment evaluation or process execution."""
    require(type(javascript) is bool)
    inputs = prepared.inputs
    environment = dict(SHARED_ENVIRONMENT)
    environment.update(
        {"BUILD_COMMIT": inputs.candidate_sha, "BUILD_CREATED_AT": inputs.created_at}
    )
    if javascript:
        environment.update(dict(prepared.frontend))
        environment.update({"VITE_BUILD_COMMIT": inputs.candidate_sha, "CI": "true"})
    else:
        environment["PYTHON_ENV"] = "staging"
    image = prepared.image + ("-javascript" if javascript else "")
    args = [
        str(prepared.root / "bin/nixpacks"),
        "build",
        "web" if javascript else ".",
        "--name",
        image,
        "--platform=linux/amd64",
        "--no-cache",
    ]
    for key, value in sorted(environment.items()):
        args.extend(("--env", key + "=" + value))
    for label, value in (
        ("org.opencontainers.image.revision", inputs.candidate_sha),
        ("org.opencontainers.image.created", inputs.created_at),
        ("org.opencontainers.image.source", SOURCE_REPOSITORY),
    ):
        args.extend(("--label", label + "=" + value))
    return tuple(args)
