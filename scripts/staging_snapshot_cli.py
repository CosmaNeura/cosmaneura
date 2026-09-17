"""Owned fixed CLI reads using a native-bound encrypted configuration COPY.

Human-session library only: never call live from an agent. No original-cache
writes, legacy nested wrapper, login, account selection mutation or resource
commands. Exact source/plan/handoff/identity authority is a separate loader gate.
"""

from __future__ import annotations

import time

from staging_snapshot_configuration import BoundConfiguration, ConfiguredSource
from staging_snapshot_identity import read_object
from staging_snapshot_pg_io import (
    BoundedChildren,
    capture_deadline,
    require_capture_process,
)
from staging_snapshot_runtime import SnapshotRuntime


class SnapshotCLIRefused(RuntimeError):
    pass


def arguments(operation: str) -> tuple[str, ...]:
    subscription = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
    if type(operation) is not str:
        raise SnapshotCLIRefused("snapshot_cli_refused")
    if operation == "account":
        return "account", "show", "--subscription", subscription
    if operation == "token":
        return (
            "account",
            "get-access-token",
            "--subscription",
            subscription,
            "--resource",
            "https://management.azure.com/",
        )
    if operation == "version":
        return ("version",)
    if operation == "cloud":
        return "cloud", "show", "--name", "AzureCloud"
    raise SnapshotCLIRefused("snapshot_cli_refused")


class OwnedAzureCLI:
    __slots__ = ("_runtime", "_copy")

    def __init__(self, runtime: SnapshotRuntime, configuration: BoundConfiguration):
        require_capture_process()
        if (
            type(runtime) is not SnapshotRuntime
            or type(configuration) is not BoundConfiguration
            or type(configuration.owner) is not ConfiguredSource
        ):
            raise SnapshotCLIRefused("snapshot_cli_refused")
        self._runtime, self._copy = runtime, configuration

    def _verify(self, deadline: float) -> None:
        if (
            type(self._runtime) is not SnapshotRuntime
            or type(self._copy) is not BoundConfiguration
            or type(self._copy.owner) is not ConfiguredSource
        ):
            raise SnapshotCLIRefused("snapshot_cli_refused")
        self._runtime.revalidate()
        self._copy.owner.validate_copy(self._copy, deadline=deadline)

    def read(self, operation: str, *, deadline: float) -> dict:
        try:
            command = arguments(operation)  # Closed before any filesystem/CLI I/O.
            require_capture_process()
            if (
                type(self._runtime) is not SnapshotRuntime
                or type(self._copy) is not BoundConfiguration
                or type(self._copy.owner) is not ConfiguredSource
            ):
                raise SnapshotCLIRefused("snapshot_cli_refused")
            if not 0 < deadline - time.monotonic() <= 120:
                raise SnapshotCLIRefused("snapshot_cli_refused")
            with capture_deadline(deadline):
                # No caller-selected binary, module search path, HOME, cloud,
                # library directory or extension source. Complete immutable
                # closure and private original/copy binding rechecked pre/post.
                root = self._runtime._root
                site = self._runtime.site_packages
                python_home = (
                    root
                    / "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14"
                )
                libraries = [
                    python_home,
                    *(
                        root / "opt" / name / "lib"
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
                environment = {
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/dev/null",
                    "LC_ALL": "C",
                    "LANG": "C",
                    "AZURE_CONFIG_DIR": str(self._copy.path),
                    "AZURE_EXTENSION_DIR": str(self._copy.path / "empty-extensions"),
                    "AZURE_EXTENSION_SYS_DIR": str(
                        self._copy.path / "empty-extensions"
                    ),
                    "AZURE_EXTENSION_DEV_SOURCES": "",
                    "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
                    "AZURE_CORE_COLLECT_TELEMETRY": "false",
                    "AZURE_LOGGING_ENABLE_LOG_FILE": "false",
                    "AZURE_CORE_USE_MSAL_HTTP_CACHE": "false",
                    "AZURE_CORE_NO_COLOR": "true",
                    "AZURE_CORE_DISABLE_CONFIRM_PROMPT": "true",
                    "AZ_INSTALLER": "HOMEBREW",
                    "NETRC": "/dev/null",
                    "OPENSSL_CONF": "/dev/null",
                    "OPENSSL_MODULES": str(root / "opt/openssl@3/lib/ossl-modules"),
                    "DYLD_LIBRARY_PATH": ":".join(map(str, libraries)),
                    "REQUESTS_CA_BUNDLE": str(site / "certifi/cacert.pem"),
                    "SSL_CERT_FILE": str(site / "certifi/cacert.pem"),
                }
                argv = [
                    str(self._runtime.python),
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='az';runpy.run_module('azure.cli',run_name='__main__')",
                    str(site),
                    *command,
                    "--only-show-errors",
                    "--output",
                    "json",
                ]
                output = bytearray()
                children = BoundedChildren(deadline, lambda: self._verify(deadline))
                children.run(argv, environment, 256 * 1024, output.extend)
                self._verify(deadline)
                return read_object(bytes(output), 256 * 1024)
        except BaseException:
            raise SnapshotCLIRefused("snapshot_cli_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_cli_requires_reviewed_human_bootstrap")
