"""Invented build/image/authority fixtures; all Docker and Nixpacks calls mocked."""

import importlib.util
import io
import json
import os
import signal
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    m = importlib.import_module("staging_human_build_executor")
    process = importlib.import_module("staging_human_build_process")

SPEC = importlib.util.spec_from_file_location(
    "build_fixture_definitions",
    ROOT / "tests/infrastructure/test_staging_human_build_inputs.py",
)
assert SPEC and SPEC.loader
fixture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture
SPEC.loader.exec_module(fixture)
CONFIG_BYTES = b'{"architecture":"amd64","rootfs":{"type":"layers","diff_ids":[]}}'
APP_ID = "sha256:" + m.sha(CONFIG_BYTES)
JS_ID = "sha256:" + "b" * 64
CONTAINER = "c" * 64


def tar(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, raw, kind in entries:
            info = tarfile.TarInfo(name)
            info.type, info.mode = kind, 0o644
            info.size = len(raw) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "/invented/outside"
            archive.addfile(info, io.BytesIO(raw) if info.size else None)
    return output.getvalue()


def assets():
    return tar(
        [
            ("client/", b"", tarfile.DIRTYPE),
            ("client/index.html", b"<html>synthetic fixture</html>", tarfile.REGTYPE),
            ("client/assets/app.js", b"synthetic javascript", tarfile.REGTYPE),
        ]
    )


def image_archive(config=CONFIG_BYTES):
    name = APP_ID.removeprefix("sha256:") + ".json"
    manifest = m.canonical(
        [{"Config": name, "RepoTags": [], "Layers": ["layer/layer.tar"]}]
    )
    return tar(
        [
            ("manifest.json", manifest, tarfile.REGTYPE),
            (name, config, tarfile.REGTYPE),
            ("layer/layer.tar", b"INVENTED_NOT_AN_EXECUTABLE_LAYER", tarfile.REGTYPE),
        ]
    )


def image(prepared, javascript):
    environment = dict(m.inputs.SHARED_ENVIRONMENT)
    environment.update(
        BUILD_COMMIT=prepared.inputs.candidate_sha,
        BUILD_CREATED_AT=prepared.inputs.created_at,
    )
    environment.update(
        dict(prepared.frontend) if javascript else {"PYTHON_ENV": "staging"}
    )
    if javascript:
        environment.update(VITE_BUILD_COMMIT=prepared.inputs.candidate_sha, CI="true")
    return {
        "Id": JS_ID if javascript else APP_ID,
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "Volumes": None,
            "Env": [k + "=" + v for k, v in environment.items()],
            "Labels": {
                "org.opencontainers.image.revision": prepared.inputs.candidate_sha,
                "org.opencontainers.image.created": prepared.inputs.created_at,
                "org.opencontainers.image.source": m.inputs.SOURCE_REPOSITORY,
            },
        },
    }


class FakeProcesses:
    """A protocol fixture only; it cannot execute an OS process."""

    def __init__(self, owner, deadline, validate):
        self.owner, self.validate = owner, validate
        self.child, self.cleanup_failed = None, False

    def run(self, argv, environment, maximum, sink, *, cwd, merge_stderr=False):
        self.validate()
        self.owner.calls.append(
            (list(argv), dict(environment), maximum, cwd, merge_stderr)
        )
        if argv[0].endswith("/nixpacks"):
            name = argv[argv.index("--name") + 1]
            if self.owner.failure == "build":
                raise RuntimeError("invented build failure")
            self.owner.images[name] = name.endswith("-javascript")
            output = b"invented build log\n"
            if self.owner.failure == "source_change":
                (self.owner.prepared.root / "source/web/package.json").write_bytes(
                    b"changed"
                )
            if self.owner.failure == "engine_change":
                self.owner.engine_changed = True
        else:
            args = argv[1:]
            key = tuple(args[:2])
            if args[0] == "info":
                output = m.canonical(
                    {
                        "ID": "changed"
                        if self.owner.engine_changed
                        else "invented-engine",
                        "ServerVersion": "29.5.2",
                        "OSType": "linux",
                        "Architecture": "aarch64",
                    }
                )
            elif args[0] == "version":
                output = b"29.7.2/29.5.2\n"
            elif key == ("image", "ls"):
                name = args[-1].removeprefix("reference=")
                output = b"existing\n" if name in self.owner.images else b""
            elif key == ("image", "inspect"):
                reference = args[-1]
                javascript = reference == JS_ID or self.owner.images.get(
                    reference, False
                )
                value = image(self.owner.prepared, javascript)
                if self.owner.failure == "bad_image":
                    value["Config"]["Env"].append("GH_TOKEN=invented")
                output = m.canonical([value])
            elif key == ("container", "ls"):
                output = (
                    (CONTAINER + "\n").encode() if self.owner.container_exists else b""
                )
            elif key == ("container", "create"):
                self.owner.container_exists = True
                if self.owner.failure == "create":
                    raise RuntimeError("invented ambiguous create")
                output = (CONTAINER + "\n").encode()
            elif key == ("container", "inspect"):
                output = m.canonical(
                    [
                        {
                            "Id": CONTAINER,
                            "Image": JS_ID,
                            "Name": "/" + self.owner.prepared.extraction_container,
                            "State": {
                                "Status": "created",
                                "Running": self.owner.failure == "running",
                                "Pid": 0,
                            },
                            "HostConfig": {
                                "Binds": None,
                                "ReadonlyRootfs": True,
                                "NetworkMode": "none",
                            },
                            "Mounts": [],
                            "Config": {
                                "Labels": {
                                    "cosmaneura.build.owner": self.owner.prepared.root.name
                                }
                            },
                        }
                    ]
                )
            elif key == ("container", "cp"):
                output = (
                    b"invalid archive" if self.owner.failure == "assets" else assets()
                )
            elif key == ("container", "rm"):
                if self.owner.failure == "remove":
                    raise RuntimeError("invented uncertain removal")
                self.owner.container_exists = False
                output = (CONTAINER + "\n").encode()
            elif key == ("image", "save"):
                if self.owner.failure == "save":
                    sink(b"retained partial archive")
                    raise RuntimeError("invented export failure")
                output = image_archive(
                    b"wrong config" if self.owner.failure == "archive" else CONFIG_BYTES
                )
            else:
                raise AssertionError("unexpected Docker command: " + repr(args))
        if len(output) > maximum:
            raise RuntimeError("invented output limit")
        sink(output)
        return len(output)


class BuildTests(unittest.TestCase):
    def test_builder_uses_dedicated_staging_release_engine(self):
        self.assertEqual(
            m.SOCKET, Path("/Users/jw/.colima/staging-release/docker.sock")
        )

    def test_created_container_id_survives_post_command_failures(self):
        original = FakeProcesses.run
        for mode in ("source", "deadline", "malformed"):
            with self.subTest(mode=mode):
                self.setUp()

                def run(owner, argv, environment, maximum, sink, **kwargs):
                    creating = argv[1:3] == ["container", "create"]
                    receiver = (
                        (lambda _: sink(b"not-an-image-id\n"))
                        if creating and mode == "malformed"
                        else sink
                    )
                    result = original(
                        owner, argv, environment, maximum, receiver, **kwargs
                    )
                    if creating and mode == "source":
                        (self.prepared.root / "source/web/package.json").write_bytes(
                            b"changed"
                        )
                    if creating and mode == "deadline":
                        raise process.BuildProcessRefused(
                            "invented deadline after stdout"
                        )
                    return result

                with patch.object(FakeProcesses, "run", run):
                    with self.assertRaises(m.BuildExecutionRefused) as caught:
                        self.build()
                record = json.loads(
                    (self.prepared.root / "artifacts/build-failure.json").read_bytes()
                )
                self.assertEqual(
                    record["container_id"], None if mode == "malformed" else CONTAINER
                )
                self.assertEqual(
                    record["container_name"], self.prepared.extraction_container
                )
                self.assertTrue(
                    record["container_id_requires_fresh_ownership_verification"]
                )
                self.assertTrue(caught.exception.failure_evidence_written)
                self.assertTrue(self.container_exists)
                self.assertFalse(
                    any(row[0][1:3] == ["container", "rm"] for row in self.calls)
                )

    def test_failure_evidence_write_failure_cannot_hide_canonical_refusal(self):
        for error in (
            OSError("invented full disk"),
            FileExistsError("invented collision"),
            KeyboardInterrupt(),
        ):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.failure = "build"
                with patch.object(
                    m.inputs, "write_exclusive", side_effect=error
                ) as writer:
                    with self.assertRaises(m.BuildExecutionRefused) as caught:
                        self.build()
                self.assertEqual(
                    str(caught.exception), "staging_build_execution_refused"
                )
                self.assertFalse(caught.exception.failure_evidence_written)
                self.assertEqual(writer.call_count, 1)
                self.assertFalse(
                    (self.prepared.root / "artifacts/build-failure.json").exists()
                )
                self.assertFalse(
                    (self.prepared.root / "artifacts/build-result.json").exists()
                )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cosmaneura-build-unit-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.parent.chmod(0o700)
        self.source = fixture.archive(fixture.source_files())
        public = fixture.encoded(fixture.configuration())
        data = m.inputs.BuildInputs(
            "a" * 40, m.sha(self.source), m.sha(public), "2030-01-01T00:00:00Z"
        )
        with patch.object(
            m.inputs, "native_builder_bytes", return_value=b"INVENTED_NOT_EXECUTABLE"
        ):
            self.prepared = m.inputs.prepare_build_inputs(
                self.parent,
                data,
                source_archive=self.source,
                frontend_json=public,
                native_archive=b"invented",
            )
        self.engine = m.EngineBinding(m.sha(b"invented-engine"), 1, 2)
        self.calls, self.images = [], {}
        self.failure = None
        self.container_exists = self.engine_changed = False
        self.authority_calls = 0
        self.finalized = False

    def authority(self):
        self.authority_calls += 1

    def finalize(self, _archive):
        self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.finalized = True

    def build(self):
        with (
            patch.object(m, "tool_check"),
            patch.object(
                m.shutil, "disk_usage", return_value=SimpleNamespace(free=32 * 1024**3)
            ),
            patch.object(
                m,
                "BuildProcesses",
                side_effect=lambda deadline, validate: FakeProcesses(
                    self, deadline, validate
                ),
            ),
            patch.dict(
                os.environ,
                {
                    "GH_TOKEN": "invented-ambient",
                    "DOCKER_CONTEXT": "wrong",
                    "BASH_ENV": "/invented",
                },
                clear=False,
            ),
        ):
            return m.build_candidate(
                self.prepared,
                self.source,
                engine=self.engine,
                validate_authority=self.authority,
                finalize_archive=self.finalize,
                seconds=60,
            )

    def test_complete_mock_pipeline_exports_bound_archive_without_credentials_or_publication(
        self,
    ):
        result = self.build()
        self.assertEqual(result.image_id, APP_ID)
        self.assertEqual(result.sha256, m.sha(result.path.read_bytes()))
        self.assertEqual(result.candidate_sha, "a" * 40)
        self.assertTrue(self.finalized)
        metadata = json.loads(result.metadata_path.read_bytes())
        self.assertFalse(metadata["publication_authorized"])
        self.assertFalse(metadata["deployment_authorized"])
        self.assertTrue(metadata["owned_extraction_container_removed"])
        # One pre-builder check plus five explicit build phase boundaries.
        # Deterministic local integrity is still checked around every child.
        self.assertEqual(self.authority_calls, 6)
        native = [row for row in self.calls if row[0][0].endswith("/nixpacks")]
        self.assertEqual(len(native), 2)
        self.assertTrue(all("--no-cache" in row[0] for row in native))
        self.assertTrue(all(row[4] for row in native))
        for argv, environment, _, cwd, _ in self.calls:
            self.assertEqual(
                set(environment),
                {
                    "HOME",
                    "BUILDX_NO_DEFAULT_ATTESTATIONS",
                    "DOCKER_CONFIG",
                    "DOCKER_HOST",
                    "PATH",
                    "TMPDIR",
                    "LC_ALL",
                    "LANG",
                },
            )
            self.assertNotIn("GH_TOKEN", environment)
            self.assertNotIn("DOCKER_CONTEXT", environment)
            self.assertNotIn("BASH_ENV", environment)
            self.assertEqual(cwd, self.prepared.root / "source")
            self.assertTrue(environment["HOME"].startswith(str(self.prepared.root)))
            self.assertEqual(environment["BUILDX_NO_DEFAULT_ATTESTATIONS"], "1")
            self.assertNotIn("push", argv)
            self.assertNotIn("login", argv)
            self.assertNotIn("start", argv)
            self.assertNotIn("--force", argv)
        self.assertEqual(
            (self.prepared.root / "source/public/index.html").read_bytes(),
            b"<html>synthetic fixture</html>",
        )
        mutations = [row[0][1:3] for row in self.calls if row[0][0] == str(m.DOCKER)]
        self.assertEqual(mutations.count(["container", "create"]), 1)
        self.assertEqual(mutations.count(["container", "rm"]), 1)
        self.assertEqual(mutations.count(["image", "save"]), 1)

    def test_existing_image_refuses_without_build_or_deletion(self):
        self.images[self.prepared.image] = False
        with self.assertRaises(m.BuildExecutionRefused):
            self.build()
        self.assertFalse(any(row[0][0].endswith("/nixpacks") for row in self.calls))
        self.assertTrue(self.images)

    def test_existing_container_refuses_without_mutation(self):
        self.container_exists = True
        with self.assertRaises(m.BuildExecutionRefused):
            self.build()
        self.assertTrue(self.container_exists)
        self.assertFalse(
            any(row[0][1:3] == ["container", "create"] for row in self.calls)
        )

    def test_failure_stages_preserve_evidence_and_never_retry_or_publish(self):
        for failure in (
            "build",
            "create",
            "running",
            "assets",
            "remove",
            "save",
            "archive",
            "bad_image",
            "source_change",
            "engine_change",
        ):
            with self.subTest(phase=failure):
                self.setUp()
                self.failure = failure
                with self.assertRaises(m.BuildExecutionRefused):
                    self.build()
                record = json.loads(
                    (self.prepared.root / "artifacts/build-failure.json").read_bytes()
                )
                self.assertFalse(record["build_accepted"])
                self.assertFalse(record["automatic_retry_allowed"])
                self.assertTrue(record["daemon_build_state_may_be_unknown"])
                self.assertFalse(
                    (self.prepared.root / "artifacts/build-result.json").exists()
                )
                self.assertFalse(
                    any("push" in row[0] or "login" in row[0] for row in self.calls)
                )
                if failure == "create":
                    self.assertTrue(self.container_exists)
                    self.assertIsNone(record["container_id"])
                    self.assertFalse(
                        any(row[0][1:3] == ["container", "rm"] for row in self.calls)
                    )
                if failure == "save":
                    self.assertEqual(
                        (
                            self.prepared.root / "artifacts/application-image.tar"
                        ).read_bytes(),
                        b"retained partial archive",
                    )

    def test_changed_source_file_mode_frontend_or_empty_directory_refuses(self):
        for kind in ("contents", "mode", "frontend", "directory", "manifest"):
            with self.subTest(kind=kind):
                self.setUp()
                path = self.prepared.root / "source/web/package.json"
                if kind == "contents":
                    path.write_bytes(b"changed")
                elif kind == "mode":
                    path.chmod(0o644)
                elif kind == "frontend":
                    (self.prepared.root / "frontend.json").chmod(0o600)
                elif kind == "directory":
                    (self.prepared.root / "source/unexpected").mkdir(mode=0o700)
                else:
                    self.prepared = replace(self.prepared, files=())
                with self.assertRaises(
                    (m.BuildExecutionRefused, m.inputs.BuildInputRefused)
                ):
                    m.source_check(self.prepared, self.source)

    def test_changed_source_archive_and_public_manifest_refuse(self):
        with self.assertRaises(m.inputs.BuildInputRefused):
            m.source_check(self.prepared, self.source + b"changed")
        with self.assertRaises(m.BuildExecutionRefused):
            m.source_check(
                self.prepared, self.source, (("web/package.json", "a" * 64),)
            )

    def test_authority_refusal_precedes_process_or_workspace_extension(self):
        with self.assertRaises(m.BuildExecutionRefused):
            m.build_candidate(
                self.prepared,
                self.source,
                engine=self.engine,
                validate_authority=lambda: (_ for _ in ()).throw(
                    RuntimeError("invented refusal")
                ),
                finalize_archive=lambda _archive: None,
                seconds=60,
            )
        self.assertFalse((self.prepared.root / "tmp").exists())

    def test_image_architecture_labels_environment_volumes_and_secrets_refuse(self):
        variants = [
            lambda v: v.update(Architecture="arm64"),
            lambda v: v.update(Os="windows"),
            lambda v: v.update(Id=None),
            lambda v: v["Config"].update(Volumes={"/data": {}}),
            lambda v: v["Config"]["Labels"].update(
                {"org.opencontainers.image.revision": "d" * 40}
            ),
            lambda v: v["Config"]["Env"].append("PYTHON_ENV=production"),
            lambda v: v["Config"]["Env"].append("SENTRY_AUTH_TOKEN=invented"),
        ]
        for change in variants:
            value = image(self.prepared, False)
            change(value)
            with (
                self.subTest(change=change),
                self.assertRaises(m.BuildExecutionRefused),
            ):
                m.image_record(m.canonical([value]), self.prepared, javascript=False)


class ArchiveTests(unittest.TestCase):
    def test_image_archive_refuses_missing_duplicate_or_unbound_members(self):
        config_name = APP_ID.removeprefix("sha256:") + ".json"
        base = [
            (config_name, CONFIG_BYTES, tarfile.REGTYPE),
            ("layer.tar", b"invented layer", tarfile.REGTYPE),
        ]
        variants = [
            ({"Config": config_name, "Layers": []}, base),
            ({"Config": config_name, "Layers": ["missing"]}, base),
            ({"Config": config_name, "Layers": ["layer.tar", "layer.tar"]}, base),
            ({"Config": "wrong", "Layers": ["layer.tar"]}, base),
            ({"Config": config_name, "Layers": None}, base),
            ({"Config": config_name, "Layers": [1]}, base),
            ({"Config": config_name, "Layers": ["layer.tar"]}, base + [base[0]]),
            (
                {"Config": config_name, "Layers": ["layer.tar"]},
                base + [("../escape", b"x", tarfile.REGTYPE)],
            ),
            (
                {"Config": config_name, "Layers": ["layer.tar"]},
                base + [("link", b"", tarfile.SYMTYPE)],
            ),
        ]
        with tempfile.TemporaryDirectory(prefix="cosmaneura-archive-unit-") as root:
            path = Path(root) / "image.tar"
            for row, members in variants:
                path.write_bytes(
                    tar(
                        [
                            ("manifest.json", m.canonical([row]), tarfile.REGTYPE),
                            *members,
                        ]
                    )
                )
                with self.subTest(row=row), self.assertRaises(m.BuildExecutionRefused):
                    m.archive_binding(path, APP_ID)

    def test_assets_accept_exact_posix_tree_without_executing_it(self):
        self.assertEqual(
            dict(m.asset_entries(assets()))["assets/app.js"], b"synthetic javascript"
        )

    def test_assets_reject_paths_links_aliases_missing_index_and_file_parents(self):
        invalid = [
            [("../index.html", b"x", tarfile.REGTYPE)],
            [("/client/index.html", b"x", tarfile.REGTYPE)],
            [("client/index.html", b"x", tarfile.SYMTYPE)],
            [("client/index.html", b"x", tarfile.LNKTYPE)],
            [("client/index.html/", b"x", tarfile.REGTYPE)],
            [("client/app.js", b"x", tarfile.REGTYPE)],
            [("client/index.html", b"", tarfile.REGTYPE)],
            [
                ("client/index.html", b"x", tarfile.REGTYPE),
                ("client/Case/a", b"x", tarfile.REGTYPE),
                ("client/case/b", b"x", tarfile.REGTYPE),
            ],
            [
                ("client/index.html", b"x", tarfile.REGTYPE),
                ("client/a", b"x", tarfile.REGTYPE),
                ("client/a/b", b"x", tarfile.REGTYPE),
            ],
        ]
        for entries in invalid:
            with (
                self.subTest(entries=entries),
                self.assertRaises(
                    (m.BuildExecutionRefused, m.inputs.BuildInputRefused)
                ),
            ):
                m.asset_entries(tar(entries))

    def test_image_archive_requires_exact_config_binding(self):
        with tempfile.TemporaryDirectory(prefix="cosmaneura-archive-unit-") as root:
            path = Path(root) / "image.tar"
            path.write_bytes(image_archive())
            m.archive_binding(path, APP_ID)
            path.write_bytes(image_archive(b"mismatched config"))
            with self.assertRaises(m.BuildExecutionRefused):
                m.archive_binding(path, APP_ID)


class DeadlineTests(unittest.TestCase):
    def test_pending_owned_alarm_is_drained_before_restoring_caller_handler(self):
        original_handler = signal.getsignal(signal.SIGALRM)
        original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        calls = []

        def previous_handler(*args):
            calls.append(args)

        signal.signal(signal.SIGALRM, previous_handler)
        try:
            with self.assertRaises(process.BuildProcessRefused):
                with process.build_deadline(60):
                    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
                    os.kill(os.getpid(), signal.SIGALRM)
                    self.assertIn(signal.SIGALRM, signal.sigpending())
            self.assertEqual(calls, [])
            self.assertNotIn(signal.SIGALRM, signal.sigpending())
            self.assertIs(signal.getsignal(signal.SIGALRM), previous_handler)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            self.assertEqual(
                signal.pthread_sigmask(signal.SIG_BLOCK, set()), original_mask
            )
        finally:
            signal.signal(signal.SIGALRM, original_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

    def test_owned_timer_and_handler_restored_on_success_and_failure(self):
        previous = signal.getsignal(signal.SIGALRM)
        for fail in (False, True):
            try:
                with process.build_deadline(60) as (_, tick):
                    tick()
                    if fail:
                        raise RuntimeError("invented")
            except RuntimeError:
                self.assertTrue(fail)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            self.assertIs(signal.getsignal(signal.SIGALRM), previous)

    def test_caller_timer_and_invalid_budget_refused(self):
        for seconds in (0, -1, 3601, True, 1.5):
            with (
                self.subTest(seconds=seconds),
                self.assertRaises(process.BuildProcessRefused),
            ):
                with process.build_deadline(seconds):
                    self.fail("accepted invalid deadline")
        with patch.object(signal, "getitimer", return_value=(10.0, 0.0)):
            with self.assertRaises(process.BuildProcessRefused):
                with process.build_deadline(60):
                    self.fail("stole caller timer")

    def test_original_deadline_and_clock_drift_refuse(self):
        for elapsed, wall_elapsed in ((60, 60), (1, 7), (-1, -1)):
            clock = [100.0, 1000.0]
            with (
                self.subTest(elapsed=elapsed, wall_elapsed=wall_elapsed),
                patch.object(process.time, "monotonic", side_effect=lambda: clock[0]),
                patch.object(process.time, "time", side_effect=lambda: clock[1]),
                self.assertRaises(process.BuildProcessRefused),
            ):
                with process.build_deadline(60) as (_, tick):
                    clock[:] = [100.0 + elapsed, 1000.0 + wall_elapsed]
                    tick()


if __name__ == "__main__":
    unittest.main()
