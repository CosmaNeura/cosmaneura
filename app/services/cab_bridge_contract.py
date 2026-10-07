"""Minimized, exact Core operation and purpose-bound callback contract."""

from typing import Literal

from pydantic import Field

from app.services.billing_header_contract import Closed, Digest

Purpose = Literal[
    "completed-note.accept",
    "header.read",
    "header.generate",
    "header.save",
    "header.generation-status",
]


class CabActor(Closed):
    issuer: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")
    subject: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")


class CabAuthorizationRequest(Closed):
    schemaVersion: Literal["core-cab-authorization-request.v1"]
    purpose: Purpose
    actor: CabActor
    requestSha256: Digest
    sourceContentSha256: Digest
    sourceTextSha256: Digest


class CabGenerationCompletion(Closed):
    schemaVersion: Literal["core-cab-generation-completion.v1"]
    actor: CabActor
    requestSha256: Digest
    sourceContentSha256: Digest
    sourceTextSha256: Digest
    status: Literal["succeeded", "failed", "unknown"]


def canonical(value):
    import json

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    import hashlib

    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def minimized_operation(row):
    return {
        "operationId": str(row.operation_id),
        "practiceId": str(row.practice_id),
        "actor": {
            **{k: row.actor[k] for k in ("issuer", "subject", "sessionSha256")},
            "role": "Provider",
        },
        "source": {**row.source, "revision": row.source_revision},
        "noteText": row.projected_text,
        "visit": row.visit,
        "completion": {
            "status": "completed",
            "completedAt": row.completed_at.isoformat(),
            "confirmedByProvider": True,
        },
        "admission": {
            k: row.admission[k]
            for k in ("admissionId", "dataClass", "reviewRef", "contentSha256")
        },
    }


def workload_request(operation, purpose, command):
    if (
        purpose
        not in (
            "completed-note.accept",
            "header.read",
            "header.generate",
            "header.save",
            "header.generation-status",
        )
        or type(command) is not dict
    ):
        raise ValueError("invalid_cab_action")
    if purpose == "completed-note.accept":
        if set(command) != {"expectedSource"}:
            raise ValueError("invalid_cab_action")
        body = {"operation": operation, "expectedSource": command["expectedSource"]}
    else:
        keys = {"encounterId", "expectedSource"}
        if purpose != "header.read":
            keys.add("operationId")
        if purpose == "header.save":
            keys |= {"headerId", "expectedRevision", "draft", "reason"}
        if purpose == "header.generate" and "responseMode" in command:
            keys.add("responseMode")
            if command["responseMode"] != "async":
                raise ValueError("invalid_cab_action")
        if purpose == "header.generate" and "continuationConsent" in command:
            keys.add("continuationConsent")
            if (
                command.get("responseMode") != "async"
                or command["continuationConsent"] is not True
            ):
                raise ValueError("invalid_cab_action")
        if set(command) != keys:
            raise ValueError("invalid_cab_action")
        body = {
            "sourceOperationId": operation["operationId"],
            "practiceId": operation["practiceId"],
            "actor": {k: operation["actor"][k] for k in ("issuer", "subject")},
            "command": command,
        }
    result = {"schemaVersion": "core-cab-workload.v1", "purpose": purpose, **body}
    if len(canonical(result).encode()) > 1_500_000:
        raise ValueError("invalid_cab_action")
    return result
