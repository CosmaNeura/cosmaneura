"""Synthetic archive bytes only: never extract layers, execute or publish."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import staging_probe_oci_archive as subject  # noqa: E402

sys.path.pop(0)


def encode(value: Any) -> bytes:
    return json.dumps(value, indent=2).encode() + b"\n"


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def descriptor(raw: bytes, media_type: str) -> dict[str, Any]:
    return {"mediaType": media_type, "digest": digest(raw), "size": len(raw)}


def fixture(
    *,
    config_edit: Callable[[dict[str, Any]], None] | None = None,
    manifest_edit: Callable[[dict[str, Any]], None] | None = None,
    index_edit: Callable[[dict[str, Any]], None] | None = None,
    members_edit: Callable[[dict[str, bytes]], None] | None = None,
    special: tarfile.TarInfo | None = None,
    legacy: bool = True,
) -> tuple[bytes, dict[str, str], dict[str, bytes]]:
    # Intentionally not a real gzip layer. This component checks exact encoded
    # bytes, not layer contents or independent image/security acceptance.
    layer = b"synthetic-compressed-layer"
    config: dict[str, Any] = {
        "os": "linux",
        "architecture": "amd64",
        "config": {
            "User": "65532:65532",
            "WorkingDir": "/probe",
            "Entrypoint": list(subject.ENTRYPOINT),
            "Cmd": [],
        },
        "rootfs": {"type": "layers", "diff_ids": [digest(b"synthetic-diff")]},
    }
    if config_edit:
        config_edit(config)
    config_raw = encode(config)
    manifest = {
        "schemaVersion": 2,
        "mediaType": subject.OCI_MANIFEST,
        "config": descriptor(config_raw, subject.OCI_CONFIG),
        "layers": [descriptor(layer, "application/vnd.oci.image.layer.v1.tar+gzip")],
    }
    if manifest_edit:
        manifest_edit(manifest)
    manifest_raw = encode(manifest)
    index = {
        "schemaVersion": 2,
        "mediaType": subject.OCI_INDEX,
        "manifests": [descriptor(manifest_raw, subject.OCI_MANIFEST)],
    }
    if index_edit:
        index_edit(index)
    members = {
        "oci-layout": encode({"imageLayoutVersion": "1.0.0"}),
        "index.json": encode(index),
        "blobs/sha256/" + digest(manifest_raw)[7:]: manifest_raw,
        "blobs/sha256/" + digest(config_raw)[7:]: config_raw,
        "blobs/sha256/" + digest(layer)[7:]: layer,
    }
    if legacy:
        members["manifest.json"] = encode(
            [
                {
                    "Config": "blobs/sha256/" + digest(config_raw)[7:],
                    "RepoTags": None,
                    "Layers": ["blobs/sha256/" + digest(layer)[7:]],
                }
            ]
        )
    if members_edit:
        members_edit(members)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in ["blobs", "blobs/sha256"]:
            directory = tarfile.TarInfo(name)
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
        for name, raw in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        if special:
            archive.addfile(special, io.BytesIO(b"x" * special.size))
    raw = stream.getvalue()
    pins = {
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_digest": digest(manifest_raw),
        "config_digest": digest(config_raw),
    }
    return raw, pins, members


@pytest.mark.parametrize("legacy", [True, False])
def test_complete_closure_preserves_bytes_without_acceptance(legacy: bool) -> None:
    raw, pins, members = fixture(legacy=legacy)
    image = subject.inspect_probe_archive(raw, **pins)
    assert image.manifest.data == members["blobs/sha256/" + pins["manifest_digest"][7:]]
    assert image.config.data == members["blobs/sha256/" + pins["config_digest"][7:]]
    assert image.layers[0].data == b"synthetic-compressed-layer"
    assert b"\n  " in image.manifest.data  # No JSON normalization.
    summary = image.summary()
    assert summary["archive_closure_verified"] is True
    for key in [
        "image_published",
        "image_executed",
        "runtime_acceptance",
        "activation_authorized",
    ]:
        assert summary[key] is False
    assert not {"pass", "published_at", "identity", "receipt"}.intersection(summary)


def test_readback_is_exact_bytes_not_semantic_json() -> None:
    raw, pins, _ = fixture()
    image = subject.inspect_probe_archive(raw, **pins)
    image.manifest.verify_readback(image.manifest.data)
    for changed in [
        json.dumps(json.loads(image.manifest.data)).encode(),
        image.manifest.data + b" ",
        bytearray(image.manifest.data),
    ]:
        with pytest.raises(subject.ProbeArchiveRefused, match="^readback_bytes$"):
            image.manifest.verify_readback(changed)  # type: ignore[arg-type]
    assert "schemaVersion" not in repr(image)
    with pytest.raises(FrozenInstanceError):
        image.manifest.digest = "changed"  # type: ignore[misc]


def test_never_extracts_archive_or_decompresses_layers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, pins, _ = fixture()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("unexpected extraction")

    monkeypatch.setattr(tarfile.TarFile, "extract", forbidden)
    monkeypatch.setattr(tarfile.TarFile, "extractall", forbidden)
    assert subject.inspect_probe_archive(raw, **pins).layers


@pytest.mark.parametrize("key", ["archive_sha256", "manifest_digest", "config_digest"])
@pytest.mark.parametrize(
    "value", [None, "", "SHA256:" + "a" * 64, "a" * 63, "a" * 64 + "\n"]
)
def test_expected_pins_are_not_discovered_from_archive(key: str, value: Any) -> None:
    raw, pins, _ = fixture()
    pins[key] = value
    with pytest.raises(subject.ProbeArchiveRefused, match="^expected_pins$"):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize("key", ["archive_sha256", "manifest_digest", "config_digest"])
def test_wrong_independent_pin(key: str) -> None:
    raw, pins, _ = fixture()
    pins[key] = ("" if key == "archive_sha256" else "sha256:") + "f" * 64
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    "change",
    [lambda x: x[:-1], lambda x: x + b"padding", lambda x: bytes([x[0] ^ 1]) + x[1:]],
)
def test_framing_and_padding_bound_by_archive_hash(
    change: Callable[[bytes], bytes],
) -> None:
    raw, pins, _ = fixture()
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_digest$"):
        subject.inspect_probe_archive(change(raw), **pins)


@pytest.mark.parametrize("raw", [b"", b"not a tar", b"\x00" * 10240])
def test_invalid_tar(raw: bytes) -> None:
    _, pins, _ = fixture()
    pins["archive_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE]
)
def test_links_and_special_nodes_refuse(kind: bytes) -> None:
    member = tarfile.TarInfo("extra")
    member.type = kind
    member.linkname = "/private/synthetic-secret"
    raw, pins, _ = fixture(special=member)
    with pytest.raises(
        subject.ProbeArchiveRefused, match="^archive_member_type_or_size$"
    ) as error:
        subject.inspect_probe_archive(raw, **pins)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "name",
    [
        "index.json",
        "../outside",
        "/absolute",
        "blobs/sha256/" + "A" * 64,
        "blobs/sha256/../outside",
    ],
)
def test_duplicate_or_unsafe_members(name: str) -> None:
    member = tarfile.TarInfo(name)
    member.size = 1
    raw, pins, _ = fixture(special=member)
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


def test_pax_extensions_refuse() -> None:
    member = tarfile.TarInfo("extra")
    member.size = 1
    member.pax_headers = {"comment": "synthetic"}
    raw, pins, _ = fixture(special=member)
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_extensions$"):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    "edit",
    [
        lambda m: m.pop("index.json"),
        lambda m: m.update(
            {
                "oci-layout": b'{"imageLayoutVersion":"1.0.0","imageLayoutVersion":"1.0.0"}'
            }
        ),
        lambda m: m.update({"oci-layout": b'{"imageLayoutVersion":NaN}'}),
        lambda m: m.update({"oci-layout": encode({"imageLayoutVersion": "2.0.0"})}),
        lambda m: m.update({"blobs/sha256/" + digest(b"unused")[7:]: b"unused"}),
        lambda m: m.update(
            {"blobs/sha256/" + digest(b"synthetic-compressed-layer")[7:]: b"corrupted"}
        ),
        lambda m: m.update(
            {
                "manifest.json": encode(
                    [{"Config": "other", "Layers": [], "RepoTags": ["latest"]}]
                )
            }
        ),
    ],
)
def test_members_reject_missing_extra_corrupt_and_ambiguous_json(
    edit: Callable[[dict[str, bytes]], None],
) -> None:
    raw, pins, _ = fixture(members_edit=edit)
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    "edit",
    [
        lambda v: v.update({"schemaVersion": True}),
        lambda v: v.update({"manifests": []}),
        lambda v: v["manifests"].append(v["manifests"][0]),
        lambda v: v["manifests"][0].update({"mediaType": subject.OCI_INDEX}),
        lambda v: v["manifests"][0].update(
            {"annotations": {"org.opencontainers.image.ref.name": "latest"}}
        ),
        lambda v: v["manifests"][0].update({"size": True}),
        lambda v: v["manifests"][0].update({"size": 1}),
    ],
)
def test_one_exact_manifest_descriptor(edit: Callable[[dict[str, Any]], None]) -> None:
    raw, pins, _ = fixture(index_edit=edit)
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    "edit",
    [
        lambda v: v.update({"schemaVersion": True}),
        lambda v: v.update({"subject": {}}),
        lambda v: v.update({"layers": []}),
        lambda v: v["layers"].append(v["layers"][0]),
        lambda v: v["layers"][0].update({"urls": ["https://example.invalid/foreign"]}),
        lambda v: v["layers"][0].update(
            {
                "mediaType": "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip"
            }
        ),
        lambda v: v["layers"][0].update({"size": -1}),
        lambda v: v["layers"][0].update({"size": 1.0}),
        lambda v: v["layers"][0].update({"digest": "sha512:" + "a" * 128}),
    ],
)
def test_no_foreign_layers_or_descriptor_substitution(
    edit: Callable[[dict[str, Any]], None],
) -> None:
    raw, pins, _ = fixture(manifest_edit=edit)
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    "edit",
    [
        lambda v: v.update({"architecture": "arm64"}),
        lambda v: v.update({"os": "windows"}),
        lambda v: v["config"].update({"User": "0"}),
        lambda v: v["config"].update({"WorkingDir": "/"}),
        lambda v: v["config"].update({"Entrypoint": ["sh"]}),
        lambda v: v["config"].update({"Cmd": ["argument"]}),
        lambda v: v["config"].update({"Volumes": {"/data": {}}}),
        lambda v: v["config"].update({"ExposedPorts": {"80/tcp": {}}}),
        lambda v: v["rootfs"].update({"diff_ids": []}),
        lambda v: v["rootfs"].update({"diff_ids": ["incorrect"]}),
    ],
)
def test_only_nonroot_linux_probe_configuration(
    edit: Callable[[dict[str, Any]], None],
) -> None:
    raw, pins, _ = fixture(config_edit=edit)
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    "limit", ["MAX_ARCHIVE_BYTES", "MAX_JSON_BYTES", "MAX_MEMBERS", "MAX_LAYERS"]
)
def test_resource_limits(monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
    raw, pins, _ = fixture()
    monkeypatch.setattr(subject, limit, 0)
    with pytest.raises(subject.ProbeArchiveRefused):
        subject.inspect_probe_archive(raw, **pins)


def repinned(raw: bytes, pins: dict[str, str]) -> dict[str, str]:
    return {**pins, "archive_sha256": hashlib.sha256(raw).hexdigest()}


def archive_end(raw: bytes) -> int:
    offset = 0
    while any(raw[offset : offset + 512]):
        size = int(raw[offset + 124 : offset + 136].strip(b"\x00 "), 8)
        offset = (offset + 512 + size + 511) // 512 * 512
    return offset


@pytest.mark.parametrize("tail", [b"not-zero", b"\x00" * 512 + b"x"])
def test_repinned_nonzero_trailing_bytes_refuse(tail: bytes) -> None:
    raw, pins, _ = fixture()
    changed = raw + tail
    changed += b"\x00" * (-len(changed) % 512)
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_end_marker$"):
        subject.inspect_probe_archive(changed, **repinned(changed, pins))


def test_repinned_concatenated_archive_refuses() -> None:
    raw, pins, _ = fixture()
    changed = raw + raw
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_end_marker$"):
        subject.inspect_probe_archive(changed, **repinned(changed, pins))


@pytest.mark.parametrize("zero_blocks", [0, 1])
def test_repinned_missing_complete_end_markers(zero_blocks: int) -> None:
    raw, pins, _ = fixture()
    changed = raw[: archive_end(raw)] + b"\x00" * (zero_blocks * 512)
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_end_marker$"):
        subject.inspect_probe_archive(changed, **repinned(changed, pins))


def test_explicitly_allowed_zero_padding_remains_valid() -> None:
    raw, pins, _ = fixture()
    changed = raw + b"\x00" * 1024
    assert subject.inspect_probe_archive(changed, **repinned(changed, pins)).manifest


@pytest.mark.parametrize(
    "kind",
    [
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
        tarfile.XHDTYPE,
        tarfile.XGLTYPE,
        tarfile.GNUTYPE_SPARSE,
    ],
)
@pytest.mark.parametrize("count", [1, 1200])
def test_hidden_extensions_refuse_before_tarfile(
    monkeypatch: pytest.MonkeyPatch, kind: bytes, count: int
) -> None:
    raw, pins, _ = fixture()
    extension = tarfile.TarInfo("././@LongLink")
    extension.type = kind
    extension.size = len(b"blobs/\x00")
    header = extension.tobuf(format=tarfile.GNU_FORMAT)
    record = header + b"blobs/\x00" + b"\x00" * (512 - extension.size)
    # A logical parser can consume these before yielding the otherwise allowed
    # first directory. No extension chain may reach tarfile recursion at all.
    changed = record * count + raw

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("hidden record reached logical tar parser")

    monkeypatch.setattr(subject.tarfile, "open", forbidden)
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_extensions$"):
        subject.inspect_probe_archive(changed, **repinned(changed, pins))


def test_repinned_nonzero_member_padding_refuses() -> None:
    raw, pins, _ = fixture()
    offset = 0
    while True:
        size = int(raw[offset + 124 : offset + 136].strip(b"\x00 "), 8)
        if size % 512:
            position = offset + 512 + size
            break
        offset += 512 + size
    changed = raw[:position] + b"x" + raw[position + 1 :]
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_payload_padding$"):
        subject.inspect_probe_archive(changed, **repinned(changed, pins))


def test_logical_parser_recursion_is_fixed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, pins, _ = fixture()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RecursionError("synthetic-private-parser-detail")

    monkeypatch.setattr(subject.tarfile, "open", fail)
    with pytest.raises(subject.ProbeArchiveRefused, match="^archive_invalid$"):
        subject.inspect_probe_archive(raw, **pins)


@pytest.mark.parametrize(
    ("offset", "replacement", "code"),
    [
        (257, b"badmagic", "archive_ustar_header"),
        (345, b"prefix", "archive_ustar_header"),
        (90, b"hidden", "archive_name_field"),
        (124, b"\x0000000000001", "archive_numeric_field"),
        (148, b"0000000\x00", "archive_checksum"),
    ],
)
def test_repinned_header_ambiguity_refuses(
    offset: int, replacement: bytes, code: str
) -> None:
    raw, pins, _ = fixture()
    header = bytearray(raw[:512])
    header[offset : offset + len(replacement)] = replacement
    if offset != 148:
        header[148:156] = b"        "
        header[148:156] = f"{sum(header):06o}\x00 ".encode()
    changed = bytes(header) + raw[512:]
    with pytest.raises(subject.ProbeArchiveRefused, match="^" + code + "$"):
        subject.inspect_probe_archive(changed, **repinned(changed, pins))
