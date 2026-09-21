"""Exact configured-user/token session for a later reviewed HUMAN snapshot.

Only the already bound encrypted-copy CLI may obtain the configured account and
ARM token. The original protected cache supplies independent home-account
linkage; a refreshed token found only in the copy is refused. No human launcher,
authority minting, secret output, network request or original-cache write here.
"""

from __future__ import annotations

import encodings.idna  # Preload before sealing, also needed by HTTPS hostname I/O.
import hashlib
import json
import os
import signal
import ssl
import stat
import time
from pathlib import Path

from staging_snapshot_cli import OwnedAzureCLI
from staging_snapshot_configuration import BoundConfiguration, ConfiguredSource
from staging_snapshot_identity import ExpectedIdentity, bind_configured_identity
from staging_snapshot_native_files import (
    FileObservation,
    NativeFiles,
    identity,
    owned_open,
)
from staging_snapshot_pg_io import capture_deadline, require_capture_process
from staging_snapshot_runtime import SnapshotRuntime

assert encodings.idna.ToASCII("management.azure.com") == b"management.azure.com"


class SnapshotSessionRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SnapshotSessionRefused("snapshot_session_refused")


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def expected_identity() -> ExpectedIdentity:
    # Source literals, never selected by the plan, environment, CLI or a caller.
    return ExpectedIdentity(
        "bab2a709-1fcb-4c87-a9fc-802f7477ec13",
        "d51754d0-bba5-4810-820e-afac542844d6",
        "acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a",
    )


def reject_ambient_transport() -> None:
    forbidden = {
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "sslkeylogfile",
        "ssl_cert_file",
        "ssl_cert_dir",
        "curl_ca_bundle",
        "requests_ca_bundle",
        "openssl_conf",
        "openssl_modules",
        "ld_preload",
        "ld_library_path",
    }
    require(
        not any(
            name.lower() in forbidden or name.upper().startswith(("DYLD_", "PYTHON"))
            for name in os.environ
        )
    )


def fixed_ca(files: NativeFiles) -> FileObservation:
    """Protected root-owned public CA bytes, fixed source path and digest.

    No default-CA search, symlink resolution or current-file self-attestation.
    Caller owns the already active absolute timer and exact source bootstrap.
    """
    require_capture_process()
    require(type(files) is NativeFiles and signal.getitimer(signal.ITIMER_REAL)[0] > 0)
    path = Path("/private/etc/ssl/cert.pem")
    with files.protected_parent(path) as parent:
        with owned_open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=parent,
        ) as descriptor:
            before = os.fstat(descriptor)
            require(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == 0
                and before.st_nlink == 1
                and not stat.S_IMODE(before.st_mode) & 0o022
                and 0 < before.st_size <= 4 * 1024**2
            )
            files.acl_safe(descriptor)
            chunks, remaining = [], before.st_size
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                require(chunk)
                chunks.append(chunk)
                remaining -= len(chunk)
            pem = b"".join(chunks)
            require(
                hashlib.sha256(pem).hexdigest()
                == "9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994"
            )
            files.acl_safe(descriptor)
            require(
                identity(before)
                == identity(os.fstat(descriptor))
                == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
            )
            return FileObservation(pem, identity(before), identity(os.fstat(parent)))


def tls_context(pem: bytes) -> ssl.SSLContext:
    reject_ambient_transport()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_verify_locations(cadata=pem.decode("ascii"))
    require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED)
    return context


class SnapshotSession:
    __slots__ = ("_cli", "_files", "_token", "_ca", "_identity")

    def _types(self):
        require_capture_process()
        reject_ambient_transport()
        require(type(self._cli) is OwnedAzureCLI and type(self._files) is NativeFiles)
        require(type(self._cli._runtime) is SnapshotRuntime)
        require(type(self._cli._copy) is BoundConfiguration)
        require(type(self._cli._copy.owner) is ConfiguredSource)
        require(self._cli._copy.owner._files is self._files)

    def __init__(self, cli: OwnedAzureCLI, files: NativeFiles, *, deadline: float):
        try:
            self._cli, self._files = cli, files
            self._identity: dict[str, object] | None = None
            self._types()
            require(
                type(deadline) in (int, float)
                and 0 < deadline - time.monotonic() <= 120
            )
            with capture_deadline(deadline):
                self._ca = fixed_ca(self._files)
                tls_context(self._ca.content)
            require(
                fingerprint(cli.read("version", deadline=deadline))
                == "98b3412b818478b3ef731be86fd539b32628e5621342f4873ac325c87428facc"
            )
            require(
                fingerprint(cli.read("cloud", deadline=deadline))
                == "1f30818094483f03f402801991b418f9065244acc8c871047ca46e5ce58b872f"
            )
            token = cli.read("token", deadline=deadline)
            expected = expected_identity()
            require(
                token.get("tokenType") == "Bearer"
                and token.get("subscription") == expected.subscription
                and token.get("tenant") == expected.tenant
                and type(token.get("accessToken")) is str
            )
            self._token = token["accessToken"]
            # 35 minutes at preparation, not permission to start an arbitrarily
            # delayed human window. The real bootstrap must recheck the entire
            # remaining action/cleanup window immediately before instructions.
            self.verify(deadline=deadline, minimum_remaining_seconds=2100)
        except BaseException:
            raise SnapshotSessionRefused("snapshot_session_refused") from None

    def verify(
        self, *, deadline: float, minimum_remaining_seconds: int = 240
    ) -> dict[str, object]:
        """Sequential guards use ONE absolute deadline; never nest/reset it.

        Identity is linkage, not JWT signature validation or authority. Only a
        successful fixed ARM request with this token authenticates the linkage.
        """
        try:
            self._types()
            require(
                type(deadline) in (int, float)
                and 0 < deadline - time.monotonic() <= 120
            )
            account = self._cli.read("account", deadline=deadline)
            with capture_deadline(deadline):
                self._cli._verify(deadline)
                require(fixed_ca(self._files) == self._ca)
                current = bind_configured_identity(
                    account,
                    self._token,
                    self._cli._copy.owner.identity_cache(),
                    expected_identity(),
                    now_epoch=int(time.time()),
                    minimum_remaining_seconds=minimum_remaining_seconds,
                )
                stable = {
                    key: current[key]
                    for key in (
                        "configured_username_sha256",
                        "current_token_oid_sha256",
                        "home_account_id_sha256",
                        "exact_arm_token_sha256",
                    )
                }
                if self._identity is None:
                    self._identity = stable
                require(stable == self._identity)
                return dict(current)
        except BaseException:
            raise SnapshotSessionRefused("snapshot_session_refused") from None

    def context(self, *, deadline: float) -> ssl.SSLContext:
        try:
            self._types()
            with capture_deadline(deadline):
                self._cli._verify(deadline)
                require(fixed_ca(self._files) == self._ca)
                return tls_context(self._ca.content)
        except BaseException:
            raise SnapshotSessionRefused("snapshot_session_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_session_requires_reviewed_human_bootstrap")
