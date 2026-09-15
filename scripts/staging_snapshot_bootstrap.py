"""Exact source/runtime startup and HUMAN-ONLY snapshot entrypoint.

Only bind_process is secretless startup; it does not establish source provenance,
review, plan/handoff or execution authority. Direct execution enters the human
composition after exact artifact gates and MUST NOT be invoked by an agent.
The independently reviewed human command must verify this loader and interpreter
before executing their bytes in a fresh clean process. Never import it into a
shared host to call bind_process: its seal is permanent even on later refusal.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import math
import os
import resource
import signal
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Any

RUNTIME_ROOT = Path(
    "/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/"
    ".trusted-runtime-bin/homebrew"
)
# Independently DERIVED from the normally merged validator, not a current-copy
# digest passed back by a caller. See the flight plan's exact derivation pins.
EXPECTED_RUNTIME_SHA256 = (
    "4dc3443055fef6f2112c96178fd86df3bf5a675ccdfaac6cd597f2bda13ebb0f"
)
SOURCE_PINS = (
    (
        "staging_snapshot_capture",
        "4ae347ec1f710a89d8649944444d202da9b4dedd0342b3b5ac53c786cdb99081",
    ),
    (
        "staging_snapshot_pg_io",
        "83957b4aa2e5d2d8bbbf70453a4e6f7d8ce3678bc3f1df4f4211431c9f504134",
    ),
    (
        "staging_snapshot_native_files",
        "de6874c3cdba6cf91cfd8885a8b17e38260e4eab57cb907610010d0b616a9c0e",
    ),
    (
        "staging_snapshot_runtime",
        "2ec8a9168b65fd5db04227b65acc3e58db17c39a2a644af607970795e8d9d28e",
    ),
    (
        "staging_snapshot_postgresql_runtime",
        "41f7c8b34136e53bc812c8412cb6689bc3fc530b4e8a4ecfd96b196644a34c50",
    ),
    (
        "staging_encrypted_workspace",
        "eaa2d745dcb3bad28452d5db3854e96b6a3c9f37da614c7201d5e6faca267960",
    ),
    (
        "staging_snapshot_identity",
        "3acfc0ff734ad722db1447be7636679d92d18c17f886f57a7d8edee1601c5f11",
    ),
    (
        "staging_snapshot_workspace",
        "a6476f3b35efe3e9e92d7087fa3ef12ed6943bfc71603ff9bac2fa55c9a68912",
    ),
    (
        "staging_snapshot_configuration",
        "36797128a1c367dcc8d78d12c40de36cbd51c477ede18334f520072ed09d85ac",
    ),
    (
        "staging_snapshot_cli",
        "828a1adf04b5da73a907fba1be78f5a0da742a4b74a258f8a65a33bbd459b1c6",
    ),
    (
        "staging_snapshot_session",
        "5b186ebc8dc8560a212a82ee64f6d521f0494c89595471c58e30ceb673b1e15b",
    ),
    (
        "staging_snapshot_azure",
        "c49a81825ce04f8aeeee657a257f6a233c2f9f9fba4a08c59d5d12a3ea0c97cc",
    ),
    (
        "staging_snapshot_public_ip",
        "05dac0cbcb6fc2c9922162298698886c5a9642efa3d2b7ab5fb199b93d47ad66",
    ),
    (
        "staging_snapshot_console",
        "60894b4d79fb84cf502e211c03c6ac1a17b8036c8b6131ef0634f2dc7d8fe86d",
    ),
    (
        "staging_snapshot_operations",
        "9f77f510be69032918dcf64cb8c623ca4e62dbd59b74c27dcffd04a023d35897",
    ),
    (
        "staging_snapshot_artifacts",
        "c2fd82b58605887eddbc8af0c31ac93e7ae59c89661c4da07b52ffec297e7ee0",
    ),
    (
        "staging_snapshot_human",
        "df2b9bf9984153467073327f75defef5e0cbf18db73f7d09f4896d26ef729858",
    ),
)
PRELOAD = (
    "argparse",
    "base64",
    "email.utils",
    "encodings.idna",
    "errno",
    "ipaddress",
    "json",
    "locale",
    "plistlib",
    "pwd",
    "re",
    "selectors",
    "ssl",
    "tempfile",
    "termios",
    "threading",
    "typing",
    "urllib.error",
    "urllib.request",
    "uuid",
)
_started = False


class SnapshotBootstrapRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SnapshotBootstrapRefused("snapshot_bootstrap_refused")


def python_prefix() -> Path:
    return RUNTIME_ROOT / (
        "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14"
    )


def startup_environment() -> dict[str, str]:
    libraries = [
        python_prefix(),
        *(
            RUNTIME_ROOT / "opt" / name / "lib"
            for name in (
                "libsodium",
                "libyaml",
                "lz4",
                "mpdecimal",
                "openssl@3",
                "readline",
                "sqlite",
                "xz",
                "zstd",
            )
        ),
    ]
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/dev/null",
        "LANG": "C",
        "LC_ALL": "C",
        # CoreFoundation otherwise inserts its user text-encoding preference
        # during native loading. Set one explicit bootstrap-owned value, not
        # an observed value accepted as authority or an ambient-home read.
        "__CF_USER_TEXT_ENCODING": f"0x{os.geteuid():X}:0x0:0x0",
        "OPENSSL_CONF": "/dev/null",
        "OPENSSL_MODULES": str(RUNTIME_ROOT / "opt/openssl@3/lib/ossl-modules"),
        "DYLD_LIBRARY_PATH": ":".join(map(str, libraries)),
    }


def parent_interpreter() -> None:
    prefix = python_prefix()
    require(
        sys.platform == "darwin"
        and os.uname().machine == "arm64"
        and os.geteuid() != 0
        and os.getuid() == os.geteuid()
        and sys.implementation.name == "cpython"
        and sys.version_info[:3] == (3, 14, 7)
        and sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.dont_write_bytecode
        and not sys.flags.optimize
        and sys.gettrace() is None
        and sys.getprofile() is None
        and sys.executable == str(prefix / "bin/python3.14")
        and {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}
        == {str(prefix)}
        and sys.path
        == [
            str(prefix / part)
            for part in (
                "lib/python314.zip",
                "lib/python3.14",
                "lib/python3.14/lib-dynload",
            )
        ]
    )


@contextmanager
def startup_deadline(deadline: float):
    """Pre-seal guard; never shares or resets the later capture timer.

    This phase only reads fixed public source and preloads trusted libraries.
    The sealed guard takes over afterwards with the SAME absolute deadline.
    """
    require(math.isfinite(deadline) and 0 < deadline - time.monotonic() <= 90)
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    require(signal.SIGALRM not in mask and signal.SIGALRM not in signal.sigpending())
    previous = signal.getsignal(signal.SIGALRM)
    expired, closing = False, False

    def expire(_number, _frame):
        nonlocal expired
        expired = True
        if not closing:
            raise SnapshotBootstrapRefused("snapshot_bootstrap_refused")

    try:
        signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, max(0.000001, deadline - time.monotonic()))
        yield
    finally:
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
                        if signal.SIGALRM in signal.sigpending():
                            expired = True
                            signal.sigwait({signal.SIGALRM})
                    finally:
                        try:
                            signal.signal(signal.SIGALRM, previous)
                        finally:
                            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        require(not expired and time.monotonic() < deadline)


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


def initial_source(path: Path, expected: str) -> bytes:
    """Hash fixed non-secret source BEFORE exec; native ACL check follows seal.

    No path-based import, pyc, caller module name or plan-selected digest.
    Even redirected pre-seal public reads cannot substitute executable bytes.
    """
    require(path.is_absolute() and path == Path(os.path.abspath(path)))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid())
        require(before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o400)
        require(0 < before.st_size <= 128 * 1024)
        data = bytearray()
        while len(data) < before.st_size:
            chunk = os.read(fd, min(65536, before.st_size - len(data)))
            require(chunk)
            data.extend(chunk)
        require(not os.read(fd, 1))
        require(
            file_identity(before)
            == file_identity(os.fstat(fd))
            == file_identity(path.lstat())
        )
        require(hashlib.sha256(data).hexdigest() == expected)
        return bytes(data)
    finally:
        os.close(fd)


def load_sources(root: Path) -> dict[str, ModuleType]:
    require(not any(name in sys.modules for name, _ in SOURCE_PINS))
    # Read/check the ENTIRE closed set before executing its first module.
    contents = [
        (name, initial_source(root / (name + ".py"), sha)) for name, sha in SOURCE_PINS
    ]
    modules = {}
    for name, raw in contents:
        module = ModuleType(name)
        module.__file__ = str(root / (name + ".py"))
        module.__package__ = ""
        sys.modules[name] = module  # dataclasses require their actual module.
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), vars(module))
        require(sys.modules[name] is module)
        modules[name] = module
    return modules


class NativeImages:
    """Resolve dyld read-only symbols BEFORE seal; no lazy ctypes afterwards."""

    def __init__(self):
        self.library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        self.count = self.library._dyld_image_count
        self.count.argtypes, self.count.restype = (), ctypes.c_uint32
        self.name = self.library._dyld_get_image_name
        self.name.argtypes, self.name.restype = (ctypes.c_uint32,), ctypes.c_char_p

    def observe(self) -> tuple[str, ...]:
        count = self.count()
        require(1 <= count <= 1024)
        result = []
        for index in range(count):
            raw = self.name(index)
            require(raw is not None and 0 < len(raw) <= 4096)
            path = Path(raw.decode("utf-8"))
            require(path.is_absolute() and path == Path(os.path.abspath(path)))
            require(
                path.is_relative_to(RUNTIME_ROOT)
                or path.is_relative_to("/usr/lib")
                or path.is_relative_to("/System/Library")
            )
            if path.is_relative_to(RUNTIME_ROOT):
                require(path.resolve(strict=True).is_relative_to(RUNTIME_ROOT))
            result.append(str(path))
        require(self.count() == count and len(set(result)) == len(result))
        return tuple(result)


def module_origins(modules: dict[str, ModuleType], loader: Path) -> tuple:
    expected = {name: module.__file__ for name, module in modules.items()}
    require(getattr(sys.modules["__main__"], "__file__", None) == str(loader))
    origins = []
    for name, module in tuple(sys.modules.items()):
        require(type(module) is ModuleType)
        path = getattr(module, "__file__", None)
        if name in expected:
            require(module is modules[name] and path == expected[name])
        elif name == "__main__":
            require(path == str(loader))
        elif path is not None:
            require(type(path) is str and Path(path).is_relative_to(python_prefix()))
            require(Path(path).resolve(strict=True).is_relative_to(python_prefix()))
        elif name in {
            "pyexpat.errors",
            "pyexpat.model",
            "xml.parsers.expat.errors",
            "xml.parsers.expat.model",
        }:
            # The pinned pyexpat extension creates these two data modules and
            # aliases; they have no independent file/spec. Require exact parent
            # object identity, not a blanket exception for origin-less modules.
            require(
                module
                is getattr(sys.modules.get("pyexpat"), name.rsplit(".", 1)[1], None)
            )
        else:
            require(
                getattr(getattr(module, "__spec__", None), "origin", None)
                in {"built-in", "frozen"}
            )
        origins.append((name, id(module), path))
    return tuple(sorted(origins))


def source_bindings(modules: dict[str, ModuleType]) -> tuple:
    """Detect rebinding modules, functions, code objects and class methods.

    This is an invariant for trusted pinned code, NOT an in-process sandbox
    against malicious same-UID/native code or mutation of arbitrary objects.
    """
    result = []
    for module_name, module in sorted(modules.items()):
        require(sys.modules.get(module_name) is module)
        for name, value in sorted(vars(module).items()):
            result.append(
                (
                    module_name,
                    name,
                    id(value),
                    id(value.__code__) if type(value) is FunctionType else 0,
                )
            )
            if isinstance(value, type) and value.__module__ == module_name:
                for member, item in sorted(vars(value).items()):
                    result.append(
                        (
                            module_name,
                            name + "." + member,
                            id(item),
                            id(item.__code__) if type(item) is FunctionType else 0,
                        )
                    )
    return tuple(result)


class SourceRuntimeBinding:
    """Prepared only by bind_process; no credential or action authority."""

    pg: ModuleType
    files: Any
    modules: dict[str, ModuleType]
    integrity_modules: dict[str, ModuleType]
    root: Path
    loader: Path
    loader_sha256: str
    loader_source: Any
    sources: dict[str, Any]
    native: NativeImages
    images: tuple[str, ...]
    environment: dict[str, str]
    origins: tuple
    bindings: tuple
    runtime: Any
    postgresql: Any

    def revalidate(self) -> None:
        try:
            require(set(self.modules) == {name for name, _ in SOURCE_PINS})
            require(source_bindings(self.integrity_modules) == self.bindings)
            require(
                all(
                    self.modules[name] is sys.modules.get(name)
                    for name, _ in SOURCE_PINS
                )
            )
            require(self.pg is self.modules["staging_snapshot_pg_io"])
            require(
                type(self.files)
                is self.modules["staging_snapshot_native_files"].NativeFiles
            )
            require(
                type(self.runtime)
                is self.modules["staging_snapshot_runtime"].SnapshotRuntime
            )
            require(type(self.native) is NativeImages)
            require(
                type(self.postgresql)
                is self.modules["staging_snapshot_postgresql_runtime"].PostgreSQLRuntime
            )
            require(self.postgresql._files is self.files)
            require(
                self.runtime._files is self.files and self.runtime._root == RUNTIME_ROOT
            )
            require(self.runtime._expected == EXPECTED_RUNTIME_SHA256)
            self.pg.require_capture_process()
            require(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
            parent_interpreter()
            require(dict(os.environ) == self.environment)
            require(module_origins(self.modules, self.loader) == self.origins)
            require(self.native.observe() == self.images)
            for name, sha in SOURCE_PINS:
                current = self.files.read(
                    self.root / (name + ".py"), mode=0o400, maximum=128 * 1024
                )
                require(
                    current == self.sources[name]
                    and hashlib.sha256(current.content).hexdigest() == sha
                )
            require(
                self.files.read(self.loader, mode=0o400, maximum=128 * 1024)
                == self.loader_source
            )
            self.runtime.revalidate()
            self.postgresql.revalidate()
            require(self.native.observe() == self.images)
        except BaseException:
            raise SnapshotBootstrapRefused("snapshot_bootstrap_refused") from None


def bind_process(*, loader_sha256: str, deadline: float) -> SourceRuntimeBinding:
    """One attempt in the fresh, independently hash-checked human loader.

    Expensive source/runtime preparation is outside the firewall window. The
    caller STILL owes protected-merge/source review and fresh plan/handoff
    authority before constructing secret-bearing objects or showing actions.
    """
    global _started
    try:
        require(not _started)
        _started = True  # Failed partial startup cannot retry in this process.
        parent_interpreter()
        require(dict(os.environ) == startup_environment())
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        require(resource.getrlimit(resource.RLIMIT_CORE) == (0, 0))
        loader = Path(__file__)
        require(loader.name == "staging_snapshot_bootstrap.py")
        with startup_deadline(deadline):
            loader_raw = initial_source(loader, loader_sha256)
            for name in PRELOAD:
                importlib.import_module(name)
            modules = load_sources(loader.parent)
            files = modules["staging_snapshot_native_files"].NativeFiles()
            native = NativeImages()
            # Initialize OpenSSL's built-in provider before recording images;
            # no default CA search or network operation occurs.
            ssl = sys.modules["ssl"]
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            images = native.observe()
            pg = modules["staging_snapshot_pg_io"]
            pg.seal_capture_process()
        # These are exact bootstrap-owned values, not ambient user overrides.
        # Remove them only after binding their loader effect. Session transport
        # continues to reject ANY such setting; it accepts no new exception.
        require(dict(os.environ) == startup_environment())
        for name in ("DYLD_LIBRARY_PATH", "OPENSSL_CONF", "OPENSSL_MODULES"):
            del os.environ[name]
        binding = SourceRuntimeBinding()
        binding.pg, binding.files, binding.modules = pg, files, modules
        binding.integrity_modules = {**modules, "__main__": sys.modules["__main__"]}
        binding.root, binding.loader = loader.parent, loader
        binding.loader_sha256 = loader_sha256
        binding.native, binding.images = native, images
        binding.environment = dict(os.environ)
        with pg.capture_deadline(deadline):
            binding.sources = {}
            for name, sha in SOURCE_PINS:
                observed = files.read(
                    loader.parent / (name + ".py"), mode=0o400, maximum=128 * 1024
                )
                require(hashlib.sha256(observed.content).hexdigest() == sha)
                binding.sources[name] = observed
            binding.loader_source = files.read(loader, mode=0o400, maximum=128 * 1024)
            require(binding.loader_source.content == loader_raw)
            binding.origins = module_origins(modules, loader)
            binding.bindings = source_bindings(binding.integrity_modules)
            require(native.observe() == images)
        binding.runtime = modules["staging_snapshot_runtime"].SnapshotRuntime(
            files, RUNTIME_ROOT, EXPECTED_RUNTIME_SHA256
        )
        binding.runtime.bind(deadline=deadline)
        binding.postgresql = modules[
            "staging_snapshot_postgresql_runtime"
        ].PostgreSQLRuntime(files)
        binding.postgresql.bind(deadline=deadline)
        with pg.capture_deadline(deadline):
            binding.revalidate()
        return binding
    except BaseException:
        raise SnapshotBootstrapRefused("snapshot_bootstrap_refused") from None


def main() -> int:
    """HUMAN-ONLY entry; fixtures may replace every consumer dependency.

    The exact reviewed Terminal command must independently hash-check this
    loader and interpreter BEFORE invocation. Arguments are the loader hash,
    packet directory, handoff hash and independently reviewed summary hash.
    Explicit `prepare` before those four arguments selects READ-ONLY preparation:
    the last two hashes then pin preparation.json and the source-review summary.
    Both modes are HUMAN-ONLY. No capture plan/receipt/authority is generated;
    preparation publishes only the minimized public observation's digest/size.
    """
    human = None
    try:
        preparing = len(sys.argv) == 6 and sys.argv[1] == "prepare"
        require(preparing or len(sys.argv) == 5)
        loader_sha, packet, handoff_sha, review_sha = (
            sys.argv[2:] if preparing else sys.argv[1:]
        )
        require(
            all(
                len(value) == 64 and all(char in "0123456789abcdef" for char in value)
                for value in (loader_sha, handoff_sha, review_sha)
            )
        )
        require(0 < len(packet) <= 4096 and Path(packet).is_absolute())
        os.umask(0o077)  # This dedicated process only; never modify the user's shell.
        binding = bind_process(loader_sha256=loader_sha, deadline=time.monotonic() + 90)
        human = binding.modules["staging_snapshot_human"]
        if preparing:
            result = human.run_human_preparation(
                binding, Path(packet), handoff_sha, review_sha
            )
            require(
                type(result) is dict
                and result.get("status") == "read_only_preparation_observed"
            )
            require(
                result.get("execution_authorized") is False
                and result.get("deployment_authorized") is False
            )
            digest, size = (
                result.get("observation_sha256"),
                result.get("observation_bytes"),
            )
            require(
                type(digest) is str
                and len(digest) == 64
                and all(char in "0123456789abcdef" for char in digest)
            )
            require(type(size) is int and 0 < size <= 16384)
            print(
                "staging_snapshot_prepared "
                + sys.modules["json"].dumps(
                    {
                        "observation_sha256": digest,
                        "observation_bytes": size,
                        "execution_authorized": False,
                        "deployment_authorized": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        result = human.run_human_snapshot(
            binding, Path(packet), handoff_sha, review_sha
        )
        require(type(result) is dict)
        require(result.get("status") == "captured_human_closure_observed")
        require(result.get("deployment_authorized") is False)
        require(result.get("restore_or_migration_accepted") is False)
        require(result.get("new_rule_absent") is True)
        require(result.get("preexisting_rules_unchanged") is True)
        digest = result.get("archive_sha256")
        require(
            type(digest) is str
            and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
        )
        size = result.get("archive_bytes")
        require(type(size) is int and 0 < size <= 1024**3)
        # Only this fixed public result is printable. Never serialize arbitrary
        # exceptions, provider responses, identities, credentials or dump bytes.
        print(
            "staging_snapshot_captured "
            + sys.modules["json"].dumps(
                {
                    "archive_sha256": digest,
                    "archive_bytes": size,
                    "human_rule_removal_observed": True,
                    "restore_or_migration_accepted": False,
                    "deployment_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except BaseException as error:
        code = "snapshot_bootstrap_or_preparation_refused"
        if (
            human is not None
            and type(error) is human.SnapshotHumanRefused
            and len(error.args) == 1
            and type(error.args[0]) is str
            and error.args[0] in human.REFUSAL_CODES
        ):
            code = error.args[0]
        try:
            sys.stderr.write("staging_snapshot_refused:" + code + "\n")
        except BaseException:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
