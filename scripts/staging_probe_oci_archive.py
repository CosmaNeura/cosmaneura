"""Read-only, exact-byte OCI input validation for the synthetic runtime probe.

No extraction, decompression, execution, network, credentials or publication.
The expected pins must come from the independently reviewed image, not from
the archive being inspected. A valid archive is not execution authority.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any, NoReturn

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024
MAX_MEMBERS = 24
MAX_LAYERS = 12
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
LAYER_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.docker.image.rootfs.diff.tar.gzip",
    }
)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
ENTRYPOINT = (
    "/usr/local/bin/python3",
    "-I",
    "-S",
    "-B",
    "/probe/staging_runtime_capability_probe.py",
)


class ProbeArchiveRefused(ValueError):
    """A fixed code, never untrusted archive content or a path."""


def _fail(code: str) -> NoReturn:
    raise ProbeArchiveRefused(code)


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _json(raw: bytes) -> Any:
    if not 0 < len(raw) <= MAX_JSON_BYTES:
        _fail("json_size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail("json_duplicate")
            result[key] = value
        return result

    def nonfinite(_value: str) -> NoReturn:
        _fail("json_number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    except ProbeArchiveRefused:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ProbeArchiveRefused("json_invalid") from None


def _object(value: Any, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail("object_fields")
    return value


@dataclass(frozen=True, slots=True)
class Blob:
    digest: str
    media_type: str
    data: bytes = field(repr=False)

    def verify_readback(self, raw: bytes) -> None:
        """Verify bytes, not a server's digest header or a reconstructed JSON."""
        if type(raw) is not bytes or raw != self.data:
            _fail("readback_bytes")


@dataclass(frozen=True, slots=True)
class ProbeImage:
    archive_sha256: str
    manifest: Blob
    config: Blob
    layers: tuple[Blob, ...]

    def summary(self) -> dict[str, Any]:
        """A diagnostic inventory, deliberately not a publication receipt."""
        return {
            "archive_sha256": self.archive_sha256,
            "manifest_digest": self.manifest.digest,
            "config_digest": self.config.digest,
            "layer_digests": [layer.digest for layer in self.layers],
            "compressed_layer_bytes": sum(len(layer.data) for layer in self.layers),
            "archive_closure_verified": True,
            "image_executed": False,
            "image_published": False,
            "runtime_acceptance": False,
            "activation_authorized": False,
        }


def _archive_members(raw: bytes) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                if len(seen) >= MAX_MEMBERS or member.name in seen:
                    _fail("archive_members")
                seen.add(member.name)
                if member.pax_headers or member.issparse():
                    _fail("archive_extensions")
                if member.isdir():
                    if member.name not in {"blobs", "blobs/sha256"}:
                        _fail("archive_directory")
                    continue
                if not member.isfile() or not 0 < member.size <= MAX_ARCHIVE_BYTES:
                    _fail("archive_member_type_or_size")
                if member.name not in {"index.json", "oci-layout", "manifest.json"}:
                    prefix = "blobs/sha256/"
                    if not member.name.startswith(prefix) or not HEX.fullmatch(
                        member.name[len(prefix) :]
                    ):
                        _fail("archive_member_name")
                total += member.size
                if total > MAX_ARCHIVE_BYTES:
                    _fail("archive_expanded_size")
                source = archive.extractfile(member)
                if source is None:
                    _fail("archive_member_missing")
                with source:
                    data = source.read(member.size + 1)
                if len(data) != member.size:
                    _fail("archive_member_truncated")
                result[member.name] = data
    except ProbeArchiveRefused:
        raise
    except (tarfile.TarError, OSError, ValueError, OverflowError):
        raise ProbeArchiveRefused("archive_invalid") from None
    return result


def inspect_probe_archive(
    raw: bytes,
    *,
    archive_sha256: str,
    manifest_digest: str,
    config_digest: str,
) -> ProbeImage:
    """Validate the complete selected OCI closure without unpacking its layers.

    The caller supplies three independently reviewed pins. The tar hash binds
    framing/padding as well as content. Only the one linux/amd64 nonroot probe
    image is allowed; indexes, foreign layers, tags and extra blobs refuse.
    Layer *compressed* bytes are verified here. Layer contents, diff_ids and
    supply-chain/runtime properties still require the separate image review.
    """
    if (
        type(archive_sha256) is not str
        or not HEX.fullmatch(archive_sha256)
        or type(manifest_digest) is not str
        or not DIGEST.fullmatch(manifest_digest)
        or type(config_digest) is not str
        or not DIGEST.fullmatch(config_digest)
    ):
        _fail("expected_pins")
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_ARCHIVE_BYTES:
        _fail("archive_size")
    if hashlib.sha256(raw).hexdigest() != archive_sha256:
        _fail("archive_digest")
    members = _archive_members(raw)
    used = {"index.json", "oci-layout"}

    def read(name: str) -> bytes:
        if name not in members:
            _fail("archive_member_missing")
        return members[name]

    def blob(descriptor: Any, media_types: set[str] | frozenset[str]) -> Blob:
        desc = _object(descriptor, {"digest", "mediaType", "size"})
        digest = desc["digest"]
        size = desc["size"]
        if (
            type(digest) is not str
            or not DIGEST.fullmatch(digest)
            or type(size) is not int
            or not 0 < size <= MAX_ARCHIVE_BYTES
            or type(desc["mediaType"]) is not str
            or desc["mediaType"] not in media_types
        ):
            _fail("descriptor")
        name = "blobs/sha256/" + digest[7:]
        if name in used:
            _fail("descriptor_duplicate")
        used.add(name)
        data = read(name)
        if len(data) != size or _digest(data) != digest:
            _fail("blob_digest_or_size")
        return Blob(digest, desc["mediaType"], data)

    layout = _object(_json(read("oci-layout")), {"imageLayoutVersion"})
    if layout["imageLayoutVersion"] != "1.0.0":
        _fail("layout_version")
    index = _object(
        _json(read("index.json")), {"schemaVersion", "mediaType", "manifests"}
    )
    if (
        type(index["schemaVersion"]) is not int
        or index["schemaVersion"] != 2
        or index["mediaType"] != OCI_INDEX
        or type(index["manifests"]) is not list
        or len(index["manifests"]) != 1
    ):
        _fail("index")
    manifest = blob(index["manifests"][0], {OCI_MANIFEST})
    if manifest.digest != manifest_digest:
        _fail("manifest_pin")
    value = _object(
        _json(manifest.data), {"schemaVersion", "mediaType", "config", "layers"}
    )
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 2
        or value["mediaType"] != manifest.media_type
        or type(value["layers"]) is not list
        or not 1 <= len(value["layers"]) <= MAX_LAYERS
    ):
        _fail("manifest")
    config = blob(value["config"], {OCI_CONFIG})
    if config.digest != config_digest:
        _fail("config_pin")
    layers = tuple(blob(item, LAYER_TYPES) for item in value["layers"])
    configuration = _json(config.data)
    if type(configuration) is not dict:
        _fail("configuration")
    runtime = configuration.get("config")
    if (
        configuration.get("os") != "linux"
        or configuration.get("architecture") != "amd64"
        or type(runtime) is not dict
        or runtime.get("User") != "65532:65532"
        or runtime.get("WorkingDir") != "/probe"
        or runtime.get("Entrypoint") != list(ENTRYPOINT)
        or runtime.get("Cmd") not in (None, [])
        or runtime.get("Volumes") not in (None, {})
        or runtime.get("ExposedPorts") not in (None, {})
    ):
        _fail("probe_configuration")
    rootfs = _object(configuration.get("rootfs"), {"type", "diff_ids"})
    if (
        rootfs["type"] != "layers"
        or type(rootfs["diff_ids"]) is not list
        or len(rootfs["diff_ids"]) != len(layers)
        or any(
            type(item) is not str or not DIGEST.fullmatch(item)
            for item in rootfs["diff_ids"]
        )
    ):
        _fail("rootfs")
    if "manifest.json" in members:
        used.add("manifest.json")
        legacy = _json(members["manifest.json"])
        if type(legacy) is not list or len(legacy) != 1:
            _fail("legacy_manifest")
        entry = _object(legacy[0], {"Config", "RepoTags", "Layers"})
        if (
            entry["Config"] != "blobs/sha256/" + config.digest[7:]
            or entry["RepoTags"] is not None
            or entry["Layers"]
            != ["blobs/sha256/" + layer.digest[7:] for layer in layers]
        ):
            _fail("legacy_manifest")
    if set(members) != used:
        _fail("archive_unreferenced_content")
    return ProbeImage(archive_sha256, manifest, config, layers)
