# pyright: reportMissingImports=false

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.commands.uploads.sas as sas


@pytest.mark.parametrize("upload_type", [sas.UploadType.VISIT, sas.UploadType.LAB])
def test_legacy_upload_sas_is_scoped_to_its_container(
    monkeypatch: pytest.MonkeyPatch,
    upload_type: sas.UploadType,
) -> None:
    generated: list[dict[str, object]] = []
    clients: list[dict[str, object]] = []

    def fake_generate_container_sas(**kwargs: object) -> str:
        generated.append(kwargs)
        return "container-signature"

    def fake_container_client(**kwargs: object) -> SimpleNamespace:
        clients.append(kwargs)
        return SimpleNamespace(
            url="https://uploads.invalid/container?container-signature"
        )

    monkeypatch.setattr(sas, "generate_container_sas", fake_generate_container_sas)
    monkeypatch.setattr(sas, "ContainerClient", fake_container_client)
    before = datetime.now(timezone.utc)

    result = sas.perform(upload_type)

    after = datetime.now(timezone.utc)
    assert result == "https://uploads.invalid/container?container-signature"
    assert len(generated) == 1
    grant = generated[0]
    assert grant["container_name"] == upload_type.value
    assert str(grant["permission"]) == "w"
    assert grant["protocol"] == "https"
    expiry = grant["expiry"]
    assert isinstance(expiry, datetime)
    assert before + timedelta(minutes=59) <= expiry
    assert expiry <= after + timedelta(minutes=61)
    assert clients == [
        {
            "container_name": upload_type.value,
            "account_url": sas.AZURE_UPLOADS_ACCOUNT_URL,
            "credential": "container-signature",
        }
    ]


def test_legacy_upload_sas_never_uses_account_scope() -> None:
    source_names = set(sas.perform.__code__.co_names)

    assert "generate_container_sas" in source_names
    assert "generate_account_sas" not in source_names
    assert "AccountSasPermissions" not in source_names
