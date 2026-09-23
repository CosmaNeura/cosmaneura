#!/usr/bin/env python3
"""Upload one Docker archive to ACR by digest without creating or changing a tag.

The caller must create the candidate tag separately with Azure ACR import in
NoForce mode. Blob and manifest writes here are content-addressed, so this
transport cannot overwrite a tag or associate a mutable name with an image.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

REGISTRY_HOST = "cosmaneurastagingregistry.azurecr.io"
REGISTRY_REPOSITORY = "cosmaneura/cosmaneura"
CANDIDATE_SHA = "866bd4bc01e0e7be32c59eccdaf9dac3f495c7de"
USERNAME = "00000000-0000-0000-0000-000000000000"
TOKEN_SCOPE = f"repository:{REGISTRY_REPOSITORY}:pull,push"
MAX_ARCHIVE_BYTES = 16 * 1024**3
MAX_METADATA_BYTES = 4 * 1024**2
MAX_LAYERS = 256
MAX_RESPONSE_BYTES = 64 * 1024
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar"
OCI_LAYER_GZIP = "application/vnd.oci.image.layer.v1.tar+gzip"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_CONFIG = "application/vnd.docker.container.image.v1+json"
DOCKER_LAYER_GZIP = "application/vnd.docker.image.rootfs.diff.tar.gzip"


class UploadRefused(RuntimeError):
    """Fixed-message fail-closed refusal."""


def require(condition: object) -> None:
    if not condition:
        raise UploadRefused("staging_acr_untagged_upload_refused")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def json_record(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict)
    return value


def file_identity(value: os.stat_result) -> tuple[int, ...]:
    return tuple(
        getattr(value, name)
        for name in (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_gid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def open_archive(path: Path, expected_sha256: str) -> tuple[BinaryIO, os.stat_result]:
    require(path.is_absolute() and path.resolve() == path)
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stream = os.fdopen(descriptor, "rb")
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        require(before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o400)
        require(0 < before.st_size <= MAX_ARCHIVE_BYTES)
        digest = hashlib.sha256()
        total = 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            require(total <= MAX_ARCHIVE_BYTES)
            digest.update(chunk)
        require(total == before.st_size and digest.hexdigest() == expected_sha256)
        stream.seek(0)
        require(file_identity(os.fstat(descriptor)) == file_identity(before))
        require(file_identity(path.lstat()) == file_identity(before))
        return stream, before
    except BaseException:
        stream.close()
        raise


def read_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, maximum: int
) -> bytes:
    require(member.isfile() and 0 < member.size <= maximum)
    stream = archive.extractfile(member)
    require(stream is not None)
    exact_stream = cast(BinaryIO, stream)
    with exact_stream:
        raw = exact_stream.read(maximum + 1)
    require(len(raw) == member.size and len(raw) <= maximum)
    return raw


def hash_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    require(member.isfile() and 0 < member.size <= MAX_ARCHIVE_BYTES)
    stream = archive.extractfile(member)
    require(stream is not None)
    exact_stream = cast(BinaryIO, stream)
    digest = hashlib.sha256()
    total = 0
    with exact_stream:
        while chunk := exact_stream.read(1024 * 1024):
            total += len(chunk)
            require(total <= member.size)
            digest.update(chunk)
    require(total == member.size)
    return digest.hexdigest()


def hash_gzip_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    """Hash a bounded decompressed layer without materializing it on disk."""
    require(member.isfile() and 0 < member.size <= MAX_ARCHIVE_BYTES)
    stream = archive.extractfile(member)
    require(stream is not None)
    exact_stream = cast(BinaryIO, stream)
    digest = hashlib.sha256()
    total = 0
    try:
        with exact_stream, gzip.GzipFile(fileobj=exact_stream, mode="rb") as expanded:
            while chunk := expanded.read(1024 * 1024):
                total += len(chunk)
                require(total <= MAX_ARCHIVE_BYTES)
                digest.update(chunk)
    except (EOFError, OSError):
        raise UploadRefused("staging_acr_untagged_upload_refused") from None
    require(total > 0)
    return digest.hexdigest()


def safe_member_name(value: object, *, directory: bool = False) -> str:
    require(type(value) is str and 0 < len(value) <= 1024)
    exact = cast(str, value)
    canonical = exact[:-1] if directory and exact.endswith("/") else exact
    path = PurePosixPath(canonical)
    require(not path.is_absolute() and ".." not in path.parts)
    require(canonical == str(path) and exact in {canonical, canonical + "/"})
    return canonical


def exchange_refresh_token(refresh_token: str) -> str:
    require(100 < len(refresh_token) < 128 * 1024)
    body = urlencode(
        {
            "grant_type": "refresh_token",
            "service": REGISTRY_HOST,
            "scope": TOKEN_SCOPE,
            "refresh_token": refresh_token,
        }
    ).encode()
    connection = http.client.HTTPSConnection(
        REGISTRY_HOST, timeout=60, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "POST",
            "/oauth2/token",
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Length": str(len(body)),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        require(response.status == 200 and len(raw) <= MAX_RESPONSE_BYTES)
        value = json_record(raw)
        access_token = value.get("access_token")
        require(type(access_token) is str and 100 < len(access_token) < 128 * 1024)
        return cast(str, access_token)
    finally:
        connection.close()


class Registry:
    def __init__(self, host: str, repository: str, authorization: str):
        require(host == REGISTRY_HOST and repository == REGISTRY_REPOSITORY)
        require(
            authorization.startswith("Bearer ")
            and 100 < len(authorization) < 128 * 1024
        )
        self.host = host
        self.repository = quote(repository, safe="/")
        self.authorization = authorization
        self.context = ssl.create_default_context(
            cafile="/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").exists() else None
        )
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2

    def request(
        self,
        method: str,
        target: str,
        *,
        body: bytes | BinaryIO | None = None,
        length: int = 0,
        content_type: str | None = None,
        maximum_response: int = MAX_RESPONSE_BYTES,
    ) -> tuple[int, dict[str, str], bytes]:
        require(method in {"HEAD", "POST", "PUT"})
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            require(parsed.scheme == "https" and parsed.netloc == self.host)
            path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        else:
            require(target.startswith("/"))
            path = target
        require("\r" not in path and "\n" not in path and len(path) <= 8192)
        headers = {
            "Authorization": self.authorization,
            "Accept": OCI_MANIFEST,
            "Content-Length": str(length),
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        connection = http.client.HTTPSConnection(
            self.host,
            timeout=3600 if method == "PUT" and length > 1024**2 else 60,
            context=self.context,
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(maximum_response + 1)
            require(len(raw) <= maximum_response)
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                raw,
            )
        finally:
            connection.close()

    def blob_exists(self, digest: str) -> bool:
        status, _headers, _raw = self.request(
            "HEAD", f"/v2/{self.repository}/blobs/{digest}"
        )
        require(status in {200, 404})
        return status == 200

    def upload_blob(self, digest: str, body: bytes | BinaryIO, size: int) -> None:
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None)
        require(type(size) is int and 0 < size <= MAX_ARCHIVE_BYTES)
        if self.blob_exists(digest):
            return
        status, headers, _raw = self.request(
            "POST", f"/v2/{self.repository}/blobs/uploads/"
        )
        require(status == 202)
        location = headers.get("location")
        require(type(location) is str)
        parsed = urlsplit(cast(str, location))
        if parsed.scheme or parsed.netloc:
            require(parsed.scheme == "https" and parsed.netloc == self.host)
        require(parsed.path.startswith(f"/v2/{self.repository}/blobs/uploads/"))
        query = parse_qsl(parsed.query, keep_blank_values=True)
        require(all(key != "digest" for key, _value in query))
        query.append(("digest", digest))
        target = parsed.path + "?" + urlencode(query)
        if parsed.scheme:
            target = f"https://{self.host}{target}"
        status, headers, _raw = self.request(
            "PUT",
            target,
            body=body,
            length=size,
            content_type="application/octet-stream",
        )
        require(status == 201)
        observed = headers.get("docker-content-digest")
        require(observed in {None, digest})
        require(self.blob_exists(digest))

    def upload_manifest_by_digest(self, raw: bytes, digest: str) -> None:
        require(digest == "sha256:" + sha(raw))
        target = f"/v2/{self.repository}/manifests/{digest}"
        status, headers, _body = self.request(
            "PUT", target, body=raw, length=len(raw), content_type=OCI_MANIFEST
        )
        require(status in {201, 202})
        require(headers.get("docker-content-digest") == digest)
        status, headers, _body = self.request("HEAD", target)
        require(status == 200 and headers.get("docker-content-digest") == digest)


def build_and_upload(
    archive_path: Path,
    expected_archive_sha256: str,
    expected_image_id: str,
    token_record: dict[str, Any],
    candidate_sha: str = CANDIDATE_SHA,
) -> dict[str, Any]:
    require(type(candidate_sha) is str and re.fullmatch(r"[0-9a-f]{40}", candidate_sha))
    keys = set(token_record)
    required = {"accessToken", "loginServer"}
    require(keys in (required, required | {"refreshToken", "username"}))
    require(token_record["loginServer"] == REGISTRY_HOST)
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_id) is not None)
    refresh_token = token_record["accessToken"]
    require(type(refresh_token) is str and 100 < len(refresh_token) < 128 * 1024)
    if keys != required:
        require(
            token_record["refreshToken"] == refresh_token
            and token_record["username"] == USERNAME
        )
    token_record.clear()
    authorization = ""
    stream, before = open_archive(archive_path, expected_archive_sha256)
    try:
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            members = archive.getmembers()
            require(0 < len(members) <= 2048)
            names: dict[str, tarfile.TarInfo] = {}
            for member in members:
                name = safe_member_name(member.name, directory=member.isdir())
                require(name not in names)
                names[name] = member
            require("manifest.json" in names)
            docker_manifest = json.loads(
                read_member(archive, names["manifest.json"], MAX_METADATA_BYTES),
                object_pairs_hook=unique,
            )
            require(type(docker_manifest) is list and len(docker_manifest) == 1)
            item = docker_manifest[0]
            require(
                type(item) is dict and set(item) == {"Config", "Layers", "RepoTags"}
            )
            exact_item = cast(dict[str, Any], item)
            config_name = safe_member_name(exact_item["Config"])
            require(type(exact_item["Layers"]) is list)
            layer_names = [
                safe_member_name(name)
                for name in cast(list[object], exact_item["Layers"])
            ]
            require(0 < len(layer_names) <= MAX_LAYERS)
            require(len(set(layer_names)) == len(layer_names))
            require(
                config_name in names
                and all(safe_member_name(name) in names for name in layer_names)
            )
            config_raw = read_member(archive, names[config_name], MAX_METADATA_BYTES)
            config = json_record(config_raw)
            require(
                config.get("architecture") == "amd64" and config.get("os") == "linux"
            )
            config_section = config.get("config")
            require(type(config_section) is dict)
            labels = cast(dict[str, Any], config_section).get("Labels")
            require(type(labels) is dict)
            exact_labels = cast(dict[str, Any], labels)
            require(
                exact_labels.get("org.opencontainers.image.revision") == candidate_sha
            )
            require(
                exact_labels.get("org.opencontainers.image.source")
                == "https://github.com/CosmaNeura/cosmaneura"
            )
            rootfs = config.get("rootfs")
            require(type(rootfs) is dict)
            diff_ids = cast(dict[str, Any], rootfs).get("diff_ids")
            require(type(diff_ids) is list and len(diff_ids) == len(layer_names))
            exact_diff_ids = cast(list[object], diff_ids)
            config_digest = "sha256:" + sha(config_raw)
            image_manifest_layers: list[object] | None = None
            if expected_image_id != config_digest:
                image_digest = expected_image_id.removeprefix("sha256:")
                image_manifest_name = "blobs/sha256/" + image_digest
                require(
                    config_name
                    == "blobs/sha256/" + config_digest.removeprefix("sha256:")
                    and image_manifest_name in names
                )
                image_manifest_raw = read_member(
                    archive, names[image_manifest_name], MAX_METADATA_BYTES
                )
                require("sha256:" + sha(image_manifest_raw) == expected_image_id)
                image_manifest = json_record(image_manifest_raw)
                require(
                    set(image_manifest)
                    == {"schemaVersion", "mediaType", "config", "layers"}
                    and image_manifest.get("schemaVersion") == 2
                    and image_manifest.get("mediaType") == DOCKER_MANIFEST
                )
                image_config = image_manifest.get("config")
                require(
                    type(image_config) is dict
                    and set(image_config) == {"digest", "mediaType", "size"}
                    and image_config.get("digest") == config_digest
                    and image_config.get("mediaType") == DOCKER_CONFIG
                    and image_config.get("size") == len(config_raw)
                )
                image_manifest_layers = image_manifest.get("layers")
                require(
                    type(image_manifest_layers) is list
                    and len(image_manifest_layers) == len(layer_names)
                )

            prepared_layers: list[tuple[str, tarfile.TarInfo, str]] = []
            for index, (name, expected_diff_id) in enumerate(
                zip(layer_names, exact_diff_ids, strict=True)
            ):
                member = names[name]
                digest = "sha256:" + hash_member(archive, member)
                if image_manifest_layers is None:
                    require(expected_diff_id == digest)
                    media_type = OCI_LAYER
                else:
                    descriptor = image_manifest_layers[index]
                    require(
                        name == "blobs/sha256/" + digest.removeprefix("sha256:")
                        and type(descriptor) is dict
                        and set(descriptor) == {"digest", "mediaType", "size"}
                        and descriptor.get("digest") == digest
                        and descriptor.get("mediaType") == DOCKER_LAYER_GZIP
                        and descriptor.get("size") == member.size
                        and expected_diff_id
                        == "sha256:" + hash_gzip_member(archive, member)
                    )
                    media_type = OCI_LAYER_GZIP
                prepared_layers.append((digest, member, media_type))

            access_token = exchange_refresh_token(cast(str, refresh_token))
            refresh_token = ""
            authorization = "Bearer " + access_token
            access_token = ""
            registry = Registry(REGISTRY_HOST, REGISTRY_REPOSITORY, authorization)
            registry.upload_blob(config_digest, config_raw, len(config_raw))
            layers = []
            for digest, member, media_type in prepared_layers:
                body = archive.extractfile(member)
                require(body is not None)
                exact_body = cast(BinaryIO, body)
                with exact_body:
                    registry.upload_blob(digest, exact_body, member.size)
                layers.append(
                    {"mediaType": media_type, "digest": digest, "size": member.size}
                )
            manifest = {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "config": {
                    "mediaType": OCI_CONFIG,
                    "digest": config_digest,
                    "size": len(config_raw),
                },
                "layers": layers,
            }
            manifest_raw = json.dumps(
                manifest, sort_keys=True, separators=(",", ":")
            ).encode()
            manifest_digest = "sha256:" + sha(manifest_raw)
            registry.upload_manifest_by_digest(manifest_raw, manifest_digest)
            return {
                "schema_version": 1,
                "archive_sha256": expected_archive_sha256,
                "candidate_sha": candidate_sha,
                "config_digest": config_digest,
                "layer_count": len(layers),
                "manifest_digest": manifest_digest,
                "manifest_media_type": OCI_MANIFEST,
                "registry_host": REGISTRY_HOST,
                "repository": REGISTRY_REPOSITORY,
                "tag_created": False,
                "tag_overwrite_possible": False,
            }
    finally:
        require(file_identity(os.fstat(stream.fileno())) == file_identity(before))
        require(file_identity(archive_path.lstat()) == file_identity(before))
        stream.close()
        refresh_token = ""
        authorization = ""


def main() -> int:
    require(sys.flags.isolated and sys.flags.no_site and not sys.flags.optimize)
    require(os.getuid() == os.geteuid() != 0 and len(sys.argv) in {4, 5})
    candidate_sha = sys.argv[4] if len(sys.argv) == 5 else CANDIDATE_SHA
    require(re.fullmatch(r"[0-9a-f]{40}", candidate_sha))
    token_raw = sys.stdin.buffer.read(256 * 1024 + 1)
    require(0 < len(token_raw) <= 256 * 1024)
    token_record = json.loads(token_raw, object_pairs_hook=unique)
    del token_raw
    require(type(token_record) is dict)
    result = build_and_upload(
        Path(sys.argv[1]), sys.argv[2], sys.argv[3], token_record, candidate_sha
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UploadRefused:
        print("staging ACR untagged upload refused", file=sys.stderr)
        raise SystemExit(2) from None
