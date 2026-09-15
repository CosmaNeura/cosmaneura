"""Approved public-IP read for the future exact reviewed HUMAN snapshot.

Only GET https://api.ipify.org, at most three attempts in the sealed process.
No token, Azure session, credentials, application data, proxy, redirect or retry.
The composed human caller must enforce source/plan/handoff and Terminal gates;
this component and the recorded service permission are not execution authority.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from staging_snapshot_azure import NoRedirect
from staging_snapshot_native_files import FileObservation, NativeFiles
from staging_snapshot_pg_io import capture_deadline, require_capture_process
from staging_snapshot_session import fixed_ca, reject_ambient_transport, tls_context

URL = "https://api.ipify.org"
APPROVAL = Path(
    "/Users/jw/Desktop/Cosmas/staging-private-foundation-command-checkpoint-20260911/"
    "STAGING-IPIFY-APPROVAL-20260914.json"
)
APPROVAL_SHA256 = "2a8bdf2eb217c3ad9fee21bfdb3dd00b34efee160de6d77f82e6f43f23b023aa"
# Preserve the source module's retained global-object identity across attempts.
# This is trusted process-local bookkeeping, not a same-UID/native-code sandbox.
_attempts = [0]


class PublicIPRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise PublicIPRefused("snapshot_public_ip_refused")


def canonical_ipv4(raw: bytes) -> str:
    require(type(raw) is bytes and 1 <= len(raw) <= 16)
    value = raw.decode("ascii")
    value = value.removesuffix("\n")
    address = ipaddress.IPv4Address(value)
    require(str(address) == value and address.is_global)
    require(not address.is_multicast and not address.is_reserved)
    return value


class SnapshotPublicIP:
    __slots__ = ("_files", "_revalidate", "_approval")

    def __init__(self, files: NativeFiles, revalidate: Callable[[], None]):
        require_capture_process()
        require(type(files) is NativeFiles and callable(revalidate))
        self._files, self._revalidate = files, revalidate
        self._approval: FileObservation | None = None

    def _permission(self) -> FileObservation:
        require(type(self._files) is NativeFiles)
        value = self._files.read(APPROVAL, mode=0o400, maximum=4096)
        require(hashlib.sha256(value.content).hexdigest() == APPROVAL_SHA256)
        require(self._approval is None or self._approval == value)
        self._approval = value
        return value

    def _read(self, timeout: float) -> str:
        require_capture_process()
        require(type(timeout) in {int, float} and math.isfinite(timeout))
        require(type(_attempts) is list and len(_attempts) == 1)
        require(type(_attempts[0]) is int and 0 <= _attempts[0] < 3)
        require(0 < timeout <= 15)
        # Count before any I/O; failed requests consume budget as well. A new
        # instance cannot reset the process-wide limit. There is no reset API.
        _attempts[0] += 1
        deadline = time.monotonic() + timeout
        with capture_deadline(deadline):
            reject_ambient_transport()
            self._revalidate()
            approval = self._permission()
            ca = fixed_ca(self._files)
            context = tls_context(ca.content)
            require(
                type(context) is ssl.SSLContext
                and context.check_hostname
                and context.verify_mode == ssl.CERT_REQUIRED
                and context.minimum_version >= ssl.TLSVersion.TLSv1_2
                and context.options & ssl.OP_NO_COMPRESSION
            )
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                NoRedirect(),
                urllib.request.HTTPSHandler(context=context),
            )
            request = urllib.request.Request(
                URL,
                method="GET",
                data=None,
                headers={"Accept": "text/plain", "Accept-Encoding": "identity"},
            )
            require(
                (request.get_method(), request.full_url, request.data)
                == ("GET", URL, None)
            )
            remaining = deadline - time.monotonic()
            require(0 < remaining <= timeout)
            with opener.open(request, timeout=remaining) as response:
                require(response.status == 200 and response.geturl() == URL)
                kinds = response.headers.get_all("Content-Type") or []
                encodings = response.headers.get_all("Content-Encoding") or []
                lengths = response.headers.get_all("Content-Length") or []
                require(
                    len(kinds) == 1
                    and kinds[0].split(";", 1)[0].strip().lower() == "text/plain"
                )
                require(not encodings or encodings == ["identity"])
                require(
                    not lengths
                    or (
                        len(lengths) == 1
                        and lengths[0].isdigit()
                        and 1 <= int(lengths[0]) <= 16
                    )
                )
                raw = response.read(17)
                require(not lengths or int(lengths[0]) == len(raw))
                value = canonical_ipv4(raw)
            require(self._permission() == approval)
            require(fixed_ca(self._files) == ca)
            reject_ambient_transport()
            self._revalidate()
        return value

    def current_ipv4(self, timeout: float) -> str:
        """One bounded read; caller callback is LOCAL retained checks only.

        The callback shares this existing alarm, never resets a timer, launches
        a child, performs network I/O, or obtains a token. The final Operations
        adapter supplies the exact source/runtime binding's revalidate method.
        """
        try:
            return self._read(timeout)
        except urllib.error.HTTPError as error:
            try:
                error.close()
            finally:
                raise PublicIPRefused("snapshot_public_ip_refused") from None
        except BaseException:
            raise PublicIPRefused("snapshot_public_ip_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_public_ip_requires_reviewed_human_bootstrap")
