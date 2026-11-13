"""Dormant, pure Scribe transition contract. No provider or application imports."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import runpy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

CONTRACT = runpy.run_path(str(Path(__file__).with_name("staging_release_contract.py")))
API = "cosmaneura-staging-app"
PHASES = ("cosmaneura-staging-worker", "cosmaneura-staging-scheduler", API)
LEDGER = "SCRIBE_CANCELLATION_LEDGER_READY"
FIELDS = (
    CONTRACT["NATIVE_FIELDS"]
    | CONTRACT["ACTIVATION_FIELDS"]
    | CONTRACT["CANCELLATION_FIELDS"]
)
STATE_KEYS = {
    "name",
    "revision",
    "template",
    "configuration",
    "identity",
    "secret_values",
}
BINDING_KEYS = {
    "candidate_sha",
    "controller_sha",
    "approval_pr",
    "founder",
    "identity_sha256",
    "schema_before",
    "schema_after",
    "candidate_image",
}
IMAGE = (
    r"cosmaneurastagingregistry\.azurecr\.io/cosmaneura/cosmaneura@sha256:[0-9a-f]{64}"
)


def require(condition: object) -> None:
    if not condition:
        raise ValueError("staging_scribe_initialization_refused")


def digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
    )


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(type(key) is str and key not in result)
        result[key] = value
    return result


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None)
    return parsed.astimezone(UTC)


def environment(state: dict[str, Any]) -> dict[str, dict[str, str]]:
    require(set(state) == STATE_KEYS and state["name"] in PHASES)
    name = state["name"]
    require(
        type(state["revision"]) is str
        and re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", state["revision"])
    )
    require(type(state["identity"]) is dict and state["identity"])
    template = cast(dict[str, Any], state["template"])
    configuration = cast(dict[str, Any], state["configuration"])
    require(type(template) is dict and type(configuration) is dict)
    containers = template["containers"]
    require(type(containers) is list and len(containers) == 1)
    container = cast(dict[str, Any], containers[0])
    require(type(container) is dict and re.fullmatch(IMAGE, container["image"]))
    command = container.get("command")
    require(
        command is None
        or (type(command) is list and all(type(value) is str for value in command))
    )
    require(
        type(container["args"]) is list
        and all(type(value) is str for value in container["args"])
    )
    entries = cast(list[dict[str, str]], container["env"])
    require(type(entries) is list)
    result: dict[str, dict[str, str]] = {}
    secrets = cast(dict[str, str], state["secret_values"])
    require(
        type(secrets) is dict
        and all(type(k) is str and type(v) is str for k, v in secrets.items())
    )
    objects = cast(list[dict[str, Any]], configuration["secrets"])
    require(
        type(objects) is list
        and all(type(item) is dict and type(item["name"]) is str for item in objects)
    )
    names = [item["name"] for item in objects]
    require(len(names) == len(set(names)) and set(names) == set(secrets))
    for entry in entries:
        require(
            type(entry) is dict
            and set(entry)
            in (
                {"name", "value"},
                {"name", "secretRef"},
                {"name", "secretRef", "value"},
            )
        )
        key = entry["name"]
        require(
            type(key) is str
            and re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            and key not in result
        )
        if "secretRef" in entry:
            require(
                key not in FIELDS
                and type(entry["secretRef"]) is str
                and entry["secretRef"] == key.lower().replace("_", "-")
                and (
                    "value" not in entry
                    or (type(entry["value"]) is str and entry["value"] == "")
                )
            )
            require(entry["secretRef"] in secrets)
        else:
            require(type(entry["value"]) is str)
        result[key] = entry
    present = set(result) & FIELDS
    if name != API:
        require(present <= {LEDGER})
    else:
        for group in (
            CONTRACT["NATIVE_FIELDS"],
            CONTRACT["ACTIVATION_FIELDS"],
            CONTRACT["CANCELLATION_FIELDS"],
        ):
            require(not present & group or group <= present)
    return result


def controls_valid(controls: dict[str, str], api: dict[str, Any]) -> None:
    require(type(controls) is dict and set(controls) == FIELDS)
    require(
        all(type(value) is str and len(value) < 2048 for value in controls.values())
    )
    CONTRACT["validate_native_groups"](controls)
    require(controls[LEDGER] == "true")
    public = environment(api)
    bindings = {
        name: public[name]["value"] for name in CONTRACT["NATIVE_BINDING_FIELDS"]
    }
    CONTRACT["validate_native_binding"](controls | bindings)


def transformed(
    before: dict[str, Any], controls: dict[str, str], image: str, revision: str
) -> dict[str, Any]:
    env = environment(before)
    require(
        re.fullmatch(IMAGE, image)
        and re.fullmatch(re.escape(before["name"]) + r"--[a-z0-9-]+", revision)
    )
    target = copy.deepcopy(before)
    target["revision"] = revision
    target["template"]["revisionSuffix"] = revision.split("--", 1)[1]
    container = target["template"]["containers"][0]
    container["image"] = image
    selected = FIELDS if before["name"] == API else {LEDGER}
    # The bound Azure CLI update fills an absent value with an empty string on
    # retained secret references. Model that raw representation explicitly;
    # the reference, resolved secret bytes and ordering remain unchanged.
    for item in container["env"]:
        if "secretRef" in item and "value" not in item:
            item["value"] = ""
    # Preserve every unowned entry and its order. Complete controls include
    # explicit empty public values; no other field is removed or normalized.
    container["env"] = [
        (
            {"name": item["name"], "value": controls[item["name"]]}
            if item["name"] in selected
            else item
        )
        for item in container["env"]
    ]
    container["env"].extend(
        {"name": key, "value": controls[key]} for key in sorted(selected - env.keys())
    )
    environment(target)
    return target


def bindings_valid(bindings: dict[str, Any]) -> None:
    require(type(bindings) is dict and set(bindings) == BINDING_KEYS)
    require(
        bindings["founder"] == "JohnClem"
        and type(bindings["approval_pr"]) is int
        and bindings["approval_pr"] > 0
    )
    for key in ("candidate_sha", "controller_sha"):
        require(
            type(bindings[key]) is str and re.fullmatch(r"[0-9a-f]{40}", bindings[key])
        )
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", bindings["identity_sha256"]))
    require(re.fullmatch(IMAGE, bindings["candidate_image"]))
    for key in ("schema_before", "schema_after"):
        require(re.fullmatch(r"[0-9a-f]{12}", bindings[key]))


def state_row(state: dict[str, Any]) -> dict[str, str]:
    return {
        "revision": state["revision"],
        "image": state["template"]["containers"][0]["image"],
        "sha256": digest(state),
        "template_sha256": digest(state["template"]),
        "configuration_sha256": digest(state["configuration"]),
        "identity_sha256": digest(state["identity"]),
        "private_sha256": digest(state["secret_values"]),
    }


def prepare_plan(
    before: dict[str, Any],
    controls: dict[str, str],
    bindings: dict[str, Any],
    revisions: dict[str, str],
    recovery: dict[str, Any],
    created_at: str,
    expires_at: str,
) -> dict[str, Any]:
    """Prepare hash-only bindings from synthetic or protected in-memory state."""
    bindings_valid(bindings)
    require(set(before) == set(revisions) == set(PHASES))
    for name, state in before.items():
        environment(state)
        require(state["name"] == name)
    controls_valid(controls, before[API])
    baseline_api = environment(before[API])
    for key in (
        "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED",
        "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED",
        "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED",
        "SCRIBE_CANCELLATION_ENABLED",
    ):
        require(key not in baseline_api or baseline_api[key]["value"] == "false")
    require(
        set(recovery) == {"image", "source_sha", "compatibility_sha256", "revisions"}
    )
    require(
        recovery["source_sha"] == bindings["candidate_sha"]
        and re.fullmatch(IMAGE, recovery["image"])
    )
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", recovery["compatibility_sha256"]))
    require(set(recovery["revisions"]) == set(PHASES))
    start, end = timestamp(created_at), timestamp(expires_at)
    require(start < end <= start + timedelta(hours=48))
    closed = controls | {
        "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED": "false",
        "SCRIBE_ACTIVATION_PRACTICE_REFS": "",
        "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED": "false",
        "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED": "false",
        "SCRIBE_CANCELLATION_ENABLED": "false",
    }
    require(
        closed["SCRIBE_NATIVE_WORKLOAD_ENABLED"]
        == closed["SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED"]
        == "true"
    )
    desired = {
        name: transformed(state, controls, bindings["candidate_image"], revisions[name])
        for name, state in before.items()
    }
    recovery_states = {
        name: transformed(state, closed, recovery["image"], recovery["revisions"][name])
        for name, state in before.items()
    }
    plan = {
        "schema": "scribe_public_initialization_v1",
        "enabled": True,
        "bindings": bindings,
        "created_at": created_at,
        "expires_at": expires_at,
        "controls": controls,
        "before": {name: state_row(state) for name, state in before.items()},
        "desired": {name: state_row(state) for name, state in desired.items()},
        "before_sha256": digest(before),
        "desired_sha256": digest(desired),
        "recovery": recovery
        | {
            "controls": closed,
            "states": {
                name: state_row(state) for name, state in recovery_states.items()
            },
            "sha256": digest(recovery_states),
        },
    }
    return copy.deepcopy(plan)


def check_plan(plan: dict[str, Any], context: dict[str, Any], now: datetime) -> None:
    require(
        set(plan)
        == {
            "schema",
            "enabled",
            "bindings",
            "created_at",
            "expires_at",
            "controls",
            "before",
            "desired",
            "before_sha256",
            "desired_sha256",
            "recovery",
        }
    )
    require(
        plan["schema"] == "scribe_public_initialization_v1" and plan["enabled"] is True
    )
    bindings_valid(plan["bindings"])
    require(set(context) == BINDING_KEYS | {"schema_revision", "plan_sha256"})
    require({key: context[key] for key in BINDING_KEYS} == plan["bindings"])
    require(context["plan_sha256"] == digest(plan))
    require(timestamp(plan["created_at"]) <= now < timestamp(plan["expires_at"]))
    require(
        timestamp(plan["expires_at"]) - timestamp(plan["created_at"])
        <= timedelta(hours=48)
    )
    require(set(plan["controls"]) == FIELDS and plan["controls"][LEDGER] == "true")
    CONTRACT["validate_native_groups"](plan["controls"])
    require(
        set(plan["recovery"])
        == {
            "image",
            "source_sha",
            "compatibility_sha256",
            "revisions",
            "controls",
            "states",
            "sha256",
        }
    )
    require(plan["recovery"]["source_sha"] == plan["bindings"]["candidate_sha"])
    require(re.fullmatch(IMAGE, plan["recovery"]["image"]))
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", plan["recovery"]["compatibility_sha256"])
    )
    for group in (plan["before"], plan["desired"], plan["recovery"]["states"]):
        require(set(group) == set(PHASES))
        for name, row in group.items():
            require(
                set(row)
                == {
                    "revision",
                    "image",
                    "sha256",
                    "template_sha256",
                    "configuration_sha256",
                    "identity_sha256",
                    "private_sha256",
                }
            )
            require(re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", row["revision"]))
            require(re.fullmatch(IMAGE, row["image"]))
            require(
                all(
                    re.fullmatch(r"sha256:[0-9a-f]{64}", row[key])
                    for key in set(row) - {"image", "revision"}
                )
            )
    require(
        all(
            row["image"] == plan["bindings"]["candidate_image"]
            for row in plan["desired"].values()
        )
    )
    require(
        all(
            row["image"] == plan["recovery"]["image"]
            for row in plan["recovery"]["states"].values()
        )
    )
    require(
        plan["recovery"]["revisions"]
        == {name: row["revision"] for name, row in plan["recovery"]["states"].items()}
    )
    for key in ("before_sha256", "desired_sha256"):
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", plan[key]))
    closed = plan["recovery"]["controls"]
    require(set(closed) == FIELDS and closed[LEDGER] == "true")
    require(
        closed["SCRIBE_NATIVE_WORKLOAD_ENABLED"]
        == closed["SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED"]
        == "true"
    )
    for key in (
        "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED",
        "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED",
        "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED",
        "SCRIBE_CANCELLATION_ENABLED",
    ):
        require(closed[key] == "false")
    require(closed["SCRIBE_ACTIVATION_PRACTICE_REFS"] == "")
    require(
        all(
            closed[key] == plan["controls"][key]
            for key in FIELDS
            - {
                "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED",
                "SCRIBE_ACTIVATION_PRACTICE_REFS",
                "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED",
                "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED",
                "SCRIBE_CANCELLATION_ENABLED",
            }
        )
    )


def validate_transition(
    plan: dict[str, Any] | None,
    observed: dict[str, Any],
    context: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    if plan is None:
        return {"enabled": False}
    check_plan(plan, context, now)
    require(context["schema_revision"] == plan["bindings"]["schema_before"])
    rebuilt = prepare_plan(
        observed,
        plan["controls"],
        plan["bindings"],
        {name: row["revision"] for name, row in plan["desired"].items()},
        {
            key: plan["recovery"][key]
            for key in ("image", "source_sha", "compatibility_sha256", "revisions")
        },
        plan["created_at"],
        plan["expires_at"],
    )
    require(rebuilt == plan)
    return {
        "enabled": True,
        "plan_sha256": digest(plan),
        "before_sha256": plan["before_sha256"],
        "desired_sha256": plan["desired_sha256"],
        "phase_order": list(PHASES),
    }


def validate_phase(
    plan: dict[str, Any],
    observed: dict[str, Any],
    context: dict[str, Any],
    completed: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    check_plan(plan, context, now)
    require(context["schema_revision"] == plan["bindings"]["schema_after"])
    require(type(completed) is list and len(completed) <= len(PHASES))
    require(set(observed) == set(PHASES))
    for index, name in enumerate(PHASES):
        state = observed[name]
        environment(state)
        expected = (
            plan["desired"][name] if index < len(completed) else plan["before"][name]
        )
        require(state["name"] == name and state_row(state) == expected)
        if index < len(completed):
            require(
                completed[index]
                == {"name": name, "plan_sha256": digest(plan), **expected}
            )
    next_name = PHASES[len(completed)] if len(completed) < len(PHASES) else None
    if next_name is None:
        require(digest(observed) == plan["desired_sha256"])
    return {
        "plan_sha256": digest(plan),
        "next_workload": next_name,
        "complete": next_name is None,
        "public_updates": {
            key: plan["controls"][key]
            for key in sorted(FIELDS if next_name == API else {LEDGER})
        }
        if next_name
        else {},
        "expected_target": plan["desired"][next_name] if next_name else None,
    }


def validate_closed_recovery(
    plan: dict[str, Any],
    observed: dict[str, Any],
    context: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    check_plan(plan, context, now)
    require(
        context["schema_revision"] == plan["bindings"]["schema_after"]
        and set(observed) == set(PHASES)
    )
    for name, state in observed.items():
        environment(state)
        require(
            state["name"] == name
            and state_row(state) == plan["recovery"]["states"][name]
        )
    require(digest(observed) == plan["recovery"]["sha256"])
    require(
        all(
            environment(state)[LEDGER]["value"] == "true" for state in observed.values()
        )
    )
    return {
        "plan_sha256": digest(plan),
        "closed_recovery_sha256": plan["recovery"]["sha256"],
        "ledger_ready": True,
        "raw_template_rollback_allowed": False,
    }


def main() -> None:
    require(len(sys.argv) == 2)
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024)
    payload = json.loads(raw, object_pairs_hook=unique)
    require(set(payload) == {"plan", "observed", "context", "completed"})
    now = datetime.now(UTC)
    functions = {
        "--validate-transition": validate_transition,
        "--validate-closed-recovery": validate_closed_recovery,
    }
    if sys.argv[1] == "--validate-phase":
        result = validate_phase(
            payload["plan"],
            payload["observed"],
            payload["context"],
            payload["completed"],
            now,
        )
    else:
        require(sys.argv[1] in functions and payload["completed"] == [])
        result = functions[sys.argv[1]](
            payload["plan"], payload["observed"], payload["context"], now
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, IndexError):
        raise SystemExit("staging_scribe_initialization_refused") from None
