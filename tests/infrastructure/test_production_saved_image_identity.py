"""Actual save formats and adversarial bytes; no Docker/client/application calls."""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import tarfile
import zlib

import pytest

from scripts import production_saved_image_identity as identity

OCI = "application/vnd.oci.image."


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def fixture(runtime=None, *, engine="containerd"):
    runtime = runtime or {
        "Labels": {"org.opencontainers.image.revision": "a" * 40},
        "Env": ["PYTHON_ENV=production"],
    }
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w:") as tar:
        entry = tarfile.TarInfo("app/synthetic.txt")
        entry.size = 17
        tar.addfile(entry, io.BytesIO(b"synthetic source\n"))
    expanded = layer.getvalue()
    rootfs = {"type": "layers", "diff_ids": [sha(expanded)]}
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": runtime,
        "rootfs": rootfs,
    }
    config_raw = encoded(config)
    config_digest = sha(config_raw)
    compressed = gzip.compress(expanded, mtime=0)
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI + "manifest.v1+json",
        "config": {
            "mediaType": OCI + "config.v1+json",
            "digest": config_digest,
            "size": len(config_raw),
        },
        "layers": [
            {
                "mediaType": OCI + "layer.v1.tar+gzip",
                "digest": sha(compressed),
                "size": len(compressed),
            }
        ],
    }
    manifest_raw = encoded(manifest)
    manifest_digest = sha(manifest_raw)
    inspected = {
        "Id": config_digest,
        "Os": "linux",
        "Architecture": "amd64",
        "Config": copy.deepcopy(runtime),
        "RootFS": {"Type": "layers", "Layers": rootfs["diff_ids"]},
    }
    if engine == "classic":
        config_name = config_digest[7:] + ".json"
        layer_name = "layer/layer.tar"
        entries = {config_name: config_raw, layer_name: expanded}
    else:
        inspected["Id"] = manifest_digest
        inspected["Descriptor"] = {
            "digest": manifest_digest,
            "mediaType": manifest["mediaType"],
            "size": len(manifest_raw),
        }
        config_name = "blobs/sha256/" + config_digest[7:]
        layer_name = "blobs/sha256/" + sha(compressed)[7:]
        entries = {
            "oci-layout": encoded({"imageLayoutVersion": "1.0.0"}),
            "index.json": encoded(
                {
                    "schemaVersion": 2,
                    "mediaType": OCI + "index.v1+json",
                    "manifests": [inspected["Descriptor"]],
                }
            ),
            "blobs/sha256/" + manifest_digest[7:]: manifest_raw,
            config_name: config_raw,
            layer_name: compressed,
        }
    entries["manifest.json"] = encoded(
        [
            {
                "Config": config_name,
                "Layers": [layer_name],
                "RepoTags": ["synthetic:test"],
            }
        ]
    )
    return inspected, entries, manifest_raw


def save(path, entries, *, duplicate=None, link=False):
    with tarfile.open(path, mode="w:") as tar:
        for name, raw in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            tar.addfile(entry, io.BytesIO(raw))
        if duplicate:
            raw = entries[duplicate]
            entry = tarfile.TarInfo(duplicate)
            entry.size = len(raw)
            tar.addfile(entry, io.BytesIO(raw))
        if link:
            entry = tarfile.TarInfo("unsafe")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/outside"
            tar.addfile(entry)
    return path


@pytest.mark.parametrize("engine", ["classic", "containerd"])
def test_config_digest_comes_from_actual_bytes_on_both_engines(tmp_path, engine):
    inspected, entries, remote = fixture(engine=engine)
    verified = identity.saved_image_identity(
        save(tmp_path / "image.tar", entries), inspected
    )
    manifest = json.loads(remote)
    assert verified["config_digest"] == manifest["config"]["digest"]
    assert (inspected["Id"] == verified["config_digest"]) == (engine == "classic")
    identity.published_manifest_identity(remote, sha(remote), verified)
    identity.published_manifest_identity(remote + b"\n", sha(remote), verified)


@pytest.mark.parametrize(
    "field",
    [
        "Id",
        "Descriptor",
        "descriptor_platform",
        "Architecture",
        "Os",
        "Config",
        "RootFS",
    ],
)
def test_inspect_metadata_cannot_substitute_or_misbind_saved_bytes(tmp_path, field):
    inspected, entries, _remote = fixture()
    if field == "Id":
        inspected[field] = "sha256:" + "f" * 64
    elif field == "Descriptor":
        inspected[field]["digest"] = "sha256:" + "f" * 64
    elif field == "descriptor_platform":
        inspected["Descriptor"]["platform"] = {"os": "linux", "architecture": "arm64"}
    elif field == "Architecture":
        inspected[field] = "arm64"
    elif field == "Os":
        inspected[field] = "windows"
    elif field == "Config":
        inspected[field]["Env"] = ["PYTHON_ENV=staging"]
    else:
        inspected[field]["Layers"] = ["sha256:" + "f" * 64]
    with pytest.raises(identity.ImageIdentityRefused):
        identity.saved_image_identity(save(tmp_path / "image.tar", entries), inspected)


@pytest.mark.parametrize("engine", ["classic", "containerd"])
@pytest.mark.parametrize(
    "kind",
    [
        "missing_config",
        "changed_config",
        "changed_layer",
        "duplicate",
        "link",
        "escape",
        "ambiguous_catalogue",
    ],
)
def test_corrupt_and_ambiguous_archives_refuse(tmp_path, engine, kind):
    inspected, entries, _remote = fixture(engine=engine)
    catalogue = json.loads(entries["manifest.json"])
    config_name = catalogue[0]["Config"]
    duplicate = None
    if kind == "missing_config":
        del entries[config_name]
    elif kind == "changed_config":
        entries[config_name] = entries[config_name].replace(
            b"production", b"stagingxxx"
        )
    elif kind == "changed_layer":
        entries[catalogue[0]["Layers"][0]] = b"corrupt layer"
    elif kind == "duplicate":
        duplicate = "manifest.json"
    elif kind == "escape":
        entries["../outside"] = b"unsafe"
    elif kind == "ambiguous_catalogue":
        entries["manifest.json"] = encoded(catalogue + catalogue)
    with pytest.raises(identity.ImageIdentityRefused):
        identity.saved_image_identity(
            save(
                tmp_path / "image.tar",
                entries,
                duplicate=duplicate,
                link=kind == "link",
            ),
            inspected,
        )


@pytest.mark.parametrize(
    "kind",
    [
        "ambiguous_index",
        "wrong_index_size",
        "wrong_config_size",
        "wrong_layer_digest",
        "wrong_platform",
        "malformed_index",
        "duplicate_json",
    ],
)
def test_native_descriptor_and_manifest_corruption_refuse(tmp_path, kind):
    inspected, entries, _remote = fixture()
    index = json.loads(entries["index.json"])
    if kind == "ambiguous_index":
        index["manifests"] *= 2
    elif kind == "wrong_index_size":
        index["manifests"][0]["size"] += 1
    elif kind == "wrong_platform":
        index["manifests"][0]["platform"] = {"os": "linux", "architecture": "arm64"}
    elif kind == "malformed_index":
        index = []
    elif kind == "duplicate_json":
        entries["index.json"] = b'{"schemaVersion":2,"schemaVersion":2}'
    else:
        selected = index["manifests"][0]
        old_name = "blobs/sha256/" + selected["digest"][7:]
        manifest = json.loads(entries.pop(old_name))
        if kind == "wrong_config_size":
            manifest["config"]["size"] += 1
        else:
            manifest["layers"][0]["digest"] = "sha256:" + "f" * 64
        raw = encoded(manifest)
        selected.update(digest=sha(raw), size=len(raw))
        inspected["Id"] = selected["digest"]
        inspected["Descriptor"] = copy.deepcopy(selected)
        entries["blobs/sha256/" + selected["digest"][7:]] = raw
    if kind != "duplicate_json":
        entries["index.json"] = encoded(index)
    with pytest.raises(identity.ImageIdentityRefused):
        identity.saved_image_identity(save(tmp_path / "image.tar", entries), inspected)


@pytest.mark.parametrize(
    "kind",
    [
        "config_digest",
        "config_size",
        "layer_digest",
        "layer_size",
        "layer_count",
        "bad_media_type",
        "wrong_compression",
    ],
)
def test_remote_manifest_must_keep_verified_config_and_native_layer_bytes(
    tmp_path, kind
):
    inspected, entries, raw = fixture()
    verified = identity.saved_image_identity(
        save(tmp_path / "image.tar", entries), inspected
    )
    manifest = json.loads(raw)
    if kind == "config_digest":
        manifest["config"]["digest"] = inspected["Id"]
    elif kind == "config_size":
        manifest["config"]["size"] += 1
    elif kind == "layer_digest":
        manifest["layers"][0]["digest"] = "sha256:" + "f" * 64
    elif kind == "layer_size":
        manifest["layers"][0]["size"] += 1
    elif kind == "layer_count":
        manifest["layers"] *= 2
    elif kind == "wrong_compression":
        manifest["layers"][0]["mediaType"] = OCI + "layer.v1.tar"
    else:
        manifest["layers"][0]["mediaType"] = "untrusted"
    changed = encoded(manifest)
    with pytest.raises(identity.ImageIdentityRefused):
        identity.published_manifest_identity(changed, sha(changed), verified)


@pytest.mark.parametrize(
    "kind", ["wrong_expanded_bytes", "truncated_gzip", "malformed_deflate"]
)
def test_self_consistent_compressed_blob_must_still_match_rootfs(tmp_path, kind):
    inspected, entries, _remote = fixture()
    index = json.loads(entries["index.json"])
    chosen = index["manifests"][0]
    manifest_name = "blobs/sha256/" + chosen["digest"][7:]
    manifest = json.loads(entries.pop(manifest_name))
    old_layer = "blobs/sha256/" + manifest["layers"][0]["digest"][7:]
    previous = entries.pop(old_layer)
    if kind == "malformed_deflate":
        # Valid gzip header/trailer, but DEFLATE BTYPE=3 is reserved. Bind its
        # actual compressed bytes into every descriptor so hashing cannot mask
        # the decoder failure. Verify the fixture reaches zlib.error itself.
        changed = bytes.fromhex("1f8b08000000000002ff07") + b"\x00" * 8
        with pytest.raises(zlib.error):
            gzip.decompress(changed)
    elif kind == "wrong_expanded_bytes":
        changed = gzip.compress(b"wrong expanded layer", mtime=0)
    else:
        changed = previous[:-8]
    manifest["layers"][0].update(digest=sha(changed), size=len(changed))
    new_layer = "blobs/sha256/" + sha(changed)[7:]
    entries[new_layer] = changed
    catalogue = json.loads(entries["manifest.json"])
    catalogue[0]["Layers"] = [new_layer]
    entries["manifest.json"] = encoded(catalogue)
    raw = encoded(manifest)
    chosen.update(digest=sha(raw), size=len(raw))
    inspected["Id"] = chosen["digest"]
    inspected["Descriptor"] = copy.deepcopy(chosen)
    entries["index.json"] = encoded(index)
    entries["blobs/sha256/" + sha(raw)[7:]] = raw
    with pytest.raises(
        identity.ImageIdentityRefused, match="^production_saved_image_identity_refused$"
    ) as refused:
        identity.saved_image_identity(save(tmp_path / "image.tar", entries), inspected)
    assert refused.value.__cause__ is None and refused.value.__suppress_context__
