import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

import app.commands.uploads.sas as uploads


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


def _issue(
    monkeypatch: pytest.MonkeyPatch,
    redis: _Redis,
    *,
    upload_type: uploads.UploadType = uploads.UploadType.VISIT,
    file_name: str = "private-patient-name.wav",
) -> uploads.IssuedClinicalUpload:
    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(uploads, "generate_blob_sas", lambda **_kwargs: "signature")
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda blob_key: SimpleNamespace(
            url=f"https://uploads.invalid/phi/{blob_key}",
        ),
    )
    return uploads.issue_upload(
        upload_type=upload_type,
        doctor_id="doctor_owner",
        clerk_issuer="https://issuer.invalid",
        clerk_id="user_owner",
        file_name=file_name,
        file_size=123,
    )


def test_grant_is_random_exact_blob_create_only_short_lived_and_private(
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
            url=f"https://uploads.invalid/phi/{blob_key}",
        ),
    )
    before = datetime.now(timezone.utc)

    first = uploads.issue_upload(
        upload_type=uploads.UploadType.VISIT,
        doctor_id="doctor_owner",
        clerk_issuer="https://issuer.invalid",
        clerk_id="user_owner",
        file_name="patient-name.wav",
        file_size=123,
    )
    second = uploads.issue_upload(
        upload_type=uploads.UploadType.VISIT,
        doctor_id="doctor_owner",
        clerk_issuer="https://issuer.invalid",
        clerk_id="user_owner",
        file_name="patient-name.wav",
        file_size=123,
    )

    after = datetime.now(timezone.utc)
    expected_sas_ttl = uploads._sas_ttl_seconds(123)
    expected_registration_ttl = uploads._registration_ttl_seconds(123)
    assert first != second
    assert first.content_type == "audio/wav"
    assert first.registration_token not in first.upload_url
    assert "patient-name" not in first.upload_url
    assert "doctor_owner" not in first.upload_url
    assert "user_owner" not in first.upload_url
    assert urlparse(first.upload_url).path != urlparse(second.upload_url).path

    for call in generated:
        assert call["container_name"] == uploads.AZURE_UPLOADS_CONTAINER_NAME
        assert str(call["permission"]) == "c"
        assert call["protocol"] == "https"
        start = call["start"]
        expiry = call["expiry"]
        assert isinstance(start, datetime)
        assert isinstance(expiry, datetime)
        assert before - timedelta(minutes=2) <= start <= after
        assert before + timedelta(seconds=expected_sas_ttl - 1) <= expiry
        assert expiry <= after + timedelta(seconds=expected_sas_ttl + 1)

    assert set(redis.expirations.values()) == {expected_registration_ttl}
    assert expected_registration_ttl == (
        expected_sas_ttl + uploads.CLINICAL_UPLOAD_REGISTRATION_GRACE_SECONDS
    )
    assert all(first.registration_token not in key for key in redis.values)
    assert all("doctor_owner" not in value for value in redis.values.values())
    assert all("user_owner" not in value for value in redis.values.values())
    assert all("issuer.invalid" not in value for value in redis.values.values())


def test_maximum_visit_upload_has_throughput_window_and_registration_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    generated: list[dict[str, object]] = []
    monkeypatch.setattr(uploads, "get_redis", lambda: redis)
    monkeypatch.setattr(
        uploads,
        "generate_blob_sas",
        lambda **kwargs: generated.append(kwargs) or "signature",
    )
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda blob_key: SimpleNamespace(
            url=f"https://uploads.invalid/phi/{blob_key}",
        ),
    )
    before = datetime.now(timezone.utc)

    uploads.issue_upload(
        upload_type=uploads.UploadType.VISIT,
        doctor_id="doctor_owner",
        clerk_issuer="https://issuer.invalid",
        clerk_id="user_owner",
        file_name="recording.wav",
        file_size=uploads.MAX_VISIT_UPLOAD_BYTES,
    )

    expected_sas_ttl = uploads._sas_ttl_seconds(uploads.MAX_VISIT_UPLOAD_BYTES)
    expected_registration_ttl = uploads._registration_ttl_seconds(
        uploads.MAX_VISIT_UPLOAD_BYTES
    )
    expiry = generated[0]["expiry"]
    assert isinstance(expiry, datetime)
    assert expiry >= before + timedelta(seconds=expected_sas_ttl - 1)
    assert expected_sas_ttl > 2 * 60 * 60
    assert set(redis.expirations.values()) == {expected_registration_ttl}
    assert expected_registration_ttl > expected_sas_ttl


def test_registration_is_actor_kind_bound_and_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    grant = _issue(monkeypatch, redis)
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda _blob_key: SimpleNamespace(
            get_blob_properties=lambda: SimpleNamespace(
                etag='"etag-v1"',
                size=123,
                content_settings=SimpleNamespace(content_type="audio/wav"),
            )
        ),
    )

    with pytest.raises(uploads.ClinicalUploadValidationError) as wrong_actor:
        uploads.consume_upload(
            grant.registration_token,
            upload_type=uploads.UploadType.VISIT,
            doctor_id="doctor_other",
            clerk_issuer="https://issuer.invalid",
            clerk_id="user_other",
        )
    assert wrong_actor.value.status_code == 409

    with pytest.raises(uploads.ClinicalUploadValidationError) as wrong_issuer:
        uploads.consume_upload(
            grant.registration_token,
            upload_type=uploads.UploadType.VISIT,
            doctor_id="doctor_owner",
            clerk_issuer="https://other-issuer.invalid",
            clerk_id="user_owner",
        )
    assert wrong_issuer.value.status_code == 409

    with pytest.raises(uploads.ClinicalUploadValidationError) as wrong_kind:
        uploads.consume_upload(
            grant.registration_token,
            upload_type=uploads.UploadType.LAB,
            doctor_id="doctor_owner",
            clerk_issuer="https://issuer.invalid",
            clerk_id="user_owner",
        )
    assert wrong_kind.value.status_code == 409

    registered = uploads.consume_upload(
        grant.registration_token,
        upload_type=uploads.UploadType.VISIT,
        doctor_id="doctor_owner",
        clerk_issuer="https://issuer.invalid",
        clerk_id="user_owner",
    )
    assert registered.blob_key.startswith("visit_")
    assert registered.blob_key.endswith(".wav")
    assert registered.content_type == "audio/wav"

    assert uploads.complete_upload(
        grant.registration_token,
        registered.claim_token,
    )

    with pytest.raises(uploads.ClinicalUploadValidationError) as reused:
        uploads.consume_upload(
            grant.registration_token,
            upload_type=uploads.UploadType.VISIT,
            doctor_id="doctor_owner",
            clerk_issuer="https://issuer.invalid",
            clerk_id="user_owner",
        )
    assert reused.value.status_code == 409


def test_failed_registration_releases_only_its_claim_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    grant = _issue(monkeypatch, redis)
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda _blob_key: SimpleNamespace(
            get_blob_properties=lambda: SimpleNamespace(
                etag='"etag-v1"',
                size=123,
                content_settings=SimpleNamespace(content_type="audio/wav"),
            )
        ),
    )

    first = uploads.consume_upload(
        grant.registration_token,
        upload_type=uploads.UploadType.VISIT,
        doctor_id="doctor_owner",
        clerk_issuer="https://issuer.invalid",
        clerk_id="user_owner",
    )
    assert (
        redis.expirations[uploads._claim_redis_key(grant.registration_token)]
        == uploads._REGISTRATION_CLAIM_TTL_SECONDS
    )
    assert not uploads.release_upload_claim(
        grant.registration_token,
        "B" * 43,
    )
    assert uploads.release_upload_claim(
        grant.registration_token,
        first.claim_token,
    )

    retry = uploads.consume_upload(
        grant.registration_token,
        upload_type=uploads.UploadType.VISIT,
        doctor_id="doctor_owner",
        clerk_issuer="https://issuer.invalid",
        clerk_id="user_owner",
    )
    assert retry.blob_key == first.blob_key
    assert retry.claim_token != first.claim_token


@pytest.mark.parametrize(
    ("upload_type", "file_name"),
    [
        (uploads.UploadType.VISIT, "recording.exe"),
        (uploads.UploadType.LAB, "labs.png"),
        (uploads.UploadType.LAB, "../labs.pdf"),
    ],
)
def test_issue_rejects_unsupported_or_path_bearing_names(
    monkeypatch: pytest.MonkeyPatch,
    upload_type: uploads.UploadType,
    file_name: str,
) -> None:
    monkeypatch.setattr(uploads, "get_redis", lambda: _Redis())

    with pytest.raises(uploads.ClinicalUploadValidationError) as exc:
        uploads.issue_upload(
            upload_type=upload_type,
            doctor_id="doctor_owner",
            clerk_issuer="https://issuer.invalid",
            clerk_id="user_owner",
            file_name=file_name,
            file_size=123,
        )

    assert exc.value.status_code == 415


@pytest.mark.parametrize(
    ("upload_type", "file_size", "expected_status"),
    [
        (uploads.UploadType.VISIT, 0, 415),
        (uploads.UploadType.VISIT, uploads.MAX_VISIT_UPLOAD_BYTES + 1, 413),
        (uploads.UploadType.LAB, uploads.MAX_LAB_UPLOAD_BYTES + 1, 413),
    ],
)
def test_issue_rejects_empty_or_oversized_files(
    monkeypatch: pytest.MonkeyPatch,
    upload_type: uploads.UploadType,
    file_size: int,
    expected_status: int,
) -> None:
    monkeypatch.setattr(uploads, "get_redis", lambda: _Redis())
    file_name = (
        "recording.wav" if upload_type is uploads.UploadType.VISIT else "labs.pdf"
    )

    with pytest.raises(uploads.ClinicalUploadValidationError) as exc:
        uploads.issue_upload(
            upload_type=upload_type,
            doctor_id="doctor_owner",
            clerk_issuer="https://issuer.invalid",
            clerk_id="user_owner",
            file_name=file_name,
            file_size=file_size,
        )

    assert exc.value.status_code == expected_status


def test_registration_rejects_a_truncated_upload_and_consumes_the_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    grant = _issue(monkeypatch, redis)
    monkeypatch.setattr(
        uploads,
        "_blob_client",
        lambda _blob_key: SimpleNamespace(
            get_blob_properties=lambda: SimpleNamespace(
                etag='"etag-v1"',
                size=122,
                content_settings=SimpleNamespace(content_type="audio/wav"),
            )
        ),
    )

    with pytest.raises(uploads.ClinicalUploadValidationError) as truncated:
        uploads.consume_upload(
            grant.registration_token,
            upload_type=uploads.UploadType.VISIT,
            doctor_id="doctor_owner",
            clerk_issuer="https://issuer.invalid",
            clerk_id="user_owner",
        )
    assert truncated.value.status_code == 415

    with pytest.raises(uploads.ClinicalUploadValidationError) as reused:
        uploads.consume_upload(
            grant.registration_token,
            upload_type=uploads.UploadType.VISIT,
            doctor_id="doctor_owner",
            clerk_issuer="https://issuer.invalid",
            clerk_id="user_owner",
        )
    assert reused.value.status_code == 409
