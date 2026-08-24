"""Issue and consume narrowly scoped clinical-upload grants.

The browser receives a create-only SAS for one unpredictable blob. The
companion registration token is encrypted at rest in Redis, single-use, and
bound to the authenticated Doctor and Clerk subject. Neither endpoint accepts
a browser-selected blob key.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import NoReturn

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import (
    BlobClient,
    BlobSasPermissions,
    BlobServiceClient,
    generate_blob_sas,
)

from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_NAME,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_CONTAINER_NAME,
)
from app.configuration.redis import get_redis
from app.utils.encryption import (
    DecryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

# Size the exact-blob SAS for a documented minimum sustained throughput of
# 2 Mbps (256 KiB/s), plus startup/retry overhead. A maximum-size recording gets
# about 2.5 hours; smaller uploads retain materially shorter credentials. The
# encrypted registration intent lives 15 minutes longer so a completed upload
# can still be registered after the write SAS expires.
CLINICAL_UPLOAD_MINIMUM_BYTES_PER_SECOND = 256 * 1024
CLINICAL_UPLOAD_STARTUP_ALLOWANCE_SECONDS = 15 * 60
CLINICAL_UPLOAD_MAX_SAS_TTL_SECONDS = 3 * 60 * 60
CLINICAL_UPLOAD_REGISTRATION_GRACE_SECONDS = 15 * 60
MAX_VISIT_UPLOAD_BYTES = 2_000 * 1024 * 1024
MAX_LAB_UPLOAD_BYTES = 25 * 1024 * 1024

_MAX_ISSUE_ATTEMPTS = 3
_REGISTRATION_CLAIM_TTL_SECONDS = 15 * 60
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_BLOB_KEY_RE = re.compile(
    r"^(?:visit|lab)_[A-Za-z0-9_-]{43}\.(?:mp3|wav|m4a|mp4|mov|avi|pdf)$"
)
_MAX_ETAG_LENGTH = 256

_ATOMIC_CONSUME_MATCHING_INTENT = """
local value = redis.call('GET', KEYS[1])
if not value or value ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""

_ATOMIC_RELEASE_MATCHING_CLAIM = """
local value = redis.call('GET', KEYS[1])
if not value or value ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""

_ATOMIC_ACQUIRE_MATCHING_CLAIM = """
local intent = redis.call('GET', KEYS[1])
if not intent or intent ~= ARGV[1] then
  return 0
end
local claimed = redis.call('SET', KEYS[2], ARGV[2], 'NX', 'EX', ARGV[3])
if not claimed then
  return 0
end
local intent_ttl = redis.call('TTL', KEYS[1])
local claim_ttl = tonumber(ARGV[3])
if intent_ttl < claim_ttl then
  redis.call('EXPIRE', KEYS[1], claim_ttl)
end
return 1
"""

_ATOMIC_COMPLETE_MATCHING_CLAIM = """
local intent = redis.call('GET', KEYS[1])
local claim = redis.call('GET', KEYS[2])
if not intent or intent ~= ARGV[1] or not claim or claim ~= ARGV[2] then
  return 0
end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
return 1
"""


class UploadType(str, Enum):
    VISIT = "visit"
    LAB = "lab"


def perform(_upload_type: UploadType) -> NoReturn:
    """Refuse the retired container-wide grant command.

    ``app.commands`` requires command modules to expose ``perform``. Keeping a
    fail-closed stub prevents an old internal caller from silently restoring
    the broad write-SAS behavior.
    """

    raise RuntimeError("container_wide_upload_grants_are_disabled")


_CONTENT_TYPE_BY_EXTENSION: dict[UploadType, dict[str, str]] = {
    UploadType.VISIT: {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
    },
    UploadType.LAB: {
        ".pdf": "application/pdf",
    },
}


@dataclass(frozen=True)
class ClinicalUploadValidationError(ValueError):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class IssuedClinicalUpload:
    upload_url: str
    registration_token: str
    content_type: str


@dataclass(frozen=True)
class RegisteredClinicalUpload:
    blob_key: str
    content_type: str
    size: int
    etag: str
    claim_token: str


@dataclass(frozen=True)
class _PendingClinicalUpload:
    blob_key: str
    doctor_id: str
    clerk_issuer: str
    clerk_id: str
    upload_type: UploadType
    content_type: str
    expected_size: int


def _upload_session_error() -> NoReturn:
    raise ClinicalUploadValidationError(
        status_code=409,
        detail="The upload session is invalid or expired. Please upload the file again.",
    )


def _redis_key(registration_token: str) -> str:
    digest = pseudonymous_digest(
        registration_token,
        context="clinical-upload-registration",
    )
    return f"clinical-upload:{digest}"


def _claim_redis_key(registration_token: str) -> str:
    digest = pseudonymous_digest(
        registration_token,
        context="clinical-upload-registration-claim",
    )
    return f"clinical-upload-claim:{digest}"


def _blob_client(blob_key: str) -> BlobClient:
    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )
    return service_client.get_blob_client(
        container=AZURE_UPLOADS_CONTAINER_NAME,
        blob=blob_key,
    )


def _decode_redis_value(raw_value: object) -> str | None:
    if isinstance(raw_value, bytes):
        try:
            return raw_value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return raw_value if isinstance(raw_value, str) else None


def _encode_pending_intent(intent: _PendingClinicalUpload) -> str:
    payload = json.dumps(
        {
            "blob_key": intent.blob_key,
            "clerk_id": intent.clerk_id,
            "clerk_issuer": intent.clerk_issuer,
            "content_type": intent.content_type,
            "doctor_id": intent.doctor_id,
            "expected_size": intent.expected_size,
            "upload_type": intent.upload_type.value,
            "version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return encrypt_value(payload)


def _decode_pending_intent(value: str) -> _PendingClinicalUpload | None:
    try:
        payload = json.loads(decrypt_value(value))
    except (DecryptionError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "blob_key",
        "clerk_id",
        "clerk_issuer",
        "content_type",
        "doctor_id",
        "expected_size",
        "upload_type",
        "version",
    }:
        return None
    try:
        upload_type = UploadType(payload.get("upload_type"))
    except (TypeError, ValueError):
        return None
    blob_key = payload.get("blob_key")
    clerk_id = payload.get("clerk_id")
    clerk_issuer = payload.get("clerk_issuer")
    content_type = payload.get("content_type")
    doctor_id = payload.get("doctor_id")
    expected_size = payload.get("expected_size")
    extension = (
        os.path.splitext(blob_key)[1].lower() if isinstance(blob_key, str) else ""
    )
    if (
        payload.get("version") != 1
        or not isinstance(blob_key, str)
        or _BLOB_KEY_RE.fullmatch(blob_key) is None
        or not blob_key.startswith(f"{upload_type.value}_")
        or not isinstance(clerk_id, str)
        or not clerk_id
        or not isinstance(clerk_issuer, str)
        or not clerk_issuer
        or not isinstance(doctor_id, str)
        or not doctor_id
        or not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size <= 0
        or expected_size > _max_upload_bytes(upload_type)
        or not isinstance(content_type, str)
        or _CONTENT_TYPE_BY_EXTENSION[upload_type].get(extension) != content_type
    ):
        return None
    return _PendingClinicalUpload(
        blob_key=blob_key,
        doctor_id=doctor_id,
        clerk_issuer=clerk_issuer,
        clerk_id=clerk_id,
        upload_type=upload_type,
        content_type=content_type,
        expected_size=expected_size,
    )


def _upload_shape(upload_type: UploadType, file_name: str) -> tuple[str, str]:
    if not file_name or file_name != os.path.basename(file_name):
        raise ClinicalUploadValidationError(
            415, "The selected file type is not supported."
        )
    extension = os.path.splitext(file_name)[1].lower()
    content_type = _CONTENT_TYPE_BY_EXTENSION[upload_type].get(extension)
    if content_type is None:
        raise ClinicalUploadValidationError(
            415, "The selected file type is not supported."
        )
    return extension, content_type


def _max_upload_bytes(upload_type: UploadType) -> int:
    return (
        MAX_VISIT_UPLOAD_BYTES
        if upload_type is UploadType.VISIT
        else MAX_LAB_UPLOAD_BYTES
    )


def _sas_ttl_seconds(file_size: int) -> int:
    transfer_seconds = (
        file_size + CLINICAL_UPLOAD_MINIMUM_BYTES_PER_SECOND - 1
    ) // CLINICAL_UPLOAD_MINIMUM_BYTES_PER_SECOND
    return min(
        CLINICAL_UPLOAD_MAX_SAS_TTL_SECONDS,
        CLINICAL_UPLOAD_STARTUP_ALLOWANCE_SECONDS + transfer_seconds,
    )


def _registration_ttl_seconds(file_size: int) -> int:
    return _sas_ttl_seconds(file_size) + CLINICAL_UPLOAD_REGISTRATION_GRACE_SECONDS


def issue_upload(
    *,
    upload_type: UploadType,
    doctor_id: str,
    clerk_issuer: str,
    clerk_id: str,
    file_name: str,
    file_size: int,
) -> IssuedClinicalUpload:
    """Issue one exact-blob, short-lived, create-only upload grant."""

    if not doctor_id or not clerk_issuer or not clerk_id:
        raise ValueError("invalid upload owner")
    extension, content_type = _upload_shape(upload_type, file_name)
    if not isinstance(file_size, int) or isinstance(file_size, bool) or file_size <= 0:
        raise ClinicalUploadValidationError(415, "The selected file is empty.")
    if file_size > _max_upload_bytes(upload_type):
        raise ClinicalUploadValidationError(413, "The selected file is too large.")

    redis = get_redis()
    sas_ttl_seconds = _sas_ttl_seconds(file_size)
    registration_ttl_seconds = _registration_ttl_seconds(file_size)
    for _ in range(_MAX_ISSUE_ATTEMPTS):
        blob_key = f"{upload_type.value}_{secrets.token_urlsafe(32)}{extension}"
        registration_token = secrets.token_urlsafe(32)
        intent = _PendingClinicalUpload(
            blob_key=blob_key,
            doctor_id=doctor_id,
            clerk_issuer=clerk_issuer,
            clerk_id=clerk_id,
            upload_type=upload_type,
            content_type=content_type,
            expected_size=file_size,
        )
        if not redis.set(
            _redis_key(registration_token),
            _encode_pending_intent(intent),
            nx=True,
            ex=registration_ttl_seconds,
        ):
            continue

        now = datetime.now(timezone.utc)
        sas_token = generate_blob_sas(
            account_name=AZURE_UPLOADS_ACCOUNT_NAME,
            account_key=AZURE_UPLOADS_ACCOUNT_KEY,
            container_name=AZURE_UPLOADS_CONTAINER_NAME,
            blob_name=blob_key,
            permission=BlobSasPermissions(create=True),
            protocol="https",
            start=now - timedelta(minutes=1),
            expiry=now + timedelta(seconds=sas_ttl_seconds),
        )
        return IssuedClinicalUpload(
            upload_url=f"{_blob_client(blob_key).url}?{sas_token}",
            registration_token=registration_token,
            content_type=content_type,
        )

    raise RuntimeError("clinical_upload_issue_failed")


def _load_pending_intent(
    registration_token: str,
) -> tuple[_PendingClinicalUpload, str]:
    if _OPAQUE_TOKEN_RE.fullmatch(registration_token) is None:
        _upload_session_error()
    raw_value = get_redis().get(_redis_key(registration_token))
    stored_value = _decode_redis_value(raw_value)
    if stored_value is None:
        _upload_session_error()
    intent = _decode_pending_intent(stored_value)
    if intent is None:
        _upload_session_error()
    return intent, stored_value


def _consume_pending_intent(registration_token: str, stored_value: str) -> bool:
    return bool(
        get_redis().eval(
            _ATOMIC_CONSUME_MATCHING_INTENT,
            1,
            _redis_key(registration_token),
            stored_value,
        )
    )


def _content_type(properties: object) -> str:
    content_settings = getattr(properties, "content_settings", None)
    raw_content_type = getattr(content_settings, "content_type", "") or ""
    return raw_content_type.partition(";")[0].strip().lower()


def _validate_uploaded_blob(
    intent: _PendingClinicalUpload,
) -> tuple[str, str, int, str]:
    try:
        properties = _blob_client(intent.blob_key).get_blob_properties()
    except ResourceNotFoundError:
        raise ClinicalUploadValidationError(
            400,
            "The uploaded file could not be found. Please upload it again.",
        ) from None
    except AzureError:
        raise ClinicalUploadValidationError(
            503,
            "The uploaded file could not be verified. Please try again.",
        ) from None

    etag = getattr(properties, "etag", None)
    size = getattr(properties, "size", None)
    max_size = _max_upload_bytes(intent.upload_type)
    if not isinstance(etag, str) or not etag or len(etag) > _MAX_ETAG_LENGTH:
        raise ClinicalUploadValidationError(415, "The uploaded file is incomplete.")
    if not isinstance(size, int) or size <= 0:
        raise ClinicalUploadValidationError(
            415, "The uploaded file is empty or incomplete."
        )
    if size > max_size:
        raise ClinicalUploadValidationError(413, "The uploaded file is too large.")
    if size != intent.expected_size:
        raise ClinicalUploadValidationError(
            415,
            "The uploaded file is incomplete. Please upload it again.",
        )
    if _content_type(properties) != intent.content_type:
        raise ClinicalUploadValidationError(
            415,
            "The uploaded file content type does not match the selected file.",
        )
    return intent.blob_key, intent.content_type, size, etag


def consume_upload(
    registration_token: str,
    *,
    upload_type: UploadType,
    doctor_id: str,
    clerk_issuer: str,
    clerk_id: str,
) -> RegisteredClinicalUpload:
    """Validate and exclusively claim an upload owned by this exact actor."""

    intent, stored_value = _load_pending_intent(registration_token)
    if (
        intent.upload_type is not upload_type
        or not secrets.compare_digest(intent.doctor_id, doctor_id)
        or not secrets.compare_digest(intent.clerk_issuer, clerk_issuer)
        or not secrets.compare_digest(intent.clerk_id, clerk_id)
    ):
        _upload_session_error()

    try:
        blob_key, content_type, size, etag = _validate_uploaded_blob(intent)
    except ClinicalUploadValidationError as exc:
        if exc.status_code != 503:
            _consume_pending_intent(registration_token, stored_value)
        raise

    claim_token = secrets.token_urlsafe(32)
    if not get_redis().eval(
        _ATOMIC_ACQUIRE_MATCHING_CLAIM,
        2,
        _redis_key(registration_token),
        _claim_redis_key(registration_token),
        stored_value,
        claim_token,
        str(_REGISTRATION_CLAIM_TTL_SECONDS),
    ):
        _upload_session_error()
    return RegisteredClinicalUpload(
        blob_key=blob_key,
        content_type=content_type,
        size=size,
        etag=etag,
        claim_token=claim_token,
    )


def release_upload_claim(registration_token: str, claim_token: str) -> bool:
    """Release only this caller's in-flight claim after registration fails."""

    if (
        _OPAQUE_TOKEN_RE.fullmatch(registration_token) is None
        or _OPAQUE_TOKEN_RE.fullmatch(claim_token) is None
    ):
        return False
    return bool(
        get_redis().eval(
            _ATOMIC_RELEASE_MATCHING_CLAIM,
            1,
            _claim_redis_key(registration_token),
            claim_token,
        )
    )


def complete_upload(registration_token: str, claim_token: str) -> bool:
    """Consume an intent only after its durable registration has succeeded."""

    if (
        _OPAQUE_TOKEN_RE.fullmatch(registration_token) is None
        or _OPAQUE_TOKEN_RE.fullmatch(claim_token) is None
    ):
        return False
    raw_value = get_redis().get(_redis_key(registration_token))
    stored_value = _decode_redis_value(raw_value)
    if stored_value is None:
        return False
    return bool(
        get_redis().eval(
            _ATOMIC_COMPLETE_MATCHING_CLAIM,
            2,
            _redis_key(registration_token),
            _claim_redis_key(registration_token),
            stored_value,
            claim_token,
        )
    )
