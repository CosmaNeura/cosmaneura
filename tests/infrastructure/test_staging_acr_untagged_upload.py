from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "staging_acr_untagged_upload_test_subject",
    ROOT / "scripts/staging_acr_untagged_upload.py",
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class FakeRegistry:
    instances: ClassVar[list[FakeRegistry]] = []

    def __init__(self, host: str, repository: str, authorization: str):
        assert host == m.REGISTRY_HOST
        assert repository == m.REGISTRY_REPOSITORY
        assert authorization.startswith("Basic ")
        self.blobs: list[tuple[str, bytes]] = []
        self.manifests: list[tuple[str, bytes]] = []
        self.instances.append(self)

    def upload_blob(self, blob_digest: str, body, size: int) -> None:
        raw = body if isinstance(body, bytes) else body.read()
        assert len(raw) == size
        assert blob_digest == "sha256:" + digest(raw)
        self.blobs.append((blob_digest, raw))

    def upload_manifest_by_digest(self, raw: bytes, manifest_digest: str) -> None:
        assert manifest_digest == "sha256:" + digest(raw)
        self.manifests.append((manifest_digest, raw))


class UntaggedUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="cosmaneura-acr-upload-test-", dir="/private/tmp"
        )
        self.archive = Path(self.temp.name) / "image.tar"

    def tearDown(self) -> None:
        self.temp.cleanup()
        FakeRegistry.instances.clear()

    def write_archive(self, *, candidate: str = m.CANDIDATE_SHA) -> bytes:
        layer = b"invented uncompressed layer\n"
        config = json.dumps(
            {
                "architecture": "amd64",
                "os": "linux",
                "config": {
                    "Labels": {
                        "org.opencontainers.image.revision": candidate,
                        "org.opencontainers.image.source": "https://github.com/CosmaNeura/cosmaneura",
                    }
                },
                "rootfs": {"diff_ids": ["sha256:" + digest(layer)]},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.config_digest = digest(config)
        manifest = json.dumps(
            [
                {
                    "Config": "config.json",
                    "RepoTags": [],
                    "Layers": ["layer.tar"],
                }
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        with tarfile.open(self.archive, "w") as archive:
            for name, raw in (
                ("manifest.json", manifest),
                ("config.json", config),
                ("layer.tar", layer),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        self.archive.chmod(0o400)
        return self.archive.read_bytes()

    @staticmethod
    def token() -> dict[str, str]:
        return {
            "accessToken": "x" * 256,
            "refreshToken": "",
            "loginServer": m.REGISTRY_HOST,
            "username": m.USERNAME,
        }

    def test_uploads_blobs_and_manifest_only_by_digest(self) -> None:
        raw = self.write_archive()
        token = self.token()
        with patch.object(m, "Registry", FakeRegistry):
            result = m.build_and_upload(
                self.archive, digest(raw), "sha256:" + self.config_digest, token
            )
        self.assertEqual(token, {})
        self.assertFalse(result["tag_created"])
        self.assertFalse(result["tag_overwrite_possible"])
        self.assertRegex(result["manifest_digest"], r"^sha256:[0-9a-f]{64}$")
        registry = FakeRegistry.instances[-1]
        self.assertEqual(len(registry.blobs), 2)
        self.assertEqual(len(registry.manifests), 1)
        manifest = json.loads(registry.manifests[0][1])
        self.assertEqual(manifest["mediaType"], m.OCI_MANIFEST)
        self.assertNotIn("annotations", manifest)

    def test_wrong_candidate_label_is_refused_before_registry_access(self) -> None:
        raw = self.write_archive(candidate="0" * 40)
        with (
            patch.object(m, "Registry", FakeRegistry),
            self.assertRaises(m.UploadRefused),
        ):
            m.build_and_upload(
                self.archive,
                digest(raw),
                "sha256:" + self.config_digest,
                self.token(),
            )
        self.assertEqual(FakeRegistry.instances, [])

    def test_archive_digest_and_mode_are_mandatory(self) -> None:
        raw = self.write_archive()
        with self.assertRaises(m.UploadRefused):
            m.build_and_upload(
                self.archive,
                "0" * 64,
                "sha256:" + self.config_digest,
                self.token(),
            )
        self.archive.chmod(0o600)
        with self.assertRaises(m.UploadRefused):
            m.build_and_upload(
                self.archive,
                digest(raw),
                "sha256:" + self.config_digest,
                self.token(),
            )


if __name__ == "__main__":
    unittest.main()
