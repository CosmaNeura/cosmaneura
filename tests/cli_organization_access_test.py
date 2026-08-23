import hashlib
import json

import pytest
import typer

from app.cli import _read_roster_manifest, _write_new_private_secret


def _private_manifest(tmp_path, payload, *, name="roster.json"):
    path = tmp_path / name
    raw = json.dumps(payload, separators=(",", ":")).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return path, raw


def test_roster_manifest_uses_exact_raw_digest_and_normalizes_email(tmp_path):
    path, raw = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [{"email": " Pilot@Example.COM "}],
        },
    )

    entries, digest = _read_roster_manifest(str(path))

    assert digest == hashlib.sha256(raw).hexdigest()
    assert len(entries) == 1
    assert entries[0].email == "pilot@example.com"
    assert entries[0].expires_at is None


def test_roster_manifest_rejects_permissive_file(tmp_path):
    path, _ = _private_manifest(
        tmp_path,
        {"schema_version": 1, "members": [{"email": "pilot@example.com"}]},
    )
    path.chmod(0o640)

    with pytest.raises(typer.BadParameter, match="chmod 600"):
        _read_roster_manifest(str(path))


def test_roster_manifest_rejects_symlink(tmp_path):
    path, _ = _private_manifest(
        tmp_path,
        {"schema_version": 1, "members": [{"email": "pilot@example.com"}]},
    )
    link = tmp_path / "linked-roster.json"
    link.symlink_to(path)

    with pytest.raises(typer.BadParameter, match="symbolic link"):
        _read_roster_manifest(str(link))


def test_roster_manifest_validation_does_not_disclose_email(tmp_path):
    sensitive_email = "sensitive-provider@example.com"
    path, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [
                {"email": sensitive_email},
                {"email": sensitive_email.upper()},
            ],
        },
    )

    with pytest.raises(typer.BadParameter) as raised:
        _read_roster_manifest(str(path))

    assert sensitive_email not in str(raised.value).lower()


def test_roster_manifest_rejects_email_longer_than_storage_contract(tmp_path):
    path, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [{"email": f"{'a' * 309}@example.com"}],
        },
    )

    with pytest.raises(typer.BadParameter, match="invalid email"):
        _read_roster_manifest(str(path))


def test_access_code_output_is_private_and_never_overwrites(tmp_path):
    path = tmp_path / "organization-code.txt"

    _write_new_private_secret(str(path), "ABCDEFGH2345")

    assert path.read_text() == "ABCDEFGH2345\n"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(typer.BadParameter, match="new writable file"):
        _write_new_private_secret(str(path), "ZZZZZZZZ9999")
    assert path.read_text() == "ABCDEFGH2345\n"
