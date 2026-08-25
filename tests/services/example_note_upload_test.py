# pyright: reportMissingImports=false

from __future__ import annotations

import hashlib
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError

import app.services.example_note_upload as uploads


class _Download:
    def __init__(self, content: bytes):
        self.content = content

    def readall(self) -> bytes:
        return self.content


class _BlobClient:
    def __init__(
        self,
        *,
        blob_key: str,
        content: bytes,
        etag: str = '"etag-v1"',
        content_type: str = uploads.PDF_CONTENT_TYPE,
        size: int | None = None,
    ) -> None:
        self.url = f"https://uploads.invalid/example-notes/{blob_key}"
        self.content = content
        self.etag = etag
        self.content_type = content_type
        self.size = len(content) if size is None else size
        self.property_calls: list[dict[str, object]] = []
        self.download_calls: list[dict[str, object]] = []

    def get_blob_properties(self, **kwargs: object) -> SimpleNamespace:
        self.property_calls.append(kwargs)
        return SimpleNamespace(
            etag=self.etag,
            size=self.size,
            content_settings=SimpleNamespace(content_type=self.content_type),
        )

    def download_blob(self, **kwargs: object) -> _Download:
        self.download_calls.append(kwargs)
        return _Download(self.content)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.lock = threading.Lock()

    def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        assert nx is True
        with self.lock:
            if key in self.values:
                return False
            self.values[key] = value
            self.expirations[key] = ex
            return True

    def get(self, key: str) -> bytes | None:
        with self.lock:
            value = self.values.get(key)
        return value.encode("utf-8") if value is not None else None

    def eval(self, script: str, key_count: int, *args: str) -> int:
        with self.lock:
            if script == uploads._ATOMIC_ACQUIRE_MATCHING_CLAIM:
                assert key_count == 2
                intent_key, claim_key, expected_intent, claim, claim_ttl = args
                if (
                    self.values.get(intent_key) != expected_intent
                    or claim_key in self.values
                ):
                    return 0
                self.values[claim_key] = claim
                self.expirations[claim_key] = int(claim_ttl)
                if self.expirations[intent_key] < int(claim_ttl):
                    self.expirations[intent_key] = int(claim_ttl)
                return 1

            if script == uploads._ATOMIC_COMPLETE_MATCHING_CLAIM:
                assert key_count == 2
                intent_key, claim_key, expected_intent, expected_claim = args
                if (
                    self.values.get(intent_key) != expected_intent
                    or self.values.get(claim_key) != expected_claim
                ):
                    return 0
                del self.values[intent_key]
                del self.values[claim_key]
                self.expirations.pop(intent_key, None)
                self.expirations.pop(claim_key, None)
                return 1

            assert key_count == 1
            key, expected = args
            if self.values.get(key) != expected:
                return 0
            del self.values[key]
            self.expirations.pop(key, None)
            return 1


def _properties(
    content: bytes,
    *,
    etag: str = '"etag-v1"',
    content_type: str = uploads.PDF_CONTENT_TYPE,
    size: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        etag=etag,
        size=len(content) if size is None else size,
        content_settings=SimpleNamespace(content_type=content_type),
    )


def _registered_blob(
    content: bytes,
    *,
    doctor_id: str = "doctor_owner",
    blob_key: str = "pending/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.pdf",
    etag: str = '"etag-v1"',
) -> uploads.RegisteredExampleNoteBlob:
    return uploads.RegisteredExampleNoteBlob(
        blob_key=blob_key,
        doctor_id=doctor_id,
        etag=etag,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def test_example_note_grant_is_random_exact_blob_create_only_and_server_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    generated: list[dict[str, object]] = []

    def fake_generate_blob_sas(**kwargs: object) -> str:
        generated.append(kwargs)
        return f"signature-{len(generated)}"

    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(uploads, "generate_blob_sas", fake_generate_blob_sas)
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda blob_key: SimpleNamespace(
            url=(
                "https://uploads.invalid/"
                f"{uploads.AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME}/{blob_key}"
            )
        ),
    )
    before = datetime.now(timezone.utc)

    first = uploads.issue_example_note_upload("doctor_server_owned")
    second = uploads.issue_example_note_upload("doctor_server_owned")

    after = datetime.now(timezone.utc)
    assert first != second
    assert first.registration_token not in first.upload_url
    assert "doctor_server_owned" not in first.upload_url
    assert "clerk" not in first.upload_url.lower()
    first_path = urlparse(first.upload_url).path
    second_path = urlparse(second.upload_url).path
    assert first_path != second_path
    assert first_path.startswith(
        f"/{uploads.AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME}/pending/"
    )
    assert first_path.endswith(".pdf")

    assert len(generated) == 2
    for grant, call in zip((first, second), generated, strict=True):
        assert (
            call["container_name"] == uploads.AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
        )
        blob_name = call["blob_name"]
        start = call["start"]
        expiry = call["expiry"]
        assert isinstance(blob_name, str)
        assert isinstance(start, datetime)
        assert isinstance(expiry, datetime)
        assert blob_name.startswith("pending/")
        assert urlparse(grant.upload_url).path == (
            f"/{uploads.AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME}/{blob_name}"
        )
        assert str(call["permission"]) == "c"
        assert call["protocol"] == "https"
        assert before - timedelta(minutes=2) <= start <= after
        assert before + timedelta(minutes=9) <= expiry
        assert expiry <= after + timedelta(minutes=11)

    assert set(redis.expirations.values()) == {
        uploads.EXAMPLE_NOTE_UPLOAD_TTL_SECONDS
        + uploads.EXAMPLE_NOTE_REGISTRATION_GRACE_SECONDS
    }
    assert all(first.registration_token not in key for key in redis.values)
    assert all("doctor_server_owned" not in value for value in redis.values.values())


def test_legacy_grant_is_short_lived_create_only_and_container_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated: list[dict[str, object]] = []
    clients: list[dict[str, object]] = []

    def fake_generate_container_sas(**kwargs: object) -> str:
        generated.append(kwargs)
        return "legacy-create-only-signature"

    def fake_container_client(**kwargs: object) -> SimpleNamespace:
        clients.append(kwargs)
        return SimpleNamespace(
            url="https://uploads.invalid/example-notes?sp=c&sig=secret"
        )

    monkeypatch.setattr(
        uploads,
        "generate_container_sas",
        fake_generate_container_sas,
    )
    monkeypatch.setattr(uploads, "ContainerClient", fake_container_client)
    before = datetime.now(timezone.utc)

    result = uploads.issue_legacy_example_note_upload_url()

    after = datetime.now(timezone.utc)
    assert result == "https://uploads.invalid/example-notes?sp=c&sig=secret"
    assert len(generated) == 1
    call = generated[0]
    assert call["container_name"] == uploads.AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
    assert str(call["permission"]) == "c"
    assert call["protocol"] == "https"
    start = call["start"]
    expiry = call["expiry"]
    assert isinstance(start, datetime)
    assert isinstance(expiry, datetime)
    assert before - timedelta(minutes=2) <= start <= after
    assert before + timedelta(minutes=9) <= expiry
    assert expiry <= after + timedelta(minutes=11)
    assert clients == [
        {
            "container_name": uploads.AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
            "account_url": uploads.AZURE_UPLOADS_ACCOUNT_URL,
            "credential": "legacy-create-only-signature",
        }
    ]


def test_legacy_registration_is_owner_bound_and_converted_to_immutable_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nlegacy cached SPA bytes"
    file_name = "user_owner_1787572800000_note.pdf"
    client = _BlobClient(blob_key=file_name, content=content)
    requested_keys: list[str] = []

    def blob_client(blob_key: str) -> _BlobClient:
        requested_keys.append(blob_key)
        return client

    monkeypatch.setattr(uploads, "_blob_client", blob_client)

    registered = uploads.validate_legacy_example_note_upload(
        file_name,
        doctor_id="doctor_owner",
        owner_clerk_id="user_owner",
    )

    assert registered == uploads.RegisteredExampleNoteBlob(
        blob_key=file_name,
        doctor_id="doctor_owner",
        etag='"etag-v1"',
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        key_kind="legacy_owner_stamped_v1",
        legacy_owner_clerk_id="user_owner",
    )
    assert requested_keys == [file_name]
    assert client.property_calls == [{}]
    assert client.download_calls == [
        {
            "offset": 0,
            "length": uploads.MAX_EXAMPLE_NOTE_BYTES + 1,
            "etag": '"etag-v1"',
            "match_condition": MatchConditions.IfNotModified,
        }
    ]
    assert uploads.serialize_registered_blob(registered).startswith("example-note:v2:")


@pytest.mark.parametrize(
    "file_name",
    [
        "user_attacker_1787572800000_note.pdf",
        "../user_owner_1787572800000_note.pdf",
        "user_owner_1787572800000_note.jpg",
        "user_owner_1787572800000_directory\\note.pdf",
    ],
)
def test_legacy_registration_rejects_unowned_or_unbounded_names_before_blob_access(
    monkeypatch: pytest.MonkeyPatch,
    file_name: str,
) -> None:
    requested_keys: list[str] = []
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda blob_key: requested_keys.append(blob_key),
    )

    with pytest.raises(uploads.ExampleNoteUploadValidationError):
        uploads.validate_legacy_example_note_upload(
            file_name,
            doctor_id="doctor_owner",
            owner_clerk_id="user_owner",
        )

    assert requested_keys == []


def test_registration_token_is_owner_bound_single_use_and_accepts_no_client_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nregistered bytes"
    redis = _Redis()
    clients: dict[str, _BlobClient] = {}

    def blob_client(blob_key: str) -> _BlobClient:
        return clients.setdefault(
            blob_key,
            _BlobClient(blob_key=blob_key, content=content),
        )

    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(uploads, "generate_blob_sas", lambda **_kwargs: "signature")
    monkeypatch.setattr(uploads, "_blob_client", blob_client)
    grant = uploads.issue_example_note_upload("doctor_owner")

    with pytest.raises(uploads.ExampleNoteUploadValidationError) as wrong_owner:
        uploads.consume_validated_example_note_upload(
            grant.registration_token,
            "doctor_attacker",
        )
    assert wrong_owner.value.status_code == 409
    assert sum(len(client.property_calls) for client in clients.values()) == 0

    registered = uploads.consume_validated_example_note_upload(
        grant.registration_token,
        "doctor_owner",
    )
    assert registered.doctor_id == "doctor_owner"
    assert registered.blob_key in grant.upload_url
    assert registered.sha256 == hashlib.sha256(content).hexdigest()
    assert registered.claim_token is not None

    with pytest.raises(uploads.ExampleNoteUploadValidationError) as replay:
        uploads.consume_validated_example_note_upload(
            grant.registration_token,
            "doctor_owner",
        )
    assert replay.value.status_code == 409
    assert sum(len(client.property_calls) for client in clients.values()) == 2
    assert uploads.complete_example_note_upload(
        grant.registration_token,
        registered.claim_token,
    )
    with pytest.raises(uploads.ExampleNoteUploadValidationError):
        uploads.consume_validated_example_note_upload(
            grant.registration_token,
            "doctor_owner",
        )
    assert (
        "file_name"
        not in inspect.signature(
            uploads.consume_validated_example_note_upload
        ).parameters
    )


def test_forged_registration_token_fails_before_blob_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    blob_client_calls: list[str] = []
    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda blob_key: blob_client_calls.append(blob_key),
    )

    with pytest.raises(uploads.ExampleNoteUploadValidationError) as error:
        uploads.consume_validated_example_note_upload("A" * 43, "doctor_owner")

    assert error.value.status_code == 409
    assert blob_client_calls == []


def test_concurrent_registration_has_exactly_one_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nregistered bytes"
    redis = _Redis()
    barrier = threading.Barrier(2)

    class _RacingBlobClient(_BlobClient):
        def download_blob(self, **kwargs: object) -> _Download:
            result = super().download_blob(**kwargs)
            barrier.wait(timeout=5)
            return result

    clients: dict[str, _RacingBlobClient] = {}

    def blob_client(blob_key: str) -> _RacingBlobClient:
        return clients.setdefault(
            blob_key,
            _RacingBlobClient(blob_key=blob_key, content=content),
        )

    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(uploads, "generate_blob_sas", lambda **_kwargs: "signature")
    monkeypatch.setattr(uploads, "_blob_client", blob_client)
    grant = uploads.issue_example_note_upload("doctor_owner")

    def register() -> str:
        try:
            uploads.consume_validated_example_note_upload(
                grant.registration_token,
                "doctor_owner",
            )
        except uploads.ExampleNoteUploadValidationError:
            return "rejected"
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: register(), range(2)))

    assert sorted(results) == ["accepted", "rejected"]


def test_failed_registration_releases_only_its_claim_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nregistered bytes"
    redis = _Redis()
    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(uploads, "generate_blob_sas", lambda **_kwargs: "signature")
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda blob_key: _BlobClient(blob_key=blob_key, content=content),
    )
    grant = uploads.issue_example_note_upload("doctor_owner")

    first = uploads.consume_validated_example_note_upload(
        grant.registration_token,
        "doctor_owner",
    )
    assert first.claim_token is not None
    assert not uploads.release_example_note_upload_claim(
        grant.registration_token,
        "B" * 43,
    )
    assert uploads.release_example_note_upload_claim(
        grant.registration_token,
        first.claim_token,
    )

    retry = uploads.consume_validated_example_note_upload(
        grant.registration_token,
        "doctor_owner",
    )
    assert retry.claim_token is not None
    assert retry.claim_token != first.claim_token
    assert uploads.complete_example_note_upload(
        grant.registration_token,
        retry.claim_token,
    )


def test_registered_reference_match_is_stable_across_randomized_encryption() -> None:
    content = b"%PDF-1.7\nregistered bytes"
    registered = _registered_blob(content)

    first_reference = uploads.serialize_registered_blob(registered)
    second_reference = uploads.serialize_registered_blob(registered)

    assert first_reference != second_reference
    assert uploads.registered_example_note_blob_matches(first_reference, registered)
    assert uploads.registered_example_note_blob_matches(second_reference, registered)
    assert not uploads.registered_example_note_blob_matches(
        first_reference,
        _registered_blob(content, doctor_id="doctor_other"),
    )


def test_registration_rejects_mutation_during_conditional_read_and_consumes_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nregistered bytes"
    redis = _Redis()

    class _ChangedDuringReadBlobClient(_BlobClient):
        def download_blob(self, **kwargs: object) -> _Download:
            raise ResourceModifiedError("private-blob-name and signed-query")

    client = _ChangedDuringReadBlobClient(
        blob_key="pending/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.pdf",
        content=content,
    )
    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(uploads, "generate_blob_sas", lambda **_kwargs: "signature")
    monkeypatch.setattr(uploads, "_blob_client", lambda _blob_key: client)
    grant = uploads.issue_example_note_upload("doctor_owner")

    with pytest.raises(uploads.ExampleNoteUploadValidationError) as error:
        uploads.consume_validated_example_note_upload(
            grant.registration_token,
            "doctor_owner",
        )

    assert error.value.status_code == 409
    assert "private-blob-name" not in str(error.value)
    with pytest.raises(uploads.ExampleNoteUploadValidationError):
        uploads.consume_validated_example_note_upload(
            grant.registration_token,
            "doctor_owner",
        )


@pytest.mark.parametrize(
    ("content", "content_type", "size", "expected_status", "expected_detail"),
    [
        (
            b"not-a-pdf",
            uploads.PDF_CONTENT_TYPE,
            None,
            415,
            "Example notes must be valid PDF files.",
        ),
        (
            b"\xff\xd8\xff\xe0image",
            "image/jpeg",
            None,
            415,
            "Example notes must have the application/pdf content type.",
        ),
        (
            b"%PDF-",
            uploads.PDF_CONTENT_TYPE,
            uploads.MAX_EXAMPLE_NOTE_BYTES + 1,
            413,
            "Example note PDFs must be 10 MB or smaller.",
        ),
    ],
)
def test_registration_preserves_bounded_pdf_rejections(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    content_type: str,
    size: int | None,
    expected_status: int,
    expected_detail: str,
) -> None:
    redis = _Redis()
    client = _BlobClient(
        blob_key="pending/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.pdf",
        content=content,
        content_type=content_type,
        size=size,
    )
    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(uploads, "generate_blob_sas", lambda **_kwargs: "signature")
    monkeypatch.setattr(uploads, "_blob_client", lambda _blob_key: client)
    grant = uploads.issue_example_note_upload("doctor_owner")

    with pytest.raises(uploads.ExampleNoteUploadValidationError) as error:
        uploads.consume_validated_example_note_upload(
            grant.registration_token,
            "doctor_owner",
        )

    assert error.value.status_code == expected_status
    assert error.value.detail == expected_detail
    assert redis.values == {}
    if expected_status == 413:
        assert client.download_calls == []


def test_registered_worker_download_is_etag_conditional_and_hash_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nregistered bytes"
    registered = _registered_blob(content)
    client = _BlobClient(
        blob_key=registered.blob_key,
        content=content,
        etag=registered.etag,
    )
    monkeypatch.setattr(uploads, "_blob_client", lambda _blob_key: client)
    reference = uploads.serialize_registered_blob(registered)

    downloaded = uploads.download_registered_or_legacy_example_note(
        reference,
        doctor_id=registered.doctor_id,
    )

    assert downloaded == content
    expected_condition = {
        "etag": registered.etag,
        "match_condition": MatchConditions.IfNotModified,
    }
    assert client.property_calls == [expected_condition]
    assert client.download_calls == [
        {
            "offset": 0,
            "length": uploads.MAX_EXAMPLE_NOTE_BYTES + 1,
            **expected_condition,
        }
    ]


def test_v1_server_owned_reference_remains_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nprevious server-owned locator"
    registered = _registered_blob(content)
    client = _BlobClient(
        blob_key=registered.blob_key,
        content=content,
        etag=registered.etag,
    )
    monkeypatch.setattr(uploads, "_blob_client", lambda _blob_key: client)
    payload = json.dumps(
        {
            "blob_key": registered.blob_key,
            "doctor_id": registered.doctor_id,
            "etag": registered.etag,
            "sha256": registered.sha256,
            "size": registered.size,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    reference = "example-note:v1:" + uploads.encrypt_value(payload)

    downloaded = uploads.download_registered_or_legacy_example_note(
        reference,
        doctor_id=registered.doctor_id,
    )

    assert downloaded == content


def test_v1_reference_never_accepts_a_legacy_owner_stamped_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_keys: list[str] = []
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda blob_key: requested_keys.append(blob_key),
    )
    payload = json.dumps(
        {
            "blob_key": "user_owner_1787572800000_note.pdf",
            "doctor_id": "doctor_owner",
            "etag": '"etag-v1"',
            "sha256": "a" * 64,
            "size": 17,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    reference = "example-note:v1:" + uploads.encrypt_value(payload)

    with pytest.raises(uploads.ExampleNoteBlobIntegrityError):
        uploads.download_registered_or_legacy_example_note(
            reference,
            doctor_id="doctor_owner",
        )

    assert requested_keys == []


@pytest.mark.parametrize(
    "registered",
    [
        uploads.RegisteredExampleNoteBlob(
            blob_key="user_owner_1787572800000_note.pdf",
            doctor_id="doctor_owner",
            etag='"etag-v1"',
            size=17,
            sha256="a" * 64,
        ),
        uploads.RegisteredExampleNoteBlob(
            blob_key="user_owner_1787572800000_note.pdf",
            doctor_id="doctor_owner",
            etag='"etag-v1"',
            size=17,
            sha256="a" * 64,
            key_kind="legacy_owner_stamped_v1",
            legacy_owner_clerk_id="user_someone_else",
        ),
        uploads.RegisteredExampleNoteBlob(
            blob_key="pending/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.pdf",
            doctor_id="doctor_owner",
            etag='"etag-v1"',
            size=17,
            sha256="a" * 64,
            key_kind="legacy_owner_stamped_v1",
            legacy_owner_clerk_id="user_owner",
        ),
    ],
)
def test_serializer_rejects_blob_keys_without_matching_discriminator(
    registered: uploads.RegisteredExampleNoteBlob,
) -> None:
    with pytest.raises(ValueError, match="invalid registered example-note"):
        uploads.serialize_registered_blob(registered)


@pytest.mark.parametrize(
    ("mutate_client", "worker_doctor"),
    [
        (lambda client: setattr(client, "etag", '"etag-v2"'), "doctor_owner"),
        (
            lambda client: setattr(client, "content", b"%PDF-1.7\nsubstitute bytes"),
            "doctor_owner",
        ),
        (lambda _client: None, "doctor_attacker"),
    ],
)
def test_registered_worker_rejects_etag_hash_or_owner_substitution(
    monkeypatch: pytest.MonkeyPatch,
    mutate_client,
    worker_doctor: str,
) -> None:
    content = b"%PDF-1.7\nregistered bytes"
    registered = _registered_blob(content)
    client = _BlobClient(
        blob_key=registered.blob_key,
        content=content,
        etag=registered.etag,
    )
    mutate_client(client)
    monkeypatch.setattr(uploads, "_blob_client", lambda _blob_key: client)
    reference = uploads.serialize_registered_blob(registered)

    with pytest.raises(uploads.ExampleNoteBlobIntegrityError):
        uploads.download_registered_or_legacy_example_note(
            reference,
            doctor_id=worker_doctor,
        )

    if worker_doctor != registered.doctor_id:
        assert client.property_calls == []


def test_registered_worker_sanitizes_azure_etag_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nregistered bytes"
    registered = _registered_blob(content)

    class _ChangedBlobClient(_BlobClient):
        def get_blob_properties(self, **kwargs: object) -> SimpleNamespace:
            raise ResourceModifiedError("private-blob-name and signed-query")

    client = _ChangedBlobClient(
        blob_key=registered.blob_key,
        content=content,
        etag=registered.etag,
    )
    monkeypatch.setattr(uploads, "_blob_client", lambda _blob_key: client)
    reference = uploads.serialize_registered_blob(registered)

    with pytest.raises(uploads.ExampleNoteBlobIntegrityError) as error:
        uploads.download_registered_or_legacy_example_note(
            reference,
            doctor_id=registered.doctor_id,
        )

    assert str(error.value) == "example_note_blob_integrity_check_failed"
    assert "private-blob-name" not in str(error.value)


def test_legacy_download_is_bounded_and_conditioned_on_observed_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7\nlegacy bytes"
    client = _BlobClient(
        blob_key="legacy.pdf",
        content=content,
        etag='"legacy-etag"',
    )
    requested_keys: list[str] = []

    def blob_client(blob_key: str) -> _BlobClient:
        requested_keys.append(blob_key)
        return client

    monkeypatch.setattr(uploads, "_blob_client", blob_client)

    downloaded = uploads.download_registered_or_legacy_example_note(
        "https://legacy.invalid/container/legacy.pdf",
        doctor_id="doctor_legacy",
    )

    assert downloaded == content
    assert requested_keys == ["legacy.pdf"]
    assert client.property_calls == [{}]
    assert client.download_calls == [
        {
            "offset": 0,
            "length": uploads.MAX_EXAMPLE_NOTE_BYTES + 1,
            "etag": '"legacy-etag"',
            "match_condition": MatchConditions.IfNotModified,
        }
    ]


def test_legacy_download_rejects_oversize_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _BlobClient(
        blob_key="legacy.pdf",
        content=b"%PDF-",
        size=uploads.MAX_EXAMPLE_NOTE_BYTES + 1,
    )
    monkeypatch.setattr(uploads, "_blob_client", lambda _blob_key: client)

    with pytest.raises(uploads.ExampleNoteBlobIntegrityError):
        uploads.download_registered_or_legacy_example_note(
            "legacy.pdf",
            doctor_id="doctor_legacy",
        )

    assert client.download_calls == []
