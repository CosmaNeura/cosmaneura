"""Synthetic local Git histories exercise every fail-closed lineage condition."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import scribe_runtime_lineage as lineage


def run_git(repository: Path, *arguments: str) -> str:
    return (
        subprocess.check_output(
            ["git", "-C", str(repository), *arguments], stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )


@pytest.fixture
def history(tmp_path, monkeypatch):
    repository = tmp_path / "source"
    repository.mkdir()
    run_git(repository, "init")
    run_git(repository, "config", "user.name", "Synthetic Test")
    run_git(repository, "config", "user.email", "synthetic@example.invalid")
    run_git(repository, "config", "commit.gpgsign", "false")
    module = repository / lineage.MODULE_PATH
    module.parent.mkdir(parents=True)

    def commit(contents: bytes) -> str:
        module.write_bytes(contents)
        run_git(repository, "add", ".")
        run_git(repository, "commit", "-m", "synthetic source")
        return run_git(repository, "rev-parse", "HEAD")

    repair_bytes = b"synthetic repaired transport\n"
    runtime_bytes = b"synthetic followup transport\n"
    repair = commit(repair_bytes)
    followup = commit(runtime_bytes)
    (repository / "README.md").write_text("synthetic documentation\n")
    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", "synthetic documentation")
    pin = run_git(repository, "rev-parse", "HEAD")
    repair_hash = hashlib.sha256(repair_bytes).hexdigest()
    runtime_hash = hashlib.sha256(runtime_bytes).hexdigest()
    for name, value in {
        "REPAIR_REVISION": repair,
        "FOLLOWUP_REVISION": followup,
        "SCRIBE_SOURCE_REVISION": pin,
        "REPAIR_SHA256": repair_hash,
        "RUNTIME_SHA256": runtime_hash,
    }.items():
        monkeypatch.setattr(lineage, name, value)
    labels = tmp_path / "recorded-labels.json"
    labels.write_text(json.dumps({lineage.PATCH_LABEL: repair_hash}))
    runtime = tmp_path / "fresh-runtime-module.py"
    runtime.write_bytes(runtime_bytes)
    return repository, labels, runtime, commit


def test_repaired_ancestor_and_reviewed_followup_pass(history):
    repository, labels, runtime, _ = history
    result = lineage.verify(repository, labels, runtime)
    assert result == (
        lineage.REPAIR_SHA256,
        lineage.REPAIR_REVISION,
        lineage.FOLLOWUP_REVISION,
        lineage.SCRIBE_SOURCE_REVISION,
        lineage.RUNTIME_SHA256,
    )


@pytest.mark.parametrize("labels", [{}, {lineage.PATCH_LABEL: "0" * 64}, []])
def test_missing_mismatched_or_malformed_recorded_labels_refuse(history, labels):
    repository, path, runtime, _ = history
    path.write_text(json.dumps(labels))
    with pytest.raises(lineage.LineageRejected, match="recorded_label"):
        lineage.verify(repository, path, runtime)


def test_repair_bytes_must_match_recorded_label(history, monkeypatch):
    repository, labels, runtime, _ = history
    monkeypatch.setattr(lineage, "REPAIR_SHA256", "0" * 64)
    labels.write_text(json.dumps({lineage.PATCH_LABEL: "0" * 64}))
    with pytest.raises(lineage.LineageRejected, match="repair_hash"):
        lineage.verify(repository, labels, runtime)


def test_nonancestor_repair_refuses(history, monkeypatch):
    repository, labels, runtime, _ = history
    monkeypatch.setattr(
        lineage, "SCRIBE_SOURCE_REVISION", lineage.REPAIR_REVISION + "^{}"
    )
    # A nonexistent pinned commit also refuses; an existing earlier pin must
    # fail the actual ancestor operation rather than a string comparison.
    run_git(repository, "checkout", "--orphan", "unrelated")
    run_git(repository, "commit", "--allow-empty", "-m", "unrelated synthetic root")
    monkeypatch.setattr(
        lineage, "SCRIBE_SOURCE_REVISION", run_git(repository, "rev-parse", "HEAD")
    )
    with pytest.raises(lineage.LineageRejected, match="git_rejected"):
        lineage.verify(repository, labels, runtime)


def test_any_additional_module_change_refuses(history, monkeypatch):
    repository, labels, runtime, commit = history
    extra = commit(b"unreviewed synthetic change\n")
    monkeypatch.setattr(lineage, "SCRIBE_SOURCE_REVISION", extra)
    with pytest.raises(lineage.LineageRejected, match="followup"):
        lineage.verify(repository, labels, runtime)


def test_wrong_followup_revision_refuses(history, monkeypatch):
    repository, labels, runtime, _ = history
    monkeypatch.setattr(lineage, "FOLLOWUP_REVISION", lineage.REPAIR_REVISION)
    with pytest.raises(lineage.LineageRejected, match="followup"):
        lineage.verify(repository, labels, runtime)


def test_pinned_source_bytes_must_match_reviewed_runtime_hash(history, monkeypatch):
    repository, labels, runtime, _ = history
    monkeypatch.setattr(lineage, "RUNTIME_SHA256", "0" * 64)
    with pytest.raises(lineage.LineageRejected, match="pinned_hash"):
        lineage.verify(repository, labels, runtime)


@pytest.mark.parametrize("condition", ["missing", "changed", "symlink"])
def test_fresh_runtime_module_required(history, condition):
    repository, labels, runtime, _ = history
    if condition == "changed":
        runtime.write_bytes(b"synthetic old runtime\n")
    else:
        runtime.unlink()
        if condition == "symlink":
            runtime.symlink_to(repository / lineage.MODULE_PATH)
    with pytest.raises(lineage.LineageRejected, match="runtime"):
        lineage.verify(repository, labels, runtime)


def test_source_revision_has_one_definition_and_documentation_matches():
    from scripts.scribe_runtime_pin import SCRIBE_SOURCE_REVISION

    assert lineage.SCRIBE_SOURCE_REVISION == SCRIBE_SOURCE_REVISION
    root = Path(__file__).resolve().parents[2]
    readme = (root / "infra/README.md").read_text()
    assert f"Scribe source at `{SCRIBE_SOURCE_REVISION}`" in readme
