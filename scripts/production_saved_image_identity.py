"""Byte-backed identity for canonical Linux/AMD64 Docker save exports.

Docker's Id may identify either a config or a manifest. It never establishes
config identity without the saved bytes. No client, application or cloud calls.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
from pathlib import Path
from typing import IO, Any, Protocol, cast

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
CONFIG_TYPES = {
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
GZIP_TYPES = {
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
}
TAR_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.docker.image.rootfs.diff.tar",
}
MAX_JSON = 1024 * 1024
MAX_ARCHIVE = 32 * 1024**3
MAX_LAYER = 16 * 1024**3


class ImageIdentityRefused(ValueError):
    """Value-free refusal of ambiguous, corrupt or mismatched image evidence."""


def require(value: Any) -> None:
    if not value:
        raise ImageIdentityRefused("production_saved_image_identity_refused")


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def decode(raw: bytes) -> Any:
    require(0 < len(raw) <= MAX_JSON)

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    def invalid(_value: str) -> None:
        raise ImageIdentityRefused("production_saved_image_identity_refused")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def object_record(value: Any) -> dict[str, Any]:
    require(type(value) is dict)
    return cast(dict[str, Any], value)


def array(value: Any) -> list[Any]:
    require(type(value) is list)
    return cast(list[Any], value)


def member_stream(archive: tarfile.TarFile, member: tarfile.TarInfo) -> IO[bytes]:
    stream = archive.extractfile(member)
    if stream is None:
        raise ImageIdentityRefused("production_saved_image_identity_refused")
    return stream


def path_name(value: Any, directory: bool = False) -> str:
    require(type(value) is str and value and len(value) <= 1024)
    if directory:
        value = value.removesuffix("/")
    require(
        not value.startswith("/")
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )
    return value


class ByteReader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


def hash_stream(stream: ByteReader) -> str:
    result, size = hashlib.sha256(), 0
    while chunk := stream.read(4 * 1024 * 1024):
        size += len(chunk)
        require(size <= MAX_LAYER)
        result.update(chunk)
    return "sha256:" + result.hexdigest()


def descriptor(value: Any, types: set[str]) -> dict[str, Any]:
    require(type(value) is dict)
    require(type(value.get("digest")) is str and DIGEST.fullmatch(value["digest"]))
    require(type(value.get("size")) is int and 0 < value["size"] <= MAX_LAYER)
    require(value.get("mediaType") in types)
    return value


def saved_image_identity(archive_path: Path, inspected: Any) -> dict[str, Any]:
    """Select one catalogued image and verify config, manifest and every layer."""
    try:
        return _saved_image_identity(archive_path, inspected)
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        EOFError,
        RecursionError,
        tarfile.TarError,
    ):
        raise ImageIdentityRefused("production_saved_image_identity_refused") from None


def _saved_image_identity(archive_path: Path, inspected: Any) -> dict[str, Any]:
    require(type(inspected) is dict and DIGEST.fullmatch(inspected.get("Id", "")))
    require(inspected.get("Os") == "linux" and inspected.get("Architecture") == "amd64")
    fd = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_ARCHIVE)
        with tarfile.open(fileobj=source, mode="r:") as archive:
            members: dict[str, tarfile.TarInfo] = {}
            for member in archive:
                require(len(members) < 10000 and (member.isfile() or member.isdir()))
                name = path_name(member.name, member.isdir())
                require(name not in members and 0 <= member.size <= MAX_LAYER)
                members[name] = member

            def raw(name: str) -> bytes:
                member = members[name]
                require(member.isfile() and 0 < member.size <= MAX_JSON)
                stream = member_stream(archive, member)
                with stream:
                    result = stream.read(MAX_JSON + 1)
                require(len(result) == member.size)
                return result

            def blob(item: dict[str, Any]) -> tuple[str, tarfile.TarInfo]:
                name = "blobs/sha256/" + item["digest"][7:]
                member = members[name]
                require(member.isfile() and member.size == item["size"])
                stream = member_stream(archive, member)
                with stream:
                    require(hash_stream(stream) == item["digest"])
                return name, member

            catalogue = array(decode(raw("manifest.json")))
            require(type(catalogue) is list and len(catalogue) == 1)
            selected = object_record(catalogue[0])
            require(
                type(selected) is dict
                and set(selected) == {"Config", "Layers", "RepoTags"}
            )
            config_name = path_name(selected["Config"])
            config_raw = raw(config_name)
            config_digest = digest(config_raw)
            config = object_record(decode(config_raw))
            require(
                type(config) is dict
                and config.get("os") == "linux"
                and config.get("architecture") == "amd64"
            )
            require(
                config.get("config") == inspected.get("Config")
                and type(config.get("config")) is dict
            )
            rootfs = object_record(config.get("rootfs"))
            require(type(rootfs) is dict and rootfs.get("type") == "layers")
            diff_ids = array(rootfs.get("diff_ids"))
            require(type(diff_ids) is list and 0 < len(diff_ids) <= 128)
            require(
                all(type(item) is str and DIGEST.fullmatch(item) for item in diff_ids)
            )
            require(inspected.get("RootFS") == {"Type": "layers", "Layers": diff_ids})
            layer_names = array(selected["Layers"])
            require(type(layer_names) is list and len(layer_names) == len(diff_ids))
            layer_names = [path_name(name) for name in layer_names]
            require(len(set(layer_names)) == len(layer_names))
            layers: list[dict[str, Any]] | None = None
            local_manifest_digest = None
            if "index.json" in members:
                require(decode(raw("oci-layout")) == {"imageLayoutVersion": "1.0.0"})
                index = object_record(decode(raw("index.json")))
                require(type(index) is dict and index.get("schemaVersion") == 2)
                require(
                    index.get("mediaType")
                    in {None, "application/vnd.oci.image.index.v1+json"}
                )
                items = array(index.get("manifests"))
                require(type(items) is list and len(items) == 1)
                chosen = descriptor(items[0], MANIFEST_TYPES)
                require(
                    chosen.get("platform")
                    in (None, {"os": "linux", "architecture": "amd64"})
                )
                manifest_name, _member = blob(chosen)
                manifest = object_record(decode(raw(manifest_name)))
                require(
                    type(manifest) is dict
                    and manifest.get("schemaVersion") == 2
                    and manifest.get("mediaType") == chosen["mediaType"]
                )
                stored_config = descriptor(manifest.get("config"), CONFIG_TYPES)
                require(
                    stored_config["digest"] == config_digest
                    and stored_config["size"] == len(config_raw)
                )
                require(blob(stored_config)[0] == config_name)
                layers = array(manifest.get("layers"))
                require(type(layers) is list and len(layers) == len(diff_ids))
                require(
                    [
                        blob(descriptor(item, GZIP_TYPES | TAR_TYPES))[0]
                        for item in layers
                    ]
                    == layer_names
                )
                local_manifest_digest = chosen["digest"]
                observed: Any = inspected.get("Descriptor")
                if inspected["Id"] != config_digest or observed is not None:
                    observed = object_record(observed)
                    require(
                        observed.get("platform")
                        in (None, {"os": "linux", "architecture": "amd64"})
                    )
                    require(
                        all(
                            observed.get(key) == chosen[key]
                            for key in ("digest", "mediaType", "size")
                        )
                    )
                require(inspected["Id"] in {config_digest, local_manifest_digest})
            else:
                require(
                    inspected["Id"] == config_digest
                    and inspected.get("Descriptor") is None
                )
                require(
                    config_name
                    in {
                        config_digest[7:] + ".json",
                        "blobs/sha256/" + config_digest[7:],
                    }
                )
            for index, (name, expected) in enumerate(
                zip(layer_names, diff_ids, strict=True)
            ):
                member = members[name]
                require(member.isfile())
                stream = member_stream(archive, member)
                with stream:
                    if layers is not None and layers[index]["mediaType"] in GZIP_TYPES:
                        with gzip.GzipFile(fileobj=stream) as expanded:
                            require(hash_stream(expanded) == expected)
                    else:
                        require(hash_stream(stream) == expected)
        after = os.fstat(source.fileno())
        require(
            (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        )
    return {
        "config_digest": config_digest,
        "config_size": len(config_raw),
        "diff_ids": diff_ids,
        "layers": layers,
        "local_manifest_digest": local_manifest_digest,
    }


def published_manifest_identity(
    raw: bytes, expected: str, identity: dict[str, Any]
) -> None:
    """Bind the remote immutable manifest to the verified saved config/layers."""
    try:
        require(type(expected) is str and DIGEST.fullmatch(expected))
        if digest(raw) != expected and raw.endswith(b"\n"):
            raw = raw[:-1]
        require(digest(raw) == expected)
        manifest = object_record(decode(raw))
        require(
            type(manifest) is dict
            and manifest.get("schemaVersion") == 2
            and manifest.get("mediaType") in MANIFEST_TYPES
        )
        config = descriptor(manifest.get("config"), CONFIG_TYPES)
        require(
            config["digest"] == identity["config_digest"]
            and config["size"] == identity["config_size"]
        )
        layers = array(manifest.get("layers"))
        require(type(layers) is list and len(layers) == len(identity["diff_ids"]))
        for item in layers:
            descriptor(item, GZIP_TYPES | TAR_TYPES)
        if identity["layers"] is not None:
            require(
                [
                    (item["digest"], item["size"], item["mediaType"] in GZIP_TYPES)
                    for item in layers
                ]
                == [
                    (item["digest"], item["size"], item["mediaType"] in GZIP_TYPES)
                    for item in identity["layers"]
                ]
            )
    except (ValueError, KeyError, TypeError, RecursionError):
        raise ImageIdentityRefused("production_saved_image_identity_refused") from None
