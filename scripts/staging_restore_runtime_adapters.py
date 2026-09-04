#!/usr/bin/env python3
"""Dormant read-only adapters for the trusted staging restore executor.

Nothing imports these adapters from an executable path yet.  They provide one
managed-identity ARM GET and one digest-pinned offline GitHub attestation
verification process.  Restore, marker, database, and deployment capabilities
are intentionally absent.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import selectors
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, NoReturn, Protocol, cast

try:
    from scripts import authenticate_staging_github_attestation as attestation
    from scripts import verify_staging_source_provider_observation as observation
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_github_attestation as attestation
    import verify_staging_source_provider_observation as observation


ARM_ORIGIN = "https://management.azure.com"
ARM_RESOURCE = "https://management.azure.com/"
IDENTITY_API_VERSION = "2019-08-01"
SOURCE_RESOURCE_ID = (
    "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/"
    "resourceGroups/cosmaneura-staging/providers/"
    "Microsoft.DBforPostgreSQL/flexibleServers/cosmaneura-staging-postgres"
)
POSTGRES_API_VERSION = "2024-08-01"
REQUEST_TIMEOUT_SECONDS = 30
MAXIMUM_PROVIDER_BYTES = 1_048_576
MAXIMUM_TOKEN_BYTES = 65_536
MAXIMUM_EXECUTABLE_BYTES = 268_435_456
MAXIMUM_CA_BUNDLE_BYTES = 8_388_608
RUNTIME_CA_BUNDLE_PATH = Path("/etc/ssl/certs/ca-certificates.crt")
LINUX_F_ADD_SEALS = 1033
LINUX_F_GET_SEALS = 1034
LINUX_REQUIRED_SEALS = 0x000F
GH_VERSION = "2.97.0"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,16384}$")
UNSIGNED_DECIMAL_RE = re.compile(r"^[0-9]{1,20}$")
OFFLINE_FILES = {
    "evidence-manifest.json": 8_388_608,
    "attestation.json": 2_097_152,
    "github-trusted-root.jsonl": 1_048_576,
}
STATIC_VERIFIER_ARGUMENTS = {
    0: "gh",
    1: "attestation",
    2: "verify",
    3: "evidence-manifest.json",
    4: "--bundle",
    5: "attestation.json",
    6: "--cert-identity",
    7: (
        "https://github.com/CosmaNeura/cosmaneura/.github/workflows/"
        "staging_restore_rehearsal.yml@refs/heads/master"
    ),
    8: "--cert-oidc-issuer",
    9: "https://token.actions.githubusercontent.com",
    10: "--custom-trusted-root",
    11: "github-trusted-root.jsonl",
    12: "--deny-self-hosted-runners",
    13: "--digest-alg",
    14: "sha256",
    15: "--format=json",
    16: "--hostname",
    17: "github.com",
    18: "--no-public-good",
    19: "--predicate-type",
    20: "https://slsa.dev/provenance/v1",
    21: "--repo",
    22: "CosmaNeura/cosmaneura",
    23: "--signer-digest",
    25: "--source-digest",
    27: "--source-ref",
    28: "refs/heads/master",
}


class RuntimeAdapterFailure(RuntimeError):
    """Bounded adapter failure safe to report without credentials or evidence."""


def _fail(code: str) -> NoReturn:
    raise RuntimeAdapterFailure(code)


class _DeadlineExpired(BaseException):
    pass


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _HttpOpener(Protocol):
    def open(self, request: Any, *, timeout: int) -> Any: ...


class _ProcessRunner(Protocol):
    def __call__(
        self,
        *,
        executable_fd: int,
        arguments: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
        maximum_output_bytes: int,
    ) -> tuple[int, bytes, bytes]: ...


class _ExecutableLoader(Protocol):
    def __call__(
        self, path: Path, expected_sha256: str
    ) -> Any: ...  # Context manager yielding (fd, observed digest).


def _read_runtime_ca_bundle(path: Path) -> str:
    if not isinstance(path, Path) or not path.is_absolute():
        _fail("tls.configuration_invalid")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= MAXIMUM_CA_BUNDLE_BYTES
        ):
            _fail("tls.configuration_invalid")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor, min(1_048_576, MAXIMUM_CA_BUNDLE_BYTES - total + 1)
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAXIMUM_CA_BUNDLE_BYTES:
                _fail("tls.configuration_invalid")
        after = os.fstat(descriptor)
        if total != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            _fail("tls.configuration_invalid")
        return b"".join(chunks).decode("ascii")
    except RuntimeAdapterFailure:
        raise
    except (OSError, UnicodeDecodeError, ValueError):
        raise RuntimeAdapterFailure("tls.configuration_invalid") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _runtime_tls_context(
    ca_bundle_path: Path | None = None,
) -> ssl.SSLContext:
    """Load only the immutable-image CA bundle, never ambient TLS settings."""

    path = RUNTIME_CA_BUNDLE_PATH if ca_bundle_path is None else ca_bundle_path
    ca_bundle = _read_runtime_ca_bundle(path)
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.options |= ssl.OP_NO_COMPRESSION
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        if context.keylog_filename is not None:
            _fail("tls.configuration_invalid")
        context.load_verify_locations(cadata=ca_bundle)
    except (OSError, ssl.SSLError, ValueError):
        raise RuntimeAdapterFailure("tls.configuration_invalid") from None
    return context


def _default_opener(*, tls_context: ssl.SSLContext | None = None) -> Any:
    context = tls_context if tls_context is not None else _runtime_tls_context()
    if not isinstance(context, ssl.SSLContext):
        _fail("tls.configuration_invalid")
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirects(),
        urllib.request.HTTPSHandler(context=context),
    )


@contextmanager
def _wall_clock_deadline(timeout_seconds: int) -> Iterator[None]:
    """Bound the complete synchronous HTTP exchange in the executor process."""

    current_thread = threading.current_thread()
    live_threads = threading.enumerate()
    if (
        type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 60
        or current_thread is not threading.main_thread()
        or len(live_threads) != 1
        or live_threads[0] is not current_thread
        or any(
            not hasattr(signal, name)
            for name in (
                "setitimer",
                "ITIMER_REAL",
                "pthread_sigmask",
                "sigpending",
                "sigwait",
            )
        )
    ):
        _fail("http_request.deadline_unavailable")
    try:
        previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
        current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    except (OSError, ValueError):
        raise RuntimeAdapterFailure("http_request.deadline_unavailable") from None
    if previous_delay != 0 or previous_interval != 0 or signal.SIGALRM in current_mask:
        _fail("http_request.deadline_unavailable")

    def expire(_signum: int, _frame: Any) -> NoReturn:
        raise _DeadlineExpired

    try:
        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, expire)
    except (OSError, ValueError):
        raise RuntimeAdapterFailure("http_request.deadline_unavailable") from None
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    except (OSError, ValueError):
        signal.signal(signal.SIGALRM, previous_handler)
        raise RuntimeAdapterFailure("http_request.deadline_unavailable") from None
    expired = False
    try:
        try:
            yield
        except _DeadlineExpired:
            expired = True
        finally:
            _clear_wall_clock_deadline(previous_handler)
    except _DeadlineExpired:
        # The one-shot alarm can land in the few instructions before teardown
        # masks it. It has then fired, so a second masked cleanup is bounded.
        expired = True
        _clear_wall_clock_deadline(previous_handler)
    if expired:
        raise RuntimeAdapterFailure("http_request.timeout") from None


def _clear_wall_clock_deadline(previous_handler: Any) -> None:
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if signal.SIGALRM in signal.sigpending():
            signal.sigwait({signal.SIGALRM})
        signal.signal(signal.SIGALRM, previous_handler)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _load_mapping(raw: bytes, code: str) -> Mapping[str, Any]:
    if type(raw) is not bytes or not raw:
        _fail(code)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                _fail(code)
            value[key] = item
        return value

    def reject_nonfinite(_value: str) -> NoReturn:
        _fail(code)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except RuntimeAdapterFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RuntimeAdapterFailure(code) from None
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _header_values(headers: Any, name: str) -> tuple[str, ...]:
    try:
        if hasattr(headers, "get_all"):
            raw = headers.get_all(name) or []
        else:
            value = headers.get(name)
            raw = [] if value is None else [value]
        values = tuple(str(value) for value in raw)
    except Exception:
        _fail("http_response.invalid")
    if any(len(value) > 4096 or "\r" in value or "\n" in value for value in values):
        _fail("http_response.invalid")
    return values


def _read_json_response(
    response: Any,
    *,
    expected_url: str,
    maximum_bytes: int,
) -> tuple[int, str, bytes]:
    try:
        status_code = response.status
        final_url = response.geturl()
    except Exception:
        _fail("http_response.invalid")
    if type(status_code) is not int or final_url != expected_url:
        _fail("http_response.invalid")
    content_types = _header_values(response.headers, "Content-Type")
    encodings = _header_values(response.headers, "Content-Encoding")
    lengths = _header_values(response.headers, "Content-Length")
    if (
        len(content_types) != 1
        or content_types[0].split(";", 1)[0].strip().lower() != "application/json"
        or len(encodings) > 1
        or (encodings and encodings[0].strip().lower() != "identity")
        or len(lengths) > 1
    ):
        _fail("http_response.invalid")
    declared_length: int | None = None
    if lengths:
        if not lengths[0].isdigit():
            _fail("http_response.invalid")
        declared_length = int(lengths[0])
        if declared_length > maximum_bytes:
            _fail("http_response.too_large")
    try:
        raw = response.read(maximum_bytes + 1)
    except Exception:
        raise RuntimeAdapterFailure("http_response.unavailable") from None
    if type(raw) is not bytes:
        _fail("http_response.invalid")
    if len(raw) > maximum_bytes:
        _fail("http_response.too_large")
    if declared_length is not None and declared_length != len(raw):
        _fail("http_response.invalid")
    return status_code, content_types[0], raw


def _validate_identity_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or len(endpoint) > 512:
        _fail("managed_identity.configuration_invalid")
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        _fail("managed_identity.configuration_invalid")
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or port is None
        or not 1 <= port <= 65535
        or not parsed.path.startswith("/")
        or parsed.path.rstrip("/").lower() != "/msi/token"
        or len(parsed.path) > 256
        or "%" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in parsed.path)
    ):
        _fail("managed_identity.configuration_invalid")
    host = parsed.hostname.lower()
    if "%" in host:
        _fail("managed_identity.configuration_invalid")
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            _fail("managed_identity.configuration_invalid")
        if not (address.is_loopback or address.is_link_local):
            _fail("managed_identity.configuration_invalid")
    return endpoint


class ManagedIdentityTokenSource:
    """One exact Container Apps managed-identity exchange with no fallback."""

    __slots__ = (
        "_client_id",
        "_clock",
        "_identity_endpoint",
        "_identity_header",
        "_lock",
        "_opener",
        "_used",
    )

    def __init__(
        self,
        *,
        client_id: str,
        identity_endpoint: str,
        identity_header: str,
        opener: _HttpOpener | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            not isinstance(client_id, str)
            or UUID_RE.fullmatch(client_id) is None
            or not isinstance(identity_header, str)
            or not 1 <= len(identity_header) <= 8192
            or SAFE_TOKEN_RE.fullmatch(identity_header) is None
            or not callable(clock)
        ):
            _fail("managed_identity.configuration_invalid")
        self._client_id = client_id
        self._identity_endpoint = _validate_identity_endpoint(identity_endpoint)
        self._identity_header = identity_header
        self._opener = opener or _default_opener()
        self._clock = clock
        self._lock = threading.Lock()
        self._used = False

    @classmethod
    def from_environment(
        cls,
        *,
        client_id: str,
        opener: _HttpOpener | None = None,
        clock: Callable[[], float] = time.time,
    ) -> ManagedIdentityTokenSource:
        endpoint = os.environ.get("IDENTITY_ENDPOINT", "")
        header = os.environ.get("IDENTITY_HEADER", "")
        return cls(
            client_id=client_id,
            identity_endpoint=endpoint,
            identity_header=header,
            opener=opener,
            clock=clock,
        )

    def get_token(self, *, resource: str, timeout_seconds: int) -> str:
        if (
            type(resource) is not str
            or resource != ARM_RESOURCE
            or type(timeout_seconds) is not int
            or timeout_seconds != REQUEST_TIMEOUT_SECONDS
        ):
            _fail("managed_identity.request_invalid")
        with self._lock:
            if self._used:
                _fail("managed_identity.already_used")
            self._used = True
            try:
                now = int(self._clock())
            except Exception:
                raise RuntimeAdapterFailure("managed_identity.clock_invalid") from None
            query = urllib.parse.urlencode(
                {
                    "api-version": IDENTITY_API_VERSION,
                    "resource": resource,
                    "client_id": self._client_id,
                }
            )
            url = f"{self._identity_endpoint}?{query}"
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Metadata": "true",
                    "X-IDENTITY-HEADER": self._identity_header,
                },
                method="GET",
            )
            try:
                with _wall_clock_deadline(timeout_seconds):
                    with self._opener.open(
                        request, timeout=timeout_seconds
                    ) as response:
                        status_code, _content_type, raw = _read_json_response(
                            response,
                            expected_url=url,
                            maximum_bytes=MAXIMUM_TOKEN_BYTES,
                        )
            except RuntimeAdapterFailure:
                raise
            except Exception:
                raise RuntimeAdapterFailure("managed_identity.unavailable") from None
            if status_code != 200:
                _fail("managed_identity.refused")
            document = _load_mapping(raw, "managed_identity.response_invalid")
            token = document.get("access_token")
            expires_on = document.get("expires_on")
            if (
                not isinstance(token, str)
                or SAFE_TOKEN_RE.fullmatch(token) is None
                or document.get("token_type") != "Bearer"
                or document.get("resource") != resource
                or document.get("client_id") != self._client_id
                or not isinstance(expires_on, str)
                or UNSIGNED_DECIMAL_RE.fullmatch(expires_on) is None
            ):
                _fail("managed_identity.response_invalid")
            expiration = int(expires_on)
            if expiration <= now + 30:
                _fail("managed_identity.response_invalid")
            return token


class AzureSourceObservationProvider:
    """One no-redirect raw-byte GET of the exact staging source server."""

    __slots__ = ("_lock", "_opener", "_token_source", "_used")

    def __init__(
        self,
        token_source: ManagedIdentityTokenSource,
        *,
        opener: _HttpOpener | None = None,
    ) -> None:
        if type(token_source) is not ManagedIdentityTokenSource:
            _fail("source_provider.configuration_invalid")
        self._token_source = token_source
        self._opener = opener or _default_opener()
        self._lock = threading.Lock()
        self._used = False

    def get_flexible_server(
        self,
        *,
        management_origin: str,
        resource_id: str,
        api_version: str,
        timeout_seconds: int,
    ) -> observation.ProviderObservationResponse:
        with self._lock:
            if self._used:
                _fail("source_provider.already_used")
            self._used = True
        if (
            management_origin != ARM_ORIGIN
            or resource_id != SOURCE_RESOURCE_ID
            or api_version != POSTGRES_API_VERSION
            or type(timeout_seconds) is not int
            or timeout_seconds != REQUEST_TIMEOUT_SECONDS
        ):
            _fail("source_provider.request_invalid")
        token = self._token_source.get_token(
            resource=ARM_RESOURCE,
            timeout_seconds=timeout_seconds,
        )
        query = urllib.parse.urlencode({"api-version": api_version})
        url = f"{management_origin}{resource_id}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {token}",
            },
            method="GET",
        )
        try:
            with _wall_clock_deadline(timeout_seconds):
                with self._opener.open(request, timeout=timeout_seconds) as response:
                    status_code, content_type, raw = _read_json_response(
                        response,
                        expected_url=url,
                        maximum_bytes=MAXIMUM_PROVIDER_BYTES,
                    )
        except RuntimeAdapterFailure:
            raise
        except urllib.error.HTTPError as exc:
            if 300 <= int(exc.code) < 400:
                raise RuntimeAdapterFailure(
                    "source_provider.redirect_refused"
                ) from None
            raise RuntimeAdapterFailure("source_provider.refused") from None
        except Exception:
            raise RuntimeAdapterFailure("source_provider.unavailable") from None
        return observation.ProviderObservationResponse(
            status=status_code,
            content_type=content_type,
            body=raw,
        )


def _validate_verifier_arguments(arguments: tuple[str, ...]) -> None:
    if (
        type(arguments) is not tuple
        or len(arguments) != 29
        or any(type(value) is not str or not value for value in arguments)
        or any("\x00" in value or len(value) > 1024 for value in arguments)
        or any(
            arguments[index] != expected
            for index, expected in STATIC_VERIFIER_ARGUMENTS.items()
        )
        or SHA_RE.fullmatch(arguments[24]) is None
        or arguments[26] != arguments[24]
    ):
        _fail("offline_verifier.arguments_invalid")


def _snapshot_offline_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(files, Mapping) or set(files) != set(OFFLINE_FILES):
        _fail("offline_verifier.files_invalid")
    snapshot: dict[str, bytes] = {}
    for name, maximum in OFFLINE_FILES.items():
        try:
            raw = files[name]
        except Exception:
            raise RuntimeAdapterFailure("offline_verifier.files_invalid") from None
        if type(raw) is not bytes or not 0 < len(raw) <= maximum:
            _fail("offline_verifier.files_invalid")
        snapshot[name] = raw
    return snapshot


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        try:
            written = os.write(descriptor, raw[offset:])
        except OSError:
            raise RuntimeAdapterFailure("offline_verifier.workspace_failed") from None
        if written <= 0:
            _fail("offline_verifier.workspace_failed")
        offset += written


def _create_linux_memfd() -> int:
    try:
        create: Any = ctypes.CDLL(None, use_errno=True).memfd_create
        create.argtypes = (ctypes.c_char_p, ctypes.c_uint)
        create.restype = ctypes.c_int
        descriptor = int(create(b"cosmaneura-gh-verifier", 0x0001 | 0x0002))
    except (AttributeError, OSError, TypeError, ValueError):
        _fail("offline_verifier.platform_unsupported")
    if descriptor < 0:
        _fail("offline_verifier.unavailable")
    return descriptor


@contextmanager
def _sealed_linux_executable(
    path: Path, expected_sha256: str
) -> Iterator[tuple[int, str]]:
    if sys.platform != "linux" or not Path("/proc/self/fd").is_dir():
        _fail("offline_verifier.platform_unsupported")
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not isinstance(expected_sha256, str)
        or DIGEST_RE.fullmatch(expected_sha256) is None
    ):
        _fail("offline_verifier.executable_invalid")
    source_fd = -1
    sealed_fd = -1
    try:
        if path.resolve(strict=True) != path:
            _fail("offline_verifier.executable_invalid")
        before = os.lstat(path)
        source_fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(source_fd)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(opened.st_mode) & 0o022
            or not stat.S_IMODE(opened.st_mode) & 0o100
            or not 1 <= opened.st_size <= MAXIMUM_EXECUTABLE_BYTES
        ):
            _fail("offline_verifier.executable_invalid")
        sealed_fd = _create_linux_memfd()
        digest = hashlib.sha256()
        total = 0
        prefix = b""
        while True:
            chunk = os.read(source_fd, 1_048_576)
            if not chunk:
                break
            total += len(chunk)
            if total > MAXIMUM_EXECUTABLE_BYTES:
                _fail("offline_verifier.executable_invalid")
            if len(prefix) < 4:
                prefix = (prefix + chunk)[:4]
            digest.update(chunk)
            _write_all(sealed_fd, chunk)
        after = os.fstat(source_fd)
        observed_sha256 = f"sha256:{digest.hexdigest()}"
        if (
            total != opened.st_size
            or prefix != b"\x7fELF"
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or observed_sha256 != expected_sha256
        ):
            _fail("offline_verifier.executable_invalid")
        os.fchmod(sealed_fd, 0o500)
        fcntl.fcntl(sealed_fd, LINUX_F_ADD_SEALS, LINUX_REQUIRED_SEALS)
        if fcntl.fcntl(sealed_fd, LINUX_F_GET_SEALS) != LINUX_REQUIRED_SEALS:
            _fail("offline_verifier.executable_invalid")
        os.lseek(sealed_fd, 0, os.SEEK_SET)
        yield sealed_fd, observed_sha256
    except RuntimeAdapterFailure:
        raise
    except (OSError, ValueError):
        raise RuntimeAdapterFailure("offline_verifier.executable_invalid") from None
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if sealed_fd >= 0:
            os.close(sealed_fd)


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_bounded_process(
    *,
    executable_fd: int,
    arguments: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    maximum_output_bytes: int,
) -> tuple[int, bytes, bytes]:
    if sys.platform != "linux":
        _fail("offline_verifier.platform_unsupported")
    executable = f"/proc/self/fd/{executable_fd}"
    try:
        process = subprocess.Popen(
            (executable, *arguments[1:]),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(executable_fd,),
            start_new_session=True,
        )
    except (OSError, ValueError):
        raise RuntimeAdapterFailure("offline_verifier.unavailable") from None
    selector: selectors.BaseSelector | None = None
    stdout = process.stdout
    stderr = process.stderr
    try:
        if stdout is None or stderr is None:
            _fail("offline_verifier.unavailable")
        selector = selectors.DefaultSelector()
        streams = {stdout: [], stderr: []}
        total = 0
        deadline = time.monotonic() + timeout_seconds
        for stream in streams:
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process_group(process)
                _fail("offline_verifier.timeout")
            for key, _mask in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, min(65_536, maximum_output_bytes - total + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > maximum_output_bytes:
                    _kill_process_group(process)
                    _fail("offline_verifier.output_too_large")
                streams[cast(BinaryIO, key.fileobj)].append(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_process_group(process)
            _fail("offline_verifier.timeout")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            _fail("offline_verifier.timeout")
        return (
            returncode,
            b"".join(streams[stdout]),
            b"".join(streams[stderr]),
        )
    except RuntimeAdapterFailure:
        raise
    except Exception:
        _kill_process_group(process)
        raise RuntimeAdapterFailure("offline_verifier.unavailable") from None
    finally:
        _kill_process_group(process)
        if selector is not None:
            selector.close()
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()


class DigestPinnedGitHubCliVerifier:
    """Execute exactly one offline gh verification from sealed Linux bytes."""

    __slots__ = (
        "_executable_loader",
        "_executable_path",
        "_expected_sha256",
        "_expected_version",
        "_lock",
        "_process_runner",
        "_used",
    )

    def __init__(
        self,
        *,
        executable_path: Path,
        expected_sha256: str,
        expected_version: str,
        executable_loader: _ExecutableLoader = _sealed_linux_executable,
        process_runner: _ProcessRunner = _run_bounded_process,
    ) -> None:
        if (
            not isinstance(executable_path, Path)
            or not executable_path.is_absolute()
            or not isinstance(expected_sha256, str)
            or DIGEST_RE.fullmatch(expected_sha256) is None
            or expected_version != GH_VERSION
            or not callable(executable_loader)
            or not callable(process_runner)
        ):
            _fail("offline_verifier.configuration_invalid")
        self._executable_path = executable_path
        self._expected_sha256 = expected_sha256
        self._expected_version = expected_version
        self._executable_loader = executable_loader
        self._process_runner = process_runner
        self._lock = threading.Lock()
        self._used = False

    def verify(
        self,
        *,
        arguments: tuple[str, ...],
        files: Mapping[str, bytes],
        timeout_seconds: int,
        maximum_output_bytes: int,
    ) -> attestation.AttestationVerifierResponse:
        with self._lock:
            if self._used:
                _fail("offline_verifier.already_used")
            self._used = True
        _validate_verifier_arguments(arguments)
        file_snapshot = _snapshot_offline_files(files)
        if (
            type(timeout_seconds) is not int
            or timeout_seconds != REQUEST_TIMEOUT_SECONDS
            or type(maximum_output_bytes) is not int
            or maximum_output_bytes != 4_194_304
        ):
            _fail("offline_verifier.request_invalid")
        try:
            with tempfile.TemporaryDirectory(
                prefix="cosmaneura-gh-attestation-", dir="/tmp"
            ) as directory:
                workspace = Path(directory)
                os.chmod(workspace, 0o700)
                github_config = workspace / "gh"
                github_config.mkdir(mode=0o700)
                directory_fd = os.open(
                    workspace,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                )
                try:
                    for name in OFFLINE_FILES:
                        descriptor = os.open(
                            name,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | getattr(os, "O_CLOEXEC", 0)
                            | getattr(os, "O_NOFOLLOW", 0),
                            0o600,
                            dir_fd=directory_fd,
                        )
                        try:
                            _write_all(descriptor, file_snapshot[name])
                            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                                _fail("offline_verifier.workspace_failed")
                        finally:
                            os.close(descriptor)
                finally:
                    os.close(directory_fd)
                environment = {
                    "ALL_PROXY": "http://127.0.0.1:9",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GH_CONFIG_DIR": str(github_config),
                    "GH_NO_UPDATE_NOTIFIER": "1",
                    "GH_PROMPT_DISABLED": "1",
                    "HOME": str(workspace),
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "NO_PROXY": "",
                    "TMPDIR": str(workspace),
                }
                with self._executable_loader(
                    self._executable_path, self._expected_sha256
                ) as (executable_fd, observed_sha256):
                    if observed_sha256 != self._expected_sha256:
                        _fail("offline_verifier.executable_invalid")
                    returncode, stdout, stderr = self._process_runner(
                        executable_fd=executable_fd,
                        arguments=arguments,
                        cwd=workspace,
                        environment=environment,
                        timeout_seconds=timeout_seconds,
                        maximum_output_bytes=maximum_output_bytes,
                    )
        except RuntimeAdapterFailure:
            raise
        except Exception:
            raise RuntimeAdapterFailure("offline_verifier.unavailable") from None
        if (
            type(returncode) is not int
            or type(stdout) is not bytes
            or type(stderr) is not bytes
            or len(stdout) + len(stderr) > maximum_output_bytes
        ):
            _fail("offline_verifier.result_invalid")
        return attestation.AttestationVerifierResponse(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            verifier_version=self._expected_version,
            verifier_sha256=self._expected_sha256,
        )
