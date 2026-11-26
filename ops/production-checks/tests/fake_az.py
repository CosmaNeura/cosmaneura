"""Fake Azure CLI for the production carrier and staging rehearsal tests."""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

args = sys.argv[1:]
state_file = Path(os.environ["FAKE_AZ_STATE"])
state = json.loads(state_file.read_text())
scenario = state["scenario"]


def option(name):
    return args[args.index(name) + 1]


def save():
    state_file.write_text(json.dumps(state))


def emit(data):
    print(json.dumps(data))


def fail():
    print("SYNTHETIC_PRIVATE_DIAGNOSTIC", file=sys.stderr)
    sys.exit(1)


if args[:3] == ["containerapp", "revision", "list"]:
    image = state["image"] if scenario != "digest" else state["image"] + "wrong"
    weight = 99 if scenario == "traffic" else 100
    emit(
        [
            {
                "weight": weight,
                "containers": [
                    {
                        "name": "cosmaneura-prod-app-web",
                        "image": image,
                        "selectors": [{"ref": "database-url-v2"}],
                    }
                ],
            }
        ]
    )
elif args[:3] == ["containerapp", "job", "show"]:
    emit(
        {
            "containers": [
                {
                    "name": state["container"],
                    "image": "changed"
                    if scenario == "drift" and state["starts"]
                    else "tag",
                }
            ],
            "initContainers": [],
            "trigger": "Manual",
            "parallelism": 1,
            "completion": 1,
            "retry": 1 if scenario == "retry" else 0,
            "timeout": 450,
        }
    )
elif args[:3] == ["rest", "--method", "get"]:
    if scenario == "list_denied":
        fail()
    rows = [
        {
            "name": name,
            "status": row["status"],
            "start": row.get("start", "2026-01-01T00:00:00Z"),
        }
        for name, row in state["executions"].items()
    ]
    emit(
        {
            "next": "https://unused.invalid/next" if scenario == "paginated" else None,
            "rows": rows,
        }
    )
elif args[:3] == ["containerapp", "job", "start"]:
    assert state["preflight_queries"] == 1
    state["starts"] += 1
    name = f"{state['job']}-fake{state['starts']}"
    bootstrap = next(
        arg.removeprefix("--args=") for arg in args if arg.startswith("--args=")
    )
    variables = dict(
        item.split("=", 1)
        for item in args[args.index("--env-vars") + 1 : args.index("--query")]
    )
    assert option("--image") == state["image"]
    assert option("--container-name") == state["container"]
    assert option("--command") == "/opt/venv/bin/python"
    assert "--debug" not in args
    status = "Succeeded"
    if scenario in {
        "timeout",
        "lost",
        "unmatched",
        "stop_failed",
        "stop_unconfirmed",
        "poll_error",
    }:
        status = "Running"
    if scenario == "unknown":
        status = None
    if scenario in {"Failed", "Stopped", "Degraded"}:
        status = scenario
    if "time.sleep" in bootstrap:
        status = "Running"
    if state["staging"]:
        assert set(variables) == {"CORE_BUNDLE_Z", "CORE_MIGRATIONS_Z"}
        payload = variables["CORE_MIGRATIONS_Z"]
        assert len(payload) == 92412
        if "time.sleep" not in bootstrap:
            assert hashlib.sha256(payload.encode()).hexdigest() in bootstrap
    else:
        assert set(variables) == {
            "CORE_BUNDLE_Z",
            "CORE_MIGRATIONS_Z",
            "CORE_RUNNER_Z",
            "DATABASE_URL",
        }
        assert variables["DATABASE_URL"] == "secretref:database-url-v2"
    env = [
        {
            "name": key,
            "secretRef": value.removeprefix("secretref:")
            if key == "DATABASE_URL"
            else None,
        }
        for key, value in reversed(list(variables.items()))
    ]
    state["executions"][name] = {
        "status": status,
        "containers": [
            {
                "name": state["container"],
                "image": state["image"],
                "command": ["/opt/venv/bin/python"],
                "args": [bootstrap],
                "env": env,
                "nonce": [
                    "different"
                    if scenario == "unmatched"
                    else variables["CORE_BUNDLE_Z"]
                ],
            }
        ],
    }
    if scenario == "two_executions":
        state["executions"][name + "other"] = state["executions"][name]
    save()
    if scenario in {"lost", "unmatched"}:
        fail()
    emit({"name": name})
elif args[:4] == ["containerapp", "job", "execution", "show"]:
    name = option("--job-execution-name")
    row = state["executions"][name]
    if not state["starts"]:
        state.setdefault("preflight_shows", []).append(name)
        save()
        if scenario == "show_denied":
            fail()
    if scenario == "poll_error" and not state["stops"]:
        fail()
    emit({"name": name, **row})
elif args[:3] == ["containerapp", "job", "stop"]:
    name = option("--job-execution-name")
    state["stops"].append(name)
    if scenario != "stop_unconfirmed":
        state["executions"][name]["status"] = "Stopped"
    save()
    if scenario == "stop_failed":
        fail()
elif args[:3] == ["rest", "--method", "post"]:
    query = json.loads(option("--body"))["query"]
    if query.endswith("summarize count = count()"):
        assert "TimeGenerated > ago(5m)" in query
        assert f"ContainerName_s == '{state['container']}'" in query
        assert not state["starts"] and not state["stops"]
        state["preflight_queries"] = 1
        save()
        if scenario == "query_denied":
            fail()
        emit(
            {
                "tables": [
                    {"columns": [{"name": "count", "type": "long"}], "rows": [[0]]}
                ]
            }
        )
        sys.exit(0)
    state["log_reads"] += 1
    save()
    assert f"^{state['job']}-fake1(-[a-z0-9]+)?$" in query
    assert f"ContainerName_s == '{state['container']}'" in query
    if scenario == "logs_error":
        fail()
    if scenario == "logs_missing" or (
        scenario == "logs_delayed" and state["log_reads"] == 1
    ):
        rows = []
    else:
        rows = [[line] for line in state["lines"]]
    response: dict[str, Any] = {
        "tables": [{"columns": [{"name": "log", "type": "string"}], "rows": rows}]
    }
    if scenario == "logs_partial":
        response["error"] = {"code": "PartialError"}
    emit(response)
else:
    fail()
