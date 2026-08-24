"""Issue and validate narrowly scoped example-note uploads.

The browser receives a create-only SAS for one unpredictable blob. Registration
uses an opaque, single-use token; it never accepts a client-selected blob name.
The persisted reference binds the worker to the exact ETag and bytes that were
validated at registration time without requiring a schema change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn

from azure.core import MatchConditions
from azure.core.exceptions import (
    AzureError,
    ResourceModifiedError,
    ResourceNotFoundError,
)
from azure.storage.blob import (
    BlobClient,
    BlobSasPermissions,
    BlobServiceClient,
    ContainerClient,
    ContainerSasPermissions,
    generate_blob_sas,
    generate_container_sas,
)

from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_NAME,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
)
from app.configuration.redis import get_redis
from app.utils.encryption import (
    DecryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

MAX_EXAMPLE_NOTE_BYTES = 10 * 1024 * 1024
PDF_CONTENT_TYPE = "application/pdf"
PDF_SIGNATURE = b"%PDF-"
EXAMPLE_NOTE_UPLOAD_TTL_SECONDS = 10 * 60
LEGACY_EXAMPLE_NOTE_UPLOAD_TTL_SECONDS = 10 * 60

_MAX_ISSUE_ATTEMPTS = 3
_MAX_LEGACY_BLOB_KEY_BYTES = 1024
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PENDING_BLOB_KEY_RE = re.compile(r"^pending/[A-Za-z0-9_-]{43}\.pdf$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REGISTERED_REFERENCE_PREFIX = "example-note:v1:"
_MAX_ETAG_LENGTH = 256

# Only the request which loaded this exact encrypted intent may consume it.
# This makes registration single-use without allowing another account to
# invalidate the rightful owner's upload session.
_ATOMIC_CONSUME_MATCHING_INTENT = """
local value = redis.call('GET', KEYS[1])
if not value or value ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


@dataclass(frozen=True)
class ExampleNoteUploadValidationError(ValueError):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class ExampleNoteBlobIntegrityError(RuntimeError):
    """A bounded worker failure which never contains a blob name or content."""

    code: str = "example_note_blob_integrity_check_failed"

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class IssuedExampleNoteUpload:
    upload_url: str
    registration_token: str


@dataclass(frozen=True)
class _PendingExampleNoteUpload:
    blob_key: str
    doctor_id: str


@dataclass(frozen=True)
class RegisteredExampleNoteBlob:
    blob_key: str
    doctor_id: str
    etag: str
    size: int
    sha256: str


def _invalid_pdf(
    detail: str = "Example notes must be valid PDF files.",
) -> NoReturn:
    raise ExampleNoteUploadValidationError(
        status_code=415,
        detail=detail,
    )


def _upload_session_error() -> NoReturn:
    raise ExampleNoteUploadValidationError(
        status_code=409,
        detail="The example note upload session is invalid or expired. Please upload it again.",
    )


def _redis_key(registration_token: str) -> str:
    digest = pseudonymous_digest(
        registration_token,
        context="example-note-upload-registration",
    )
    return f"example-note:upload:{digest}"


def _blob_client(blob_key: str) -> BlobClient:
    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )
    return service_client.get_blob_client(
        container=AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
        blob=blob_key,
    )


def _decode_redis_value(raw_value: object) -> str | None:
    if isinstance(raw_value, bytes):
        try:
            return raw_value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return raw_value if isinstance(raw_value, str) else None


def _encode_pending_intent(intent: _PendingExampleNoteUpload) -> str:
    payload = json.dumps(
        {
            "blob_key": intent.blob_key,
            "doctor_id": intent.doctor_id,
            "version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return encrypt_value(payload)


def _decode_pending_intent(value: str) -> _PendingExampleNoteUpload | None:
    try:
        payload = json.loads(decrypt_value(value))
    except (DecryptionError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "blob_key",
        "doctor_id",
        "version",
    }:
        return None
    blob_key = payload.get("blob_key")
    doctor_id = payload.get("doctor_id")
    if (
        payload.get("version") != 1
        or not isinstance(blob_key, str)
        or _PENDING_BLOB_KEY_RE.fullmatch(blob_key) is None
        or not isinstance(doctor_id, str)
        or not doctor_id
    ):
        return None
    return _PendingExampleNoteUpload(blob_key=blob_key, doctor_id=doctor_id)


def issue_example_note_upload(doctor_id: str) -> IssuedExampleNoteUpload:
    """Issue a short-lived create-only SAS for one server-owned blob key."""

    if not doctor_id:
        raise ValueError("invalid doctor reference")

    redis = get_redis()
    for _ in range(_MAX_ISSUE_ATTEMPTS):
        blob_key = f"pending/{secrets.token_urlsafe(32)}.pdf"
        registration_token = secrets.token_urlsafe(32)
        intent = _PendingExampleNoteUpload(blob_key=blob_key, doctor_id=doctor_id)
        if not redis.set(
            _redis_key(registration_token),
            _encode_pending_intent(intent),
            nx=True,
            ex=EXAMPLE_NOTE_UPLOAD_TTL_SECONDS,
        ):
            continue

        now = datetime.now(timezone.utc)
        sas_token = generate_blob_sas(
            account_name=AZURE_UPLOADS_ACCOUNT_NAME,
            account_key=AZURE_UPLOADS_ACCOUNT_KEY,
            container_name=AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
            blob_name=blob_key,
            permission=BlobSasPermissions(create=True),
            protocol="https",
            start=now - timedelta(minutes=1),
            expiry=now + timedelta(seconds=EXAMPLE_NOTE_UPLOAD_TTL_SECONDS),
        )
        return IssuedExampleNoteUpload(
            upload_url=f"{_blob_client(blob_key).url}?{sas_token}",
            registration_token=registration_token,
        )

    raise RuntimeError("example_note_upload_issue_failed")


def issue_legacy_example_note_upload_url() -> str:
    """Issue the cached-SPA upload grant during the v2 rollout window.

    The legacy browser chooses the final blob name, so this grant cannot be
    scoped to one exact blob. Keep it short-lived, HTTPS-only, create-only,
    and limited to the de-identified example-notes container. The companion
    registration endpoint separately enforces the authenticated Clerk owner.
    """

    now = datetime.now(timezone.utc)
    sas_token = generate_container_sas(
        account_name=AZURE_UPLOADS_ACCOUNT_NAME,
        account_key=AZURE_UPLOADS_ACCOUNT_KEY,
        container_name=AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
        permission=ContainerSasPermissions(create=True),
        protocol="https",
        start=now - timedelta(minutes=1),
        expiry=now + timedelta(seconds=LEGACY_EXAMPLE_NOTE_UPLOAD_TTL_SECONDS),
    )
    return ContainerClient(
        container_name=AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
        account_url=AZURE_UPLOADS_ACCOUNT_URL,
        credential=sas_token,
    ).url


def _load_pending_intent(
    registration_token: str,
) -> tuple[_PendingExampleNoteUpload, str]:
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


def _etag(properties: object) -> str | None:
    value = getattr(properties, "etag", None)
    if not isinstance(value, str) or not value or len(value) > _MAX_ETAG_LENGTH:
        return None
    return value


def _validate_uploaded_blob(
    intent: _PendingExampleNoteUpload,
) -> RegisteredExampleNoteBlob:
    blob_client = _blob_client(intent.blob_key)
    try:
        properties = blob_client.get_blob_properties()
    except ResourceNotFoundError:
        raise ExampleNoteUploadValidationError(
            status_code=400,
            detail="The uploaded example note could not be found. Please upload it again.",
        ) from None
    except AzureError:
        raise ExampleNoteUploadValidationError(
            status_code=503,
            detail="The uploaded example note could not be verified. Please try again.",
        ) from None

    etag = _etag(properties)
    if etag is None:
        _invalid_pdf("The uploaded PDF is incomplete.")
    size = getattr(properties, "size", None)
    if not isinstance(size, int) or size <= 0:
        _invalid_pdf("The uploaded PDF is empty or incomplete.")
    if size > MAX_EXAMPLE_NOTE_BYTES:
        raise ExampleNoteUploadValidationError(
            status_code=413,
            detail="Example note PDFs must be 10 MB or smaller.",
        )
    if _content_type(properties) != PDF_CONTENT_TYPE:
        _invalid_pdf(
            "Example notes must have the application/pdf content type.",
        )

    try:
        content = blob_client.download_blob(
            offset=0,
            length=MAX_EXAMPLE_NOTE_BYTES + 1,
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        ).readall()
    except ResourceNotFoundError:
        raise ExampleNoteUploadValidationError(
            status_code=400,
            detail="The uploaded example note could not be found. Please upload it again.",
        ) from None
    except ResourceModifiedError:
        raise ExampleNoteUploadValidationError(
            status_code=409,
            detail="The uploaded example note changed during verification. Please upload it again.",
        ) from None
    except AzureError:
        raise ExampleNoteUploadValidationError(
            status_code=503,
            detail="The uploaded example note could not be verified. Please try again.",
        ) from None

    if not isinstance(content, bytes) or len(content) != size:
        _invalid_pdf("The uploaded PDF is incomplete.")
    if not content.startswith(PDF_SIGNATURE):
        _invalid_pdf()

    return RegisteredExampleNoteBlob(
        blob_key=intent.blob_key,
        doctor_id=intent.doctor_id,
        etag=etag,
        size=size,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def consume_validated_example_note_upload(
    registration_token: str,
    doctor_id: str,
) -> RegisteredExampleNoteBlob:
    """Validate and atomically consume one upload owned by ``doctor_id``."""

    intent, stored_value = _load_pending_intent(registration_token)
    if not secrets.compare_digest(intent.doctor_id, doctor_id):
        _upload_session_error()

    try:
        registered = _validate_uploaded_blob(intent)
    except ExampleNoteUploadValidationError as exc:
        if exc.status_code != 503:
            # Invalid uploads are deliberately left in the random pending
            # namespace. Deletion/lifecycle policy needs separate operational
            # approval; consuming the token prevents them from being registered.
            _consume_pending_intent(registration_token, stored_value)
        raise

    if not _consume_pending_intent(registration_token, stored_value):
        _upload_session_error()
    return registered


def validate_legacy_example_note_upload(
    file_name: str,
    *,
    doctor_id: str,
    owner_clerk_id: str,
) -> RegisteredExampleNoteBlob:
    """Validate an owner-stamped blob submitted by a cached pre-v2 SPA.

    This compatibility path deliberately accepts only the legacy filename
    shape. Successful registrations are converted to the same immutable,
    ETag/hash-bound reference used by v2 before they are queued.
    """

    owner_prefix = f"{owner_clerk_id}_"
    encoded_name = file_name.encode("utf-8")
    if (
        not file_name
        or len(encoded_name) > _MAX_LEGACY_BLOB_KEY_BYTES
        or file_name != os.path.basename(file_name)
        or "\\" in file_name
        or not file_name.lower().endswith(".pdf")
    ):
        _invalid_pdf("Example notes must use a .pdf file name.")
    if (
        not owner_clerk_id
        or len(file_name) <= len(owner_prefix)
        or not secrets.compare_digest(file_name[: len(owner_prefix)], owner_prefix)
    ):
        raise ExampleNoteUploadValidationError(
            status_code=403,
            detail="The uploaded example note does not belong to this account.",
        )

    return _validate_uploaded_blob(
        _PendingExampleNoteUpload(blob_key=file_name, doctor_id=doctor_id)
    )


def serialize_registered_blob(blob: RegisteredExampleNoteBlob) -> str:
    payload = json.dumps(
        {
            "blob_key": blob.blob_key,
            "doctor_id": blob.doctor_id,
            "etag": blob.etag,
            "sha256": blob.sha256,
            "size": blob.size,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return _REGISTERED_REFERENCE_PREFIX + encrypt_value(payload)


def _deserialize_registered_blob(
    reference: str,
) -> RegisteredExampleNoteBlob | None:
    if not reference.startswith(_REGISTERED_REFERENCE_PREFIX):
        return None
    ciphertext = reference.removeprefix(_REGISTERED_REFERENCE_PREFIX)
    try:
        payload = json.loads(decrypt_value(ciphertext))
    except (DecryptionError, json.JSONDecodeError, TypeError):
        raise ExampleNoteBlobIntegrityError from None
    if not isinstance(payload, dict) or set(payload) != {
        "blob_key",
        "doctor_id",
        "etag",
        "sha256",
        "size",
    }:
        raise ExampleNoteBlobIntegrityError
    blob_key = payload.get("blob_key")
    doctor_id = payload.get("doctor_id")
    etag = payload.get("etag")
    size = payload.get("size")
    sha256 = payload.get("sha256")
    if (
        not isinstance(blob_key, str)
        or _PENDING_BLOB_KEY_RE.fullmatch(blob_key) is None
        or not isinstance(doctor_id, str)
        or not doctor_id
        or not isinstance(etag, str)
        or not etag
        or len(etag) > _MAX_ETAG_LENGTH
        or not isinstance(size, int)
        or not 0 < size <= MAX_EXAMPLE_NOTE_BYTES
        or not isinstance(sha256, str)
        or _SHA256_RE.fullmatch(sha256) is None
    ):
        raise ExampleNoteBlobIntegrityError
    return RegisteredExampleNoteBlob(blob_key, doctor_id, etag, size, sha256)


def _download_registered_blob(blob: RegisteredExampleNoteBlob) -> bytes:
    client = _blob_client(blob.blob_key)
    try:
        properties = client.get_blob_properties(
            etag=blob.etag,
            match_condition=MatchConditions.IfNotModified,
        )
        if (
            _etag(properties) != blob.etag
            or getattr(properties, "size", None) != blob.size
            or _content_type(properties) != PDF_CONTENT_TYPE
        ):
            raise ExampleNoteBlobIntegrityError
        content = client.download_blob(
            offset=0,
            length=MAX_EXAMPLE_NOTE_BYTES + 1,
            etag=blob.etag,
            match_condition=MatchConditions.IfNotModified,
        ).readall()
    except ExampleNoteBlobIntegrityError:
        raise
    except AzureError:
        raise ExampleNoteBlobIntegrityError from None

    if (
        not isinstance(content, bytes)
        or len(content) != blob.size
        or not content.startswith(PDF_SIGNATURE)
        or not secrets.compare_digest(hashlib.sha256(content).hexdigest(), blob.sha256)
    ):
        raise ExampleNoteBlobIntegrityError
    return content


def _download_legacy_blob(reference: str) -> bytes:
    """Bound and validate historical rows while retaining basename compatibility."""

    blob_key = os.path.basename(reference)
    if not blob_key or "\\" in blob_key or not blob_key.lower().endswith(".pdf"):
        raise ExampleNoteBlobIntegrityError
    client = _blob_client(blob_key)
    try:
        properties = client.get_blob_properties()
        size = getattr(properties, "size", None)
        etag = _etag(properties)
        if (
            not isinstance(size, int)
            or not 0 < size <= MAX_EXAMPLE_NOTE_BYTES
            or etag is None
            or _content_type(properties) != PDF_CONTENT_TYPE
        ):
            raise ExampleNoteBlobIntegrityError
        content = client.download_blob(
            offset=0,
            length=MAX_EXAMPLE_NOTE_BYTES + 1,
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        ).readall()
    except ExampleNoteBlobIntegrityError:
        raise
    except AzureError:
        raise ExampleNoteBlobIntegrityError from None
    if (
        not isinstance(content, bytes)
        or len(content) != size
        or not content.startswith(PDF_SIGNATURE)
    ):
        raise ExampleNoteBlobIntegrityError
    return content


def download_registered_or_legacy_example_note(
    reference: str,
    *,
    doctor_id: str,
) -> bytes:
    """Return only the exact registered bytes, with bounded legacy support."""

    registered = _deserialize_registered_blob(reference)
    if registered is None:
        return _download_legacy_blob(reference)
    if not secrets.compare_digest(registered.doctor_id, doctor_id):
        raise ExampleNoteBlobIntegrityError
    return _download_registered_blob(registered)
