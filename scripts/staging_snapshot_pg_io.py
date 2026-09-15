"""Fixed-target PostgreSQL I/O for a later reviewed human snapshot launcher.

No CLI, credential discovery, Azure mutation, approval or receipt implementation.
Never call these live operations from an agent. The complete human launcher must
establish normally merged source, pinned runtime closure, explicit identity and
fresh exact plan/handoff reviews before constructing this adapter. Secretless
tests replace process creation and native encrypted-volume verification.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import pwd
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HOST = "cosmaneura-staging-postgres.postgres.database.azure.com"
DATABASE = "staging"
REVISION = "89c31f0ae275"
CLIENT_DIRECTORY = Path("/opt/homebrew/Cellar/postgresql@16/16.15/bin")
CA_FILE = "/etc/ssl/cert.pem"
ARCHIVE_NAME = "staging-snapshot.dump"
MAX_BYTES = 1024**3
MAX_SECONDS = 1200
CHUNK = 64 * 1024
SCHEMA_SQL = """BEGIN READ ONLY;
SELECT json_build_object(
 'database', current_database(),
 'major', current_setting('server_version_num')::integer / 10000,
 'readonly', current_setting('transaction_read_only') = 'on',
 'tls', (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()),
 'revisions', (SELECT json_agg(version_num ORDER BY version_num)
               FROM public.alembic_version));
ROLLBACK;"""


class PostgreSQLRefused(RuntimeError):
    """Fixed codes only; never embed provider text, SQL rows or credentials."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise PostgreSQLRefused(code)


_capture_process_pid: int | None = None


def require_core_disabled() -> None:
    require(
        resource.getrlimit(resource.RLIMIT_CORE) == (0, 0), "core_dumps_not_disabled"
    )


def native_single_thread() -> None:
    """Check OS threads, including ones not registered with threading."""
    if sys.platform == "darwin":
        observed = subprocess.run(
            ["/bin/ps", "-M", "-p", str(os.getpid()), "-o", "pid="],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        lines = observed.stdout.splitlines()
        require(
            observed.returncode == 0
            and len(lines) == 2
            and lines[0].split()[:2] == [b"USER", b"PID"]
            and lines[1].split()[-1:] == [str(os.getpid()).encode()],
            "capture_single_thread_required",
        )
    elif sys.platform == "linux":
        require(
            len(list(Path("/proc/self/task").iterdir())) == 1,
            "capture_single_thread_required",
        )
    else:
        raise PostgreSQLRefused("capture_single_thread_required")


def _prebound_native_calls() -> tuple[object | None, frozenset[int]]:
    """Python 3.14 audits calls as well as loading; permit a CLOSED OS set.

    Resolve addresses ourselves before the seal, never accept caller-provided
    pointers or later registration. These are only the already used ACL reads,
    ACL-memory release, thread-local errno and read-only dyld image inventory.
    NativeFiles/NativeImages supply fixed prototypes. This remains a trusted
    pinned-code invariant, not a sandbox for arbitrary malicious native calls.
    """
    if sys.platform != "darwin":
        return None, frozenset()
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    addresses = []
    for name in (
        "acl_get_fd_np",
        "acl_get_entry",
        "acl_get_tag_type",
        "acl_free",
        "__error",
        "_dyld_image_count",
        "_dyld_get_image_name",
    ):
        address = ctypes.cast(getattr(library, name), ctypes.c_void_p).value
        if type(address) is not int or address <= 0:
            raise PostgreSQLRefused("capture_native_binding")
        addresses.append(address)
    require(len(set(addresses)) == 7, "capture_native_binding")
    return library, frozenset(addresses)


def seal_capture_process() -> None:
    """Once, at startup of the reviewed fresh -I -S human process.

    Preload the complete pinned standard-library/source closure first. This
    permanent audit guard prohibits Python workers, subinterpreters, fork and
    later native-library loading. Existing native threads are also refused.
    Only the closed prebound system-function set may emit ctypes.call_function;
    all later dynamic loading/symbol resolution and other ctypes events refuse.
    It is an interface invariant for TRUSTED pinned code, not a sandbox for
    malicious code already loaded in this process. Never use in a shared host.
    """
    global _capture_process_pid
    require_core_disabled()
    require(
        _capture_process_pid is None
        and sys.flags.isolated
        and sys.flags.no_site
        and sys.version_info >= (3, 13)
        and sys.gettrace() is None
        and sys.getprofile() is None,
        "capture_fresh_process_required",
    )
    require(
        threading.current_thread() is threading.main_thread()
        and threading.active_count() == 1,
        "capture_single_thread_required",
    )

    native_binding = _prebound_native_calls()

    def guard(event: str, args: tuple[object, ...]) -> None:
        allowed_native_call = (
            event == "ctypes.call_function"
            and len(args) == 2
            and native_binding[0] is not None
            and type(args[0]) is int
            and args[0] in native_binding[1]
            and type(args[1]) is tuple
        )
        if (
            event.startswith("_thread.")
            or (event.startswith("ctypes.") and not allowed_native_call)
            or event
            in {
                "cpython.PyInterpreterState_New",
                "os.fork",
                "os.forkpty",
                "cosmaneura.capture.guard_probe",
            }
            or (event == "import" and args and args[0] not in sys.modules)
        ):
            raise PostgreSQLRefused("capture_process_frozen")

    sys.addaudithook(guard)
    try:
        sys.audit("cosmaneura.capture.guard_probe")
    except PostgreSQLRefused as error:
        require(str(error) == "capture_process_frozen", "capture_guard_unavailable")
    else:
        raise PostgreSQLRefused("capture_guard_unavailable")
    native_single_thread()
    _capture_process_pid = os.getpid()


def require_capture_process() -> None:
    require_core_disabled()
    require(
        _capture_process_pid == os.getpid()
        and threading.current_thread() is threading.main_thread()
        and threading.active_count() == 1,
        "capture_sealed_process_required",
    )


@contextmanager
def defer_control_signals() -> Iterator[None]:
    """Keep child ownership updates/reaping atomic with respect to Ctrl-C/alarm.

    Only trusted process spawn and bounded cleanup use this mask. SIGKILL still
    terminates children; this is not a guarantee against a hung host/kernel.
    """
    require_capture_process()
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGALRM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def capture_deadline(deadline: float) -> Iterator[None]:
    """Bound even blocking filesystem/native callbacks in the foreground thread.

    Never overwrite another active timer, reset the supplied absolute deadline,
    or accept a swallowed timeout. Cleanup has its own short bounded reserve.
    """
    require_capture_process()
    remaining = deadline - time.monotonic()
    require(
        math.isfinite(remaining) and 0 < remaining <= MAX_SECONDS, "capture_deadline"
    )
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "active_alarm_refused")
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    require(signal.SIGALRM not in original_mask, "blocked_alarm_refused")
    require(signal.SIGALRM not in signal.sigpending(), "pending_alarm_refused")
    previous = signal.getsignal(signal.SIGALRM)
    expired = False
    closing = False

    def expire(_number: int, _frame: object) -> None:
        nonlocal expired
        expired = True
        if not closing:
            raise PostgreSQLRefused("capture_deadline")

    try:
        signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, max(0.000001, deadline - time.monotonic()))
        yield
    finally:
        # A queued Python handler can run as teardown starts. Once closing is
        # set it only records expiry. Nested finally also covers delivery at
        # that first transition, before any global state has been restored.
        try:
            closing = True
        finally:
            closing = True
            try:
                signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
            finally:
                try:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                finally:
                    try:
                        # Drain this one-shot timer's blocked pending signal
                        # before restoring a handler that belongs to the caller.
                        if signal.SIGALRM in signal.sigpending():
                            expired = True
                            signal.sigwait({signal.SIGALRM})
                    finally:
                        try:
                            signal.signal(signal.SIGALRM, previous)
                        finally:
                            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        # Propagate expiry only after the old handler/mask and disarmed timer
        # are restored, never from the middle of teardown.
        require(not expired and time.monotonic() < deadline, "capture_deadline")


@dataclass(frozen=True)
class Credentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


def credentials_from_exact_source_dsn(raw: str) -> Credentials:
    """Only the already-bound staging application's database-url value.

    Parsing is target validation, not credential provenance or identity proof.
    The human launcher must retrieve it only from the exact authenticated app.
    """
    require_core_disabled()
    require(type(raw) is str and 1 <= len(raw) <= 8192, "credential_contract")
    require(not any(ord(character) < 32 for character in raw), "credential_contract")
    try:
        parsed = urlsplit(raw)
        query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
        require(
            parsed.scheme in {"postgresql", "postgresql+psycopg"}
            and parsed.hostname == HOST
            and parsed.port == 5432
            and parsed.path == "/staging"
            and not parsed.fragment
            and set(query) == {"sslmode"}
            and query["sslmode"] in (["require"], ["verify-full"])
            and parsed.username is not None
            and parsed.password is not None,
            "credential_contract",
        )
        username = unquote(parsed.username or "", errors="strict")
        password = unquote(parsed.password or "", errors="strict")
    except (ValueError, UnicodeError):
        raise PostgreSQLRefused("credential_contract") from None
    require(
        all(1 <= len(value) <= 4096 for value in (username, password))
        and not any(ord(char) < 32 for value in (username, password) for char in value),
        "credential_contract",
    )
    return Credentials(username, password)


def connection_environment(credentials: Credentials) -> dict[str, str]:
    require_core_disabled()
    require(type(credentials) is Credentials, "credential_contract")
    require(
        all(
            type(value) is str and 1 <= len(value) <= 4096
            for value in (credentials.username, credentials.password)
        )
        and not any(
            ord(char) < 32
            for value in (credentials.username, credentials.password)
            for char in value
        ),
        "credential_contract",
    )
    # libpq may resolve default TLS client material from passwd's home rather
    # than HOME. Refuse that ambient directory; do not load, change or delete it.
    require(
        not os.path.lexists(Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".postgresql"),
        "ambient_postgresql_tls_configuration",
    )
    # This allowlist is built from scratch. No inherited HOME/PGSERVICE, password
    # file, PGOPTIONS, proxy, DYLD, Python or application/provider configuration.
    # PGPASSWORD exists only in child memory/environment, never argv or a file.
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PGHOST": HOST,
        "PGPORT": "5432",
        "PGDATABASE": DATABASE,
        "PGUSER": credentials.username,
        "PGPASSWORD": credentials.password,
        "PGPASSFILE": "/dev/null",
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": CA_FILE,
        "PGGSSENCMODE": "disable",
        "PGCONNECT_TIMEOUT": "10",
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=1200000 -c lock_timeout=5000",
        "PGAPPNAME": "cosmaneura-bounded-staging-snapshot",
    }


def schema_observation(raw: bytes) -> None:
    require(0 < len(raw) <= 4096, "schema_output_bound")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise PostgreSQLRefused("source_schema_contract") from None
    require(
        type(value) is dict
        and set(value) == {"database", "major", "readonly", "tls", "revisions"}
        and value["database"] == DATABASE
        and type(value["major"]) is int
        and value["major"] == 16
        and value["readonly"] is True
        and value["tls"] is True
        and value["revisions"] == [REVISION],
        "source_schema_contract",
    )


def archive_revision(raw: bytes) -> None:
    # pg_restore emits SQL to an in-memory bounded pipe, never to a database.
    # Require the archived snapshot's own Alembic row, not just before/after
    # observations of a potentially independently changing live database.
    require(0 < len(raw) <= 65536, "archived_schema_output_bound")
    marker = b"COPY public.alembic_version (version_num) FROM stdin;\n"
    require(raw.count(marker) == 1, "archived_revision_contract")
    rows = raw.split(marker, 1)[1].split(b"\\.\n", 1)
    require(
        len(rows) == 2 and rows[0] == REVISION.encode() + b"\n",
        "archived_revision_contract",
    )


@dataclass(frozen=True)
class PayloadBinding:
    # Native encrypted workspace verification and manifest comparison are the
    # caller's required precondition. A constructed binding is NOT proof of it.
    directory_fd: int
    device: int
    inode: int

    def check(self) -> None:
        value = os.fstat(self.directory_fd)
        require(
            stat.S_ISDIR(value.st_mode)
            and value.st_dev == self.device
            and value.st_ino == self.inode
            and value.st_uid == os.geteuid()
            and stat.S_IMODE(value.st_mode) == 0o700,
            "payload_descriptor_changed",
        )


def open_exclusive_archive(binding: PayloadBinding) -> int:
    binding.check()
    require(not os.listdir(binding.directory_fd), "payload_not_empty")
    fd = os.open(
        ARCHIVE_NAME,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=binding.directory_fd,
    )
    try:
        check_archive_binding(binding, fd, expected_size=0)
        return fd
    except BaseException:
        os.close(fd)
        # Partial artifacts remain private; no automatic delete or overwrite.
        raise


def check_archive_binding(binding: PayloadBinding, fd: int, expected_size: int) -> None:
    binding.check()
    opened = os.fstat(fd)
    named = os.stat(ARCHIVE_NAME, dir_fd=binding.directory_fd, follow_symlinks=False)
    require(
        stat.S_ISREG(opened.st_mode)
        and stat.S_ISREG(named.st_mode)
        and opened.st_dev == binding.device == named.st_dev
        and opened.st_ino == named.st_ino
        and opened.st_nlink == named.st_nlink == 1
        and opened.st_uid == named.st_uid == os.geteuid()
        and stat.S_IMODE(opened.st_mode) in {0o400, 0o600}
        and opened.st_size == named.st_size == expected_size,
        "archive_descriptor_changed",
    )


class BoundedChildren:
    """One process group at a time; fixed caller commands, no shell or retries."""

    def __init__(self, deadline: float, revalidate_runtime: Callable[[], None]):
        require(
            math.isfinite(deadline) and deadline > time.monotonic(), "capture_deadline"
        )
        self.deadline = deadline
        self.revalidate_runtime = revalidate_runtime
        self.child: subprocess.Popen[bytes] | None = None
        self.cleanup_failed = False
        self.may_signal_child = False

    def remaining(self) -> float:
        value = self.deadline - time.monotonic()
        require(value > 0, "capture_deadline")
        return value

    def group_members(
        self, process_id: int, *, deadline: float | None = None
    ) -> tuple[tuple[int, str], ...]:
        # On macOS killpg(..., 0) can report EPERM for a zombie-only group.
        # Exact read-only ps selection distinguishes that from active children;
        # denial, malformed output or an unknown state is never called absence.
        remaining = 2 if deadline is None else min(2, deadline - time.monotonic())
        require(remaining > 0, "capture_group_readback")
        result = subprocess.run(
            ["/bin/ps", "-g", str(process_id), "-o", "pid=,pgid=,stat="],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
            check=False,
        )
        require(len(result.stdout) <= 16384, "capture_group_readback")
        if result.returncode == 1 and not result.stdout.strip():
            return ()
        require(
            result.returncode == 0 and result.stdout.strip(), "capture_group_readback"
        )
        members = []
        for line in result.stdout.decode("ascii").splitlines():
            fields = line.split()
            require(
                len(fields) == 3
                and all(value.isdigit() for value in fields[:2])
                and int(fields[1]) == process_id
                and re.fullmatch(r"[A-Z][A-Za-z+<>s]*", fields[2]),
                "capture_group_readback",
            )
            members.append((int(fields[0]), fields[2]))
        return tuple(members)

    def cancel_and_reap(self) -> None:
        with defer_control_signals():
            self._cancel_and_reap()

    def _cancel_and_reap(self) -> None:
        child = self.child
        if child is None:
            require(not self.cleanup_failed, "capture_group_not_gone")
            return
        if child.returncode is not None:
            self.may_signal_child = False
            self.cleanup_failed = True
            self.child = None
            if child.stdout is not None:
                child.stdout.close()
            raise PostgreSQLRefused("capture_child_already_reaped")
        cleanup_deadline = time.monotonic() + 5

        def unreaped_members() -> tuple[tuple[int, str], ...]:
            members = self.group_members(child.pid, deadline=cleanup_deadline)
            require(
                members
                and len({pid for pid, _state in members}) == len(members)
                and any(pid == child.pid for pid, _state in members),
                "capture_group_readback",
            )
            return members

        # Keep the leader unreaped until group termination to avoid PID reuse.
        try:
            members = unreaped_members()
        except BaseException:
            self.cleanup_failed = True
            members = None
        if self.may_signal_child and (
            members is None or len(members) != 1 or not members[0][1].startswith("Z")
        ):
            # Failed inspection must not suppress the known, unreaped child's
            # group cancellation. This signal cannot target a recycled PID.
            # At most one signal attempt per registered child. Even interrupted
            # or ambiguous wait cleanup may only reap/read, never signal again.
            self.may_signal_child = False
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except BaseException:
                self.cleanup_failed = True
        self.may_signal_child = False
        try:
            while not (
                members is not None
                and len(members) == 1
                and members[0][1].startswith("Z")
            ):
                require(time.monotonic() < cleanup_deadline, "capture_group_readback")
                members = unreaped_members()
                if len(members) != 1 or not members[0][1].startswith("Z"):
                    time.sleep(min(0.01, max(0, cleanup_deadline - time.monotonic())))
        except BaseException:
            # A later valid observation cannot erase any ambiguous pre-reap read.
            self.cleanup_failed = True
        try:
            child.wait(timeout=max(0.01, cleanup_deadline - time.monotonic()))
            # A later cleanup call MUST NOT signal this now-reaped PID again.
            self.child = None
        except BaseException:
            self.cleanup_failed = True
            if child.returncode is not None:
                self.child = None
            raise PostgreSQLRefused("capture_child_not_reaped") from None
        finally:
            if child.stdout is not None:
                child.stdout.close()
        try:
            if self.group_members(child.pid, deadline=cleanup_deadline):
                self.cleanup_failed = True
        except BaseException:
            self.cleanup_failed = True
        require(not self.cleanup_failed, "capture_group_not_gone")

    def run(
        self,
        argv: list[str],
        environment: dict[str, str],
        max_bytes: int,
        sink: Callable[[bytes], None],
        *,
        pass_fds: tuple[int, ...] = (),
    ) -> int:
        require(self.child is None, "capture_child_already_running")
        require(not self.cleanup_failed, "capture_group_not_gone")
        require(
            resource.getrlimit(resource.RLIMIT_CORE) == (0, 0),
            "core_dumps_not_disabled",
        )
        require(type(max_bytes) is int and 0 <= max_bytes <= MAX_BYTES, "output_limit")
        self.revalidate_runtime()
        self.remaining()
        try:
            # A deferred deadline/Ctrl-C is delivered only after the exact
            # Popen handle is registered inside this try/finally.
            with defer_control_signals():
                self.child = subprocess.Popen(
                    argv,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                    pass_fds=pass_fds,
                )
                self.may_signal_child = True
            child = self.child
            require(child.stdout is not None, "child_output_pipe")
            if child.stdout is None:
                raise PostgreSQLRefused("child_output_pipe")
            os.set_blocking(child.stdout.fileno(), False)
            size = 0
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    ready = selector.select(min(0.25, self.remaining()))
                    if not ready:
                        continue
                    chunk = os.read(child.stdout.fileno(), CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= max_bytes, "child_output_limit")
                    sink(chunk)
            # Observe exit without reaping. Keep this exact PID allocated until
            # cancellation has killed any remaining members of its process group.
            while True:
                status = os.waitid(
                    os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
                )
                if status is not None:
                    require(
                        status.si_pid == child.pid
                        and status.si_code == os.CLD_EXITED
                        and status.si_status == 0,
                        "postgresql_command_failed",
                    )
                    break
                time.sleep(min(0.01, self.remaining()))
            self.remaining()
        finally:
            # Even a successful leader cannot leave a hidden descendant behind.
            self.cancel_and_reap()
        self.remaining()
        return size


@dataclass(frozen=True)
class DumpResult:
    size: int
    sha256: str
    source_revision: str = REVISION


def _capture_with_verified_payload(
    credentials: Credentials,
    binding: PayloadBinding,
    children: BoundedChildren,
    revalidate_encrypted_binding: Callable[[], None],
    max_bytes: int = MAX_BYTES,
) -> DumpResult:
    """Human-only I/O, called solely inside the reviewed cleanup core's try/finally.

    The callbacks must actually check exact runtime closure and native encrypted
    mount/manifest/source binding. No stub callback is acceptable for live use.
    This module does not claim those external authority gates are implemented.
    """
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES, "archive_size_limit")
    require(0 < children.remaining() <= MAX_SECONDS, "capture_time_limit")
    environment = connection_environment(credentials)

    def read_schema() -> None:
        output = bytearray()
        children.run(
            [
                str(CLIENT_DIRECTORY / "psql"),
                "-X",
                "-w",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                SCHEMA_SQL,
            ],
            environment,
            4096,
            output.extend,
        )
        schema_observation(bytes(output))

    read_schema()
    revalidate_encrypted_binding()
    fd = open_exclusive_archive(binding)
    try:
        digest = hashlib.sha256()
        prefix = bytearray()

        def write(chunk: bytes) -> None:
            if len(prefix) < 5:
                prefix.extend(chunk[: 5 - len(prefix)])
            view = memoryview(chunk)
            while view:
                children.remaining()
                count = os.write(fd, view)
                require(count > 0, "archive_write_failed")
                view = view[count:]
            digest.update(chunk)

        size = children.run(
            [
                str(CLIENT_DIRECTORY / "pg_dump"),
                "--no-password",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--lock-wait-timeout=5s",
                "--dbname=staging",
            ],
            environment,
            max_bytes,
            write,
        )
        require(size > 5 and bytes(prefix) == b"PGDMP", "archive_custom_format")
        os.fsync(fd)
        check_archive_binding(binding, fd, size)
        # The reader uses this open descriptor, not a mutable pathname. No
        # connection flags or credentials are present in either restore command.
        local_environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        os.lseek(fd, 0, os.SEEK_SET)
        children.run(
            [
                str(CLIENT_DIRECTORY / "pg_restore"),
                "--no-owner",
                "--no-privileges",
                "--file=/dev/null",
                f"/dev/fd/{fd}",
            ],
            local_environment,
            0,
            lambda _chunk: None,
            pass_fds=(fd,),
        )
        os.lseek(fd, 0, os.SEEK_SET)
        output = bytearray()
        children.run(
            [
                str(CLIENT_DIRECTORY / "pg_restore"),
                "--data-only",
                "--table=alembic_version",
                "--file=-",
                f"/dev/fd/{fd}",
            ],
            local_environment,
            65536,
            output.extend,
            pass_fds=(fd,),
        )
        archive_revision(bytes(output))
        read_schema()
        revalidate_encrypted_binding()
        check_archive_binding(binding, fd, size)
        os.lseek(fd, 0, os.SEEK_SET)
        verified_digest = hashlib.sha256()
        verified_size = 0
        while True:
            children.remaining()
            chunk = os.read(fd, CHUNK)
            if not chunk:
                break
            verified_size += len(chunk)
            require(verified_size <= size, "archive_changed_during_validation")
            verified_digest.update(chunk)
        require(
            verified_size == size and verified_digest.digest() == digest.digest(),
            "archive_changed_during_validation",
        )
        check_archive_binding(binding, fd, size)
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        children.remaining()
        return DumpResult(size, digest.hexdigest())
    finally:
        os.close(fd)


def capture_with_verified_payload(
    credentials: Credentials,
    binding: PayloadBinding,
    children: BoundedChildren,
    revalidate_encrypted_binding: Callable[[], None],
    max_bytes: int = MAX_BYTES,
) -> DumpResult:
    """A fresh absolute capture deadline also covers writes/fsync/native checks.

    Caller still owns the reviewed identity/authority gates and human firewall
    finally path. No new timer may silently replace a caller's active timer.
    """
    with capture_deadline(children.deadline):
        return _capture_with_verified_payload(
            credentials, binding, children, revalidate_encrypted_binding, max_bytes
        )


if __name__ == "__main__":
    raise SystemExit("snapshot_pg_io_requires_separately_reviewed_human_launcher")
