"""Credential-free staging build; never resolve credentials or publish.

The reviewed controller MUST authenticate source/candidate/public-config,
runtime bindings and the build authorization before and during this call.
An agent controller additionally needs explicit founder-delegated execution
authority; it must identify itself as an agent, never claim human presence.
Callbacks and dataclasses do not authenticate themselves. No CLI/default
authority exists. Tests use invented inputs and mock all Docker/Nixpacks calls.
OS and same-user integrity are trusted; this is not a hostile-code host sandbox.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

import staging_human_build_inputs as inputs
from staging_human_build_process import BuildProcesses, build_deadline

DOCKER = Path("/opt/homebrew/Cellar/docker/29.7.2/bin/docker")
BUILDX = Path("/opt/homebrew/Cellar/docker-buildx/0.37.0/bin/docker-buildx")
SOCKET = Path("/Users/jw/.colima/default/docker.sock")
TOOLS = {
    DOCKER: (
        27924322,
        "adb33a6b552536d8219b2d8c32f3c44def98058e76e97c8031498471c8dfdac8",
    ),
    BUILDX: (
        62298290,
        "16b0610021197ff1c373bb60b1adfb757b41c170b7776fe748b26e7ef68026bd",
    ),
    Path("/bin/ps"): (
        170432,
        "3bbba882e30d91fc4ff6e6844ca7c59fef4351b9a3fea35dca55708d6d487d1c",
    ),
}
MAX_ASSETS = 256 * 1024**2
MAX_IMAGE = 16 * 1024**3
MAX_LOG = 64 * 1024**2


class BuildExecutionRefused(RuntimeError):
    """Fixed public code; inspect only the private bounded logs for diagnostics."""

    failure_evidence_written: bool = False


def require(value: object) -> None:
    if not value:
        raise BuildExecutionRefused("staging_build_execution_refused")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class EngineBinding:
    """Previously inspected, reviewed local binding, not provider authentication."""

    id_sha256: str
    socket_device: int
    socket_inode: int


@dataclass(frozen=True)
class BuildArchive:
    """Build evidence only. The distinct publisher owes its own fresh authority."""

    path: Path
    sha256: str
    size: int
    image_id: str
    candidate_sha: str
    public_assets_sha256: str
    frontend_configuration_sha256: str
    metadata_path: Path


def directory(path: Path) -> None:
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISDIR(info.st_mode))
    require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)


def regular(path: Path, maximum: int, modes: set[int]) -> bytes:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) in modes)
    require(0 <= info.st_size <= maximum)
    raw = path.read_bytes()
    require(len(raw) == info.st_size)
    return raw


def file_sha(path: Path, maximum: int) -> tuple[str, int]:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o400)
    require(0 < info.st_size <= maximum)
    result, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024**2):
            size += len(chunk)
            require(size <= maximum)
            result.update(chunk)
    after = path.lstat()
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_uid",
        "st_gid",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    require(
        size == info.st_size
        and all(getattr(info, name) == getattr(after, name) for name in fields)
    )
    return result.hexdigest(), size


def tool_check(prepared: inputs.PreparedBuild, engine: EngineBinding) -> None:
    require(platform.system() == "Darwin" and platform.machine() == "arm64")
    require(os.geteuid() != 0 and type(engine) is EngineBinding)
    require(inputs.digest(engine.id_sha256))
    require(type(engine.socket_device) is int and engine.socket_device > 0)
    require(type(engine.socket_inode) is int and engine.socket_inode > 0)
    for path, (size, digest) in TOOLS.items():
        info = path.lstat()
        require(path.resolve() == path and stat.S_ISREG(info.st_mode))
        require(info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022)
        require(info.st_size == size and sha(path.read_bytes()) == digest)
    sock = SOCKET.lstat()
    require(SOCKET.resolve() == SOCKET and stat.S_ISSOCK(sock.st_mode))
    require(sock.st_uid == os.geteuid() and stat.S_IMODE(sock.st_mode) == 0o600)
    require((sock.st_dev, sock.st_ino) == (engine.socket_device, engine.socket_inode))
    binary = regular(
        prepared.root / "bin/nixpacks", inputs.NIXPACKS_BINARY_BYTES, {0o500}
    )
    require(
        len(binary) == inputs.NIXPACKS_BINARY_BYTES
        and sha(binary) == inputs.NIXPACKS_BINARY_SHA256
    )
    require(regular(prepared.root / "docker-config/config.json", 3, {0o400}) == b"{}\n")


def source_check(
    prepared: inputs.PreparedBuild,
    source_archive: bytes,
    public_files: tuple[tuple[str, str], ...] = (),
) -> None:
    require(type(prepared) is inputs.PreparedBuild)
    root = prepared.root
    require(
        root.is_absolute() and re.fullmatch(r"staging-build-[0-9a-f]{32}", root.name)
    )
    for path in (
        root,
        *(
            root / name
            for name in ("source", "home", "docker-config", "bin", "artifacts")
        ),
    ):
        directory(path)
    source = inputs.source_entries(source_archive, prepared.inputs)
    expected = {
        name: (sha(raw), 0o700 if executable else 0o600)
        for name, raw, executable in source
    }
    require(
        prepared.files
        == tuple(sorted((name, value[0]) for name, value in expected.items()))
    )
    config = regular(root / "frontend.json", 16384, {0o400})
    require(
        tuple(
            sorted(
                inputs.public_configuration(
                    config, prepared.inputs.frontend_configuration_sha256
                ).items()
            )
        )
        == prepared.frontend
    )
    require(len(dict(public_files)) == len(public_files))
    for name, digest in public_files:
        require(
            name.startswith("public/")
            and name not in expected
            and inputs.digest(digest)
        )
        expected[name] = (digest, 0o600)
    expected_directories = {"."}
    for name in expected:
        expected_directories.update(
            str(parent) for parent in PurePosixPath(name).parents
        )
    seen: set[str] = set()
    for parent, directories, files in os.walk(root / "source", followlinks=False):
        parent_path = Path(parent)
        directory(parent_path)
        require(
            parent_path.relative_to(root / "source").as_posix() in expected_directories
        )
        for name in directories:
            directory(parent_path / name)
        for name in files:
            path = parent_path / name
            relative = path.relative_to(root / "source").as_posix()
            require(relative in expected)
            digest, mode = expected[relative]
            require(sha(regular(path, inputs.MAX_SOURCE_BYTES, {mode})) == digest)
            seen.add(relative)
    require(seen == set(expected))


def asset_entries(raw: bytes) -> tuple[tuple[str, bytes], ...]:
    """Read Docker's POSIX tar; no extractall, links, aliases or path rewrites."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_ASSETS)
    entries: list[tuple[str, bytes]] = []
    paths = inputs.SourcePathIndex()
    seen: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        require(0 < len(members) <= 20000)
        for member in members:
            name = inputs.source_member_name(raw, member)
            path = PurePosixPath(name)
            require(name == str(path) and name.isprintable() and len(name) <= 1024)
            require(not path.is_absolute() and path.parts[0] == "client")
            require(not member.linkname and member.mode & 0o7000 == 0)
            key = paths.observe(path, is_file=member.isfile())
            require(key not in seen)
            seen.add(key)
            if member.isdir():
                continue
            require(len(path.parts) > 1 and 0 <= member.size <= 64 * 1024**2)
            total += member.size
            require(total <= MAX_ASSETS)
            stream = archive.extractfile(member)
            require(stream is not None)
            assert stream is not None
            contents = stream.read(member.size + 1)
            require(len(contents) == member.size)
            entries.append((PurePosixPath(*path.parts[1:]).as_posix(), contents))
    require(any(name == "index.html" and contents for name, contents in entries))
    return tuple(sorted(entries))


def image_record(
    raw: bytes, prepared: inputs.PreparedBuild, *, javascript: bool
) -> dict[str, Any]:
    rows = json.loads(raw)
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict)
    value = cast(dict[str, Any], rows[0])
    require(
        type(value.get("Id")) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["Id"])
    )
    require(value.get("Os") == "linux" and value.get("Architecture") == "amd64")
    config = value.get("Config")
    require(type(config) is dict and not config.get("Volumes"))
    config = cast(dict[str, Any], config)
    labels = config.get("Labels")
    require(type(labels) is dict)
    labels = cast(dict[str, Any], labels)
    for name, expected in {
        "org.opencontainers.image.revision": prepared.inputs.candidate_sha,
        "org.opencontainers.image.created": prepared.inputs.created_at,
        "org.opencontainers.image.source": inputs.SOURCE_REPOSITORY,
    }.items():
        require(labels.get(name) == expected)
    environment = config.get("Env")
    require(
        type(environment) is list
        and all(type(v) is str and "=" in v for v in environment)
    )
    pairs = [value.split("=", 1) for value in cast(list[str], environment)]
    require(len({v[0] for v in pairs}) == len(pairs))
    actual = dict(pairs)
    expected_env = dict(inputs.SHARED_ENVIRONMENT)
    expected_env.update(
        BUILD_COMMIT=prepared.inputs.candidate_sha,
        BUILD_CREATED_AT=prepared.inputs.created_at,
    )
    expected_env.update(
        dict(prepared.frontend) if javascript else {"PYTHON_ENV": "staging"}
    )
    if javascript:
        expected_env.update(VITE_BUILD_COMMIT=prepared.inputs.candidate_sha, CI="true")
    require(all(actual.get(k) == v for k, v in expected_env.items()))
    require(
        not any(
            key in actual
            for key in (
                "AZURE_CLIENT_SECRET",
                "AZURE_CLIENT_CERTIFICATE_PATH",
                "OP_SERVICE_ACCOUNT_TOKEN",
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "SENTRY_AUTH_TOKEN",
                "DOCKER_AUTH_CONFIG",
            )
        )
    )
    return value


def archive_binding(path: Path, image_id: str) -> None:
    """Bind Docker-save metadata to the inspected immutable config, no extraction.

    Layer decoding remains Docker-load's job in the separate reviewed publisher;
    this does not claim that a manifest/hash alone verifies executable layers.
    """
    digest = image_id.removeprefix("sha256:")
    config_names = {digest + ".json", "blobs/sha256/" + digest}
    manifest: object = None
    configs: set[str] = set()
    files: set[str] = set()
    count = 0
    with tarfile.open(path, mode="r:") as archive:
        for member in archive:
            count += 1
            require(count <= 10000 and (member.isfile() or member.isdir()))
            name = member.name.removesuffix("/") if member.isdir() else member.name
            value = PurePosixPath(name)
            require(
                name == str(value)
                and not value.is_absolute()
                and ".." not in value.parts
            )
            require(not member.linkname and member.sparse is None)
            if member.isdir():
                continue
            require(name not in files)
            files.add(name)
            if name == "manifest.json" or name in config_names:
                require(0 < member.size <= 4 * 1024**2)
                stream = archive.extractfile(member)
                require(stream is not None)
                assert stream is not None
                raw = stream.read(member.size + 1)
                require(len(raw) == member.size)
                if name == "manifest.json":
                    manifest = json.loads(raw)
                else:
                    require(sha(raw) == digest)
                    configs.add(name)
    require(type(manifest) is list and len(manifest) == 1)
    row = cast(list[Any], manifest)[0]
    require(type(row) is dict and row.get("Config") in configs)
    layers = row.get("Layers")
    require(type(layers) is list and 0 < len(layers) <= 1000)
    layers = cast(list[str], layers)
    require(all(type(name) is str and name in files for name in layers))
    require(len(set(layers)) == len(layers))


class Builder:
    def __init__(
        self,
        prepared: inputs.PreparedBuild,
        source_archive: bytes,
        engine: EngineBinding,
        validate_authority: Callable[[], None],
        deadline: float,
        tick: Callable[[], None],
    ):
        self.prepared, self.source_archive, self.engine = (
            prepared,
            source_archive,
            engine,
        )
        self.validate_authority, self.tick = validate_authority, tick
        self.public_files: tuple[tuple[str, str], ...] = ()
        self.environment = {
            "HOME": str(prepared.root / "home"),
            # Docker's containerd image store otherwise makes a single-platform
            # build plus the default provenance attestation an OCI index. A
            # docker-save archive contains the runnable image config, not that
            # index, so retain one content identity across build and publish.
            "BUILDX_NO_DEFAULT_ATTESTATIONS": "1",
            "DOCKER_CONFIG": str(prepared.root / "docker-config"),
            "DOCKER_HOST": "unix://" + str(SOCKET),
            "PATH": str(prepared.root / "bin") + ":/usr/bin:/bin",
            "TMPDIR": str(prepared.root / "tmp"),
            "LC_ALL": "C",
            "LANG": "C",
        }
        # Every child remains bracketed by deterministic local integrity checks.
        # Live provider authority is checked at the explicit build boundaries
        # below, rather than multiplying five network reads around every local
        # Docker subprocess.
        self.processes = BuildProcesses(deadline, self.local_integrity_check)
        self.phase = "not_started"
        self.container_id: str | None = None
        self.container_removed = False

    def local_integrity_check(self) -> None:
        self.tick()
        tool_check(self.prepared, self.engine)
        source_check(self.prepared, self.source_archive, self.public_files)
        for relative, target in (
            ("bin/docker", DOCKER),
            ("docker-config/cli-plugins/docker-buildx", BUILDX),
        ):
            path = self.prepared.root / relative
            require(path.is_symlink() and os.readlink(path) == str(target))
        self.tick()

    def local_check(self) -> None:
        """Bind live authority to a major phase and then recheck local inputs."""
        self.tick()
        self.validate_authority()
        self.local_integrity_check()

    def command(self, args: list[str], maximum: int = 1024**2) -> bytes:
        chunks: list[bytes] = []
        try:
            self.processes.run(
                [str(DOCKER), *args],
                self.environment,
                maximum,
                chunks.append,
                cwd=self.prepared.root / "source",
            )
        finally:
            if args[:2] == ["container", "create"]:
                # Retain observed ID even if child cleanup/deadline or the next
                # authority check fails. This is recovery evidence, NOT proof
                # of ownership or permission to remove the named container.
                observed = b"".join(chunks).strip()
                if re.fullmatch(rb"[0-9a-f]{64}", observed):
                    self.container_id = observed.decode("ascii")
        self.local_integrity_check()
        return b"".join(chunks)

    def engine_check(self) -> None:
        value = json.loads(self.command(["info", "--format", "{{json .}}"]))
        require(type(value) is dict and type(value.get("ID")) is str)
        require(sha(cast(str, value["ID"]).encode()) == self.engine.id_sha256)
        require(
            value.get("ServerVersion") == "29.5.2" and value.get("OSType") == "linux"
        )
        require(value.get("Architecture") in {"aarch64", "arm64"})
        require(
            self.command(
                ["version", "--format", "{{.Client.Version}}/{{.Server.Version}}"]
            ).strip()
            == b"29.7.2/29.5.2"
        )

    def absent(self, image: str) -> None:
        require(
            not self.command(
                [
                    "image",
                    "ls",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    "reference=" + image,
                ]
            ).strip()
        )

    def image(self, reference: str, *, javascript: bool) -> dict[str, Any]:
        return image_record(
            self.command(["image", "inspect", reference]),
            self.prepared,
            javascript=javascript,
        )

    def container(self, image_id: str) -> None:
        require(self.container_id is not None)
        rows = json.loads(
            self.command(["container", "inspect", cast(str, self.container_id)])
        )
        require(type(rows) is list and len(rows) == 1)
        require(type(rows[0]) is dict)
        value = cast(dict[str, Any], rows[0])
        require(value.get("Id") == self.container_id and value.get("Image") == image_id)
        require(value.get("Name") == "/" + self.prepared.extraction_container)
        require(value.get("State", {}).get("Status") == "created")
        require(
            value["State"].get("Running") is False and value["State"].get("Pid") == 0
        )
        require(
            value.get("Mounts") == []
            and value.get("HostConfig", {}).get("Binds") is None
        )
        require(
            value["HostConfig"].get("ReadonlyRootfs") is True
            and value["HostConfig"].get("NetworkMode") == "none"
        )
        require(
            value.get("Config", {}).get("Labels", {}).get("cosmaneura.build.owner")
            == self.prepared.root.name
        )

    def build_image(self, *, javascript: bool) -> dict[str, Any]:
        self.phase = "javascript_build" if javascript else "application_build"
        self.engine_check()
        image = self.prepared.image + ("-javascript" if javascript else "")
        self.absent(image)
        path = self.prepared.root / "artifacts" / (self.phase + ".log")
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600),
            "wb",
        ) as stream:
            self.processes.run(
                list(inputs.build_arguments(self.prepared, javascript=javascript)),
                self.environment,
                MAX_LOG,
                stream.write,
                cwd=self.prepared.root / "source",
                merge_stderr=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
        self.local_check()
        self.engine_check()
        return self.image(image, javascript=javascript)

    def execute(self) -> BuildArchive:
        self.local_check()
        require(shutil.disk_usage(self.prepared.root).free >= MAX_IMAGE + 4 * 1024**3)
        self.engine_check()
        self.absent(self.prepared.image)
        self.absent(self.prepared.image + "-javascript")
        require(
            not self.command(
                [
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    "name=^/" + self.prepared.extraction_container + "$",
                ]
            ).strip()
        )
        javascript = self.build_image(javascript=True)
        self.phase = "asset_container_create"
        created = (
            self.command(
                [
                    "container",
                    "create",
                    "--name",
                    self.prepared.extraction_container,
                    "--network",
                    "none",
                    "--read-only",
                    "--label",
                    "cosmaneura.build.owner=" + self.prepared.root.name,
                    "--entrypoint",
                    "/bin/true",
                    javascript["Id"],
                ]
            )
            .decode()
            .strip()
        )
        require(re.fullmatch(r"[0-9a-f]{64}", created))
        self.container_id = created
        self.container(javascript["Id"])
        self.phase = "asset_copy"
        raw = self.command(
            ["container", "cp", created + ":/app/build/staging/client", "-"], MAX_ASSETS
        )
        entries = asset_entries(raw)
        public = self.prepared.root / "source/public"
        public.mkdir(mode=0o700)
        published: list[tuple[str, str]] = []
        for name, contents in entries:
            self.tick()
            path = public / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            inputs.write_exclusive(path, contents, 0o600)
            published.append(("public/" + name, sha(contents)))
        self.public_files = tuple(sorted(published))
        self.local_check()
        self.phase = "asset_container_remove"
        self.container(javascript["Id"])
        require(self.command(["container", "rm", created]).decode().strip() == created)
        require(
            not self.command(
                [
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    "id=" + created,
                ]
            ).strip()
        )
        self.container_removed = True
        application = self.build_image(javascript=False)
        self.phase = "image_archive"
        self.engine_check()
        path = self.prepared.root / "artifacts/application-image.tar"
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400),
            "wb",
        ) as stream:
            self.processes.run(
                [str(DOCKER), "image", "save", application["Id"]],
                self.environment,
                MAX_IMAGE,
                stream.write,
                cwd=self.prepared.root / "source",
            )
            stream.flush()
            os.fsync(stream.fileno())
        self.local_check()
        self.engine_check()
        require(
            self.image(application["Id"], javascript=False)["Id"] == application["Id"]
        )
        archive_sha, size = file_sha(path, MAX_IMAGE)
        archive_binding(path, application["Id"])
        require(file_sha(path, MAX_IMAGE) == (archive_sha, size))
        self.tick()
        require(self.processes.child is None and not self.processes.cleanup_failed)
        assets_sha = sha(canonical(self.public_files))
        metadata = self.prepared.root / "artifacts/build-result.json"
        result = {
            "schema_version": 1,
            "candidate_sha": self.prepared.inputs.candidate_sha,
            "source_archive_sha256": self.prepared.inputs.source_archive_sha256,
            "frontend_configuration_sha256": self.prepared.inputs.frontend_configuration_sha256,
            "image_id": application["Id"],
            "archive_sha256": archive_sha,
            "archive_bytes": size,
            "public_assets_sha256": assets_sha,
            "engine_id_sha256": self.engine.id_sha256,
            "local_children_closed": True,
            "owned_extraction_container_removed": self.container_removed,
            "publication_authorized": False,
            "deployment_authorized": False,
        }
        inputs.write_exclusive(metadata, canonical(result) + b"\n", 0o400)
        self.phase = "build_complete"
        return BuildArchive(
            path,
            archive_sha,
            size,
            application["Id"],
            self.prepared.inputs.candidate_sha,
            assets_sha,
            self.prepared.inputs.frontend_configuration_sha256,
            metadata,
        )


def build_candidate(
    prepared: inputs.PreparedBuild,
    source_archive: bytes,
    *,
    engine: EngineBinding,
    validate_authority: Callable[[], None],
    finalize_archive: Callable[[BuildArchive], None],
    seconds: int,
) -> BuildArchive:
    """Called only by an authenticated, reviewed, explicitly authorized controller.

    This library does not authenticate a controller or create execution authority.
    The separate controller binds the founder's explicit actor/source/action scope.
    No registry login/push, Azure, shell, retry, image cleanup or container start.
    On failure preserve all artifacts/images and ambiguous server-side build state.
    A killed Docker client does not prove that a daemon-side build was cancelled.
    """
    require(
        callable(validate_authority)
        and callable(finalize_archive)
        and type(seconds) is int
        and 0 < seconds <= 3600
    )
    builder: Builder | None = None
    try:
        with build_deadline(seconds) as (deadline, tick):
            validate_authority()
            source_check(prepared, source_archive)
            tool_check(prepared, engine)
            for name in ("tmp", "docker-config/cli-plugins"):
                (prepared.root / name).mkdir(mode=0o700)
            os.symlink(DOCKER, prepared.root / "bin/docker")
            os.symlink(
                BUILDX, prepared.root / "docker-config/cli-plugins/docker-buildx"
            )
            builder = Builder(
                prepared, source_archive, engine, validate_authority, deadline, tick
            )
            result = builder.execute()
            tick()
            finalize_archive(result)
            tick()
        return result
    except BaseException:
        refusal = BuildExecutionRefused("staging_build_execution_refused")
        if builder is not None:
            # Evidence only, not an attempt token, permission or cleanup action.
            failure = {
                "phase": builder.phase,
                "build_accepted": False,
                "container_id": builder.container_id,
                "container_name": prepared.extraction_container,
                "container_id_requires_fresh_ownership_verification": True,
                "container_removed": builder.container_removed,
                "daemon_build_state_may_be_unknown": True,
                "automatic_retry_allowed": False,
            }
            try:
                inputs.write_exclusive(
                    prepared.root / "artifacts/build-failure.json",
                    canonical(failure) + b"\n",
                    0o400,
                )
                refusal.failure_evidence_written = True
            except BaseException:
                # A full disk, changed path or interruption cannot replace the
                # canonical refusal. Missing failure evidence is never success.
                pass
        raise refusal from None


if __name__ == "__main__":
    raise SystemExit("authorized_reviewed_controller_required")
