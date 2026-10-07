"""One bounded server-owned HTTPS request; failures never trigger an automatic retry."""

import json
import time
from typing import Callable

import httpx
from azure.identity import ManagedIdentityCredential

from app.configuration import cab_bridge as configuration
from app.services.cab_bridge_contract import canonical, digest


class CabUnavailable(RuntimeError):
    def __init__(self, status=503):
        self.status = status
        super().__init__(
            "cab_action_not_confirmed" if status == 503 else "cab_action_rejected"
        )


def configured():
    p = configuration.CAB_AUTHORIZE_POLICY
    s = configuration.CAB_BRIDGE_SETTINGS
    return s.enabled and s.is_complete() and p.enabled and p.is_complete()


def _source(value, operation, purpose):
    source = value["source"]
    if source is None:
        if (
            purpose != "completed-note.accept"
            or value["result"].get("current") is not False
        ):
            raise ValueError()
        return
    required = {
        "encounterId",
        "encounterVersion",
        "headerSource",
        "noteText",
        "intake",
        "transcript",
        "visitContext",
        "completion",
        "projection",
        "inferencePolicy",
    }
    if type(source) is not dict or set(source) != required:
        raise ValueError()
    expected = {
        "version": operation["source"]["projectionVersion"],
        **{
            k: operation["source"][k]
            for k in ("excludedRecordingTime", "contentSha256", "textSha256")
        },
    }
    pin = source["headerSource"]
    pin_keys = {
        "encounterId",
        "encounterVersion",
        "ingestionSource",
        "noteId",
        "noteVersion",
        "noteSha256",
        "supportingSourceDigests",
        "bundleSha256",
    }
    policy = source["inferencePolicy"]
    if (
        source["noteText"] != operation["noteText"]
        or source["intake"] != ""
        or source["transcript"] != ""
        or canonical(source["visitContext"]) != canonical(operation["visit"])
        or canonical(source["completion"]) != canonical(operation["completion"])
        or canonical(source["projection"]) != canonical(expected)
        or type(pin) is not dict
        or set(pin) != pin_keys
        or pin.get("encounterId") != source["encounterId"]
        or type(source["encounterVersion"]) is not int
        or source["encounterVersion"] < 1
        or type(pin.get("encounterVersion")) is not int
        or pin.get("encounterVersion") != source["encounterVersion"]
        or pin.get("ingestionSource") != "provider_completed_note"
        or pin.get("noteSha256") != operation["source"]["textSha256"]
        or type(policy) is not dict
        or type(policy.get("allowed")) is not bool
        or policy
        not in (
            {"allowed": True, "reason": None},
            {"allowed": False, "reason": "operational_source_model_not_approved"},
        )
    ):
        raise ValueError()


class CabClient:
    def __init__(
        self, settings=None, *, transport=None, token_provider: Callable | None = None
    ):
        self.settings = settings or configuration.CAB_BRIDGE_SETTINGS
        if not self.settings.enabled or not self.settings.is_complete():
            raise CabUnavailable()
        self.transport, self.token_provider = transport, token_provider

    def invoke(self, payload, operation):
        purpose = payload["purpose"]
        path = (
            "/api/core/v1/completed-notes"
            if purpose == "completed-note.accept"
            else "/api/core/v1/header/" + purpose.split(".")[1]
        )
        asynchronous = purpose == "header.generation-status" or (
            purpose == "header.generate"
            and payload["command"].get("responseMode") == "async"
        )
        deadline = 75 if purpose == "header.generate" and not asynchronous else 30
        try:
            started = time.monotonic()
            if self.token_provider is None:
                with ManagedIdentityCredential(
                    client_id=self.settings.managed_identity_client_id,
                    retry_total=0,
                    connection_timeout=3,
                    read_timeout=3,
                ) as credential:
                    token = credential.get_token(
                        "api://" + self.settings.audience + "/.default"
                    ).token
            else:
                token = self.token_provider()
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 16384
                or any(c.isspace() for c in token)
            ):
                raise ValueError()
            with httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(deadline, connect=3),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    self.settings.origin + path,
                    content=canonical(payload).encode(),
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise CabUnavailable(
                            response.status_code
                            if response.status_code in {401, 403, 404, 409, 422}
                            else 503
                        )
                    if (
                        response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                        != "application/json"
                    ):
                        raise ValueError()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if (
                            len(data) > 1_500_000
                            or time.monotonic() - started > deadline
                        ):
                            raise ValueError()

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result

            value = json.loads(
                data,
                object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if (
                type(value) is not dict
                or set(value)
                != {
                    "schemaVersion",
                    "purpose",
                    "sourceOperationId",
                    "requestSha256",
                    "result",
                    "source",
                }
                or value["schemaVersion"] != "core-cab-result.v1"
                or value["purpose"] != purpose
                or value["sourceOperationId"] != operation["operationId"]
                or value["requestSha256"] != digest(payload)
                or type(value["result"]) is not dict
            ):
                raise ValueError()
            result = value["result"]
            if purpose == "completed-note.accept":
                if (
                    set(result)
                    != {
                        "schemaVersion",
                        "receipt",
                        "replayed",
                        "current",
                        "launchPath",
                        "generation",
                    }
                    or result["schemaVersion"] != "cab.completed-note-handoff.v1"
                    or type(result["replayed"]) is not bool
                    or type(result["current"]) is not bool
                    or result["launchPath"] is not None
                    or result["generation"] != "not_started"
                ):
                    raise ValueError()
                receipt = result["receipt"]
                if (
                    type(receipt) is not dict
                    or set(receipt)
                    != {
                        "operationId",
                        "receiptId",
                        "acceptedAt",
                        "encounterId",
                        "encounterVersion",
                        "sourceRevision",
                        "sourceManifestSha256",
                        "headerSource",
                    }
                    or receipt["operationId"] != operation["operationId"]
                    or type(receipt["sourceRevision"]) is not int
                    or receipt["sourceRevision"] != operation["source"]["revision"]
                ):
                    raise ValueError()
                if result["current"] and (
                    value["source"] is None
                    or any(
                        receipt[k] != value["source"][k]
                        for k in ("headerSource", "encounterId", "encounterVersion")
                    )
                ):
                    raise ValueError()
            elif asynchronous:
                generation = result.get("generation")
                if (
                    set(result) != {"generation", "view", "created"}
                    or type(generation) is not dict
                    or set(generation) != {"operationId", "status"}
                    or generation["operationId"] != payload["command"]["operationId"]
                    or generation["status"]
                    not in {"running", "succeeded", "failed", "unknown"}
                    or type(result["created"]) is not bool
                    or (
                        generation["status"] == "succeeded"
                        and type(result["view"]) is not dict
                    )
                    or (
                        generation["status"] != "succeeded"
                        and (
                            result["view"] is not None or result["created"] is not False
                        )
                    )
                ):
                    raise ValueError()
            elif set(result) != (
                {"view"} if purpose == "header.read" else {"view", "created"}
            ):
                raise ValueError()
            elif purpose != "header.read" and type(result["created"]) is not bool:
                raise ValueError()
            _source(value, operation, purpose)
            if purpose != "completed-note.accept" and canonical(
                value["source"]["headerSource"]
            ) != canonical(payload["command"]["expectedSource"]):
                raise ValueError()
            return value
        except CabUnavailable:
            raise
        except Exception:
            raise CabUnavailable() from None
