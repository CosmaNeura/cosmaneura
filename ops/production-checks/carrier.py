"""One-shot Azure Container Apps Jobs carrier. CLI output stays private."""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from admission import INCIDENT_CODES, RECEIPT_FILE, check_context, verify_checks_receipt
from contract import (
    CHECKS_WORKFLOW,
    IMAGE,
    PACKAGE,
    PRODUCTION_JOB,
    SELECTORS,
    Stop,
    encoded_inputs,
    execution_name,
    parse_markers,
    receipt_header,
    require,
    sha256,
    verify_package,
)

PRODUCTION_SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
PRODUCTION_WORKSPACE = "2a7d954b-373c-4de6-884f-c050ecc1d863"
PRODUCTION_APP = "cosmaneura-prod-app"
PYTHON = "/opt/venv/bin/python"
TERMINAL = {"Succeeded", "Failed", "Stopped", "Degraded"}
ACTIVE = {"Running", "Pending", "Processing", "Starting"}
CONTAINERS = "properties.template.containers"
TEMPLATE_QUERY = (
    "{containers:properties.template.containers[].{name:name,image:image,command:command,"
    "args:args,env:env[].{name:name,secretRef:secretRef}},"
    "initContainers:properties.template.initContainers[].name,"
    "trigger:properties.configuration.triggerType,"
    "parallelism:properties.configuration.manualTriggerConfig.parallelism,"
    "completion:properties.configuration.manualTriggerConfig.replicaCompletionCount,"
    "retry:properties.configuration.replicaRetryLimit,timeout:properties.configuration.replicaTimeout}"
)
EXECUTION_QUERY = (
    "{name:name,status:properties.status,containers:properties.template.containers[]."
    "{name:name,image:image,command:command,args:args,env:env[].{name:name,secretRef:secretRef},"
    "nonce:env[?name=='CORE_BUNDLE_Z'].value}}"
)


@dataclass(frozen=True)
class Target:
    subscription: str
    group: str
    job: str
    workspace: str
    image: str
    container: str


PRODUCTION = Target(
    PRODUCTION_SUBSCRIPTION,
    "cosmaneura-prod",
    PRODUCTION_JOB,
    PRODUCTION_WORKSPACE,
    IMAGE,
    PRODUCTION_JOB,
)


class Carrier:
    def __init__(self, target, *, clock=time.monotonic, sleep=time.sleep):
        self.target = target
        self.clock = clock
        self.sleep = sleep

    def az(self, *args, timeout=15, output="json") -> Any:
        # All calls fail closed. Callers catching Stop do cleanup only.
        try:
            result = subprocess.run(
                [
                    "az",
                    *args,
                    "--subscription",
                    self.target.subscription,
                    "--only-show-errors",
                    "--output",
                    output,
                ],
                capture_output=True,
                timeout=timeout,
                check=True,
                env={
                    **{
                        key: value
                        for key, value in os.environ.items()
                        if key != "GH_TOKEN"
                    },
                    "AZURE_CORE_LOG_LEVEL": "error",
                    "AZURE_CORE_COLLECT_TELEMETRY": "no",
                    "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
                },
            )
            require(not result.stderr, "azure_diagnostic")
            return json.loads(result.stdout) if output == "json" else None
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise Stop("azure_command_failed") from error

    def job(self, *args, **options):
        return self.az(
            "containerapp",
            "job",
            *args,
            "--name",
            self.target.job,
            "--resource-group",
            self.target.group,
            **options,
        )

    def serving_selector(self):
        query = (
            f"[].{{weight:properties.trafficWeight,containers:{CONTAINERS}[]."
            "{name:name,image:image,selectors:env[?name=='DATABASE_URL'].{ref:secretRef}}}"
        )
        revisions = self.az(
            "containerapp",
            "revision",
            "list",
            "--name",
            PRODUCTION_APP,
            "--resource-group",
            self.target.group,
            "--query",
            query,
        )
        total, selectors = 0, set()
        for revision in revisions:
            weight = revision["weight"]
            require(
                type(weight) is int and 0 <= weight <= 100, "unknown_traffic_weight"
            )
            if weight:
                web = [
                    item
                    for item in revision["containers"]
                    if item["name"] == f"{PRODUCTION_APP}-web"
                ]
                require(
                    len(web) == 1 and web[0]["image"] == IMAGE, "traffic_image_drift"
                )
                selector = web[0]["selectors"]
                require(
                    len(selector) == 1 and selector[0]["ref"] in SELECTORS,
                    "traffic_database_selector",
                )
                selectors.add(selector[0]["ref"])
                total += weight
        require(total == 100 and len(selectors) == 1, "traffic_not_fully_verified")
        return selectors.pop()

    def template(self):
        value = self.job("show", "--query", TEMPLATE_QUERY)
        require(
            value["trigger"] == "Manual"
            and value["initContainers"] in (None, [])
            and type(value["parallelism"]) is int
            and value["parallelism"] == 1
            and type(value["completion"]) is int
            and value["completion"] == 1
            and len(value["containers"]) == 1
            and value["containers"][0]["name"] == self.target.container,
            "job_template_topology",
        )
        require(
            type(value["retry"]) is int and value["retry"] == 0, "retry_must_be_zero"
        )
        require(
            type(value["timeout"]) is int and value["timeout"] > 0,
            "unknown_replica_timeout",
        )
        return sha256(json.dumps(value, sort_keys=True).encode()), value["timeout"]

    def pages(self, url, host, code, *options, deadline, method="get", body=()):
        seen = set()
        for _ in range(20):
            require(isinstance(url, str) and url not in seen, code)
            link = urlsplit(url)
            require(link.scheme == "https" and link.netloc == host, code)
            seen.add(url)
            timeout = min(15, deadline - self.clock())
            require(timeout > 0, code)
            args = ("rest", "--method", method, "--url", url, *options, *body)
            page = self.az(*args, timeout=timeout)
            require(isinstance(page, dict), code)
            yield page
            url = page.get("nextLink")
            if url is None:
                return
            method, body = "get", ()
        raise Stop(code)

    def executions(self, *, newest_first=False):
        # The CLI execution-list wrapper drops nextLink.
        target = self.target
        url = (
            f"https://management.azure.com/subscriptions/{target.subscription}"
            f"/resourceGroups/{target.group}/providers/Microsoft.App/jobs/{target.job}"
            "/executions?api-version=2025-01-01"
        )
        pages = self.pages(
            url,
            "management.azure.com",
            "execution_list_incomplete",
            "--query",
            "{nextLink:nextLink,rows:value[].{name:name,status:properties.status,"
            "start:properties.startTime}}",
            deadline=self.clock() + 60,
        )
        executions = {}
        rows = []
        for page in pages:
            require(isinstance(page.get("rows"), list), "execution_list_incomplete")
            rows.extend(page["rows"])
        if newest_first:
            rows = sorted(
                rows, key=lambda row: datetime.fromisoformat(row["start"]), reverse=True
            )
        for row in rows:
            name = row["name"]
            require(
                execution_name(name, target.job) and name not in executions,
                "execution_list_invalid",
            )
            executions[name] = row["status"]
        return executions

    def execution(self, name, query, **options):
        require(execution_name(name, self.target.job), "invalid_execution_name")
        return self.job(
            "execution",
            "show",
            "--job-execution-name",
            name,
            "--query",
            query,
            **options,
        )

    def status(self, name, *, timeout=15):
        record = self.execution(
            name, "{name:name,status:properties.status}", timeout=timeout
        )
        require(record["name"] == name, "execution_identity")
        status = record["status"]
        return status if type(status) is str else None

    def preflight(self):
        try:
            executions = self.executions(newest_first=True)
        except (Stop, KeyError, TypeError, ValueError) as error:
            raise Stop("preflight_list_denied") from error
        if executions:
            try:
                self.status(next(iter(executions)))
            except (Stop, KeyError, TypeError) as error:
                raise Stop("preflight_show_denied") from error
        try:
            table = self.query_logs(
                "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(5m) "
                f"| where ContainerName_s == '{self.target.container}' "
                "| summarize count = count()"
            )
            rows = table["rows"]
            require(
                table["columns"] == [{"name": "count", "type": "long"}]
                and len(rows) == 1
                and type(rows[0]) is list
                and len(rows[0]) == 1
                and type(rows[0][0]) is int
                and rows[0][0] >= 0,
                "analytics_response",
            )
        except (Stop, KeyError, TypeError) as error:
            raise Stop("preflight_query_denied") from error
        return (
            {
                "list_executions": True,
                "show_execution": bool(executions),
                "no_prior_execution": not executions,
                "query_workspace": True,
            },
            executions,
        )

    def reconcile(self, before, bootstrap, variables, deadline):
        """Identify only the execution with this invocation's unique override."""
        while self.clock() < deadline:
            new = self.executions().keys() - before.keys()
            require(len(new) <= 1, "incident_multiple_new_executions")
            if new:
                name = next(iter(new))
                record = self.execution(name, EXECUTION_QUERY)
                names = [
                    {
                        "name": key,
                        "secretRef": value.removeprefix("secretref:")
                        if key == "DATABASE_URL"
                        else None,
                    }
                    for key, value in variables.items()
                ]
                expected = {
                    "name": self.target.container,
                    "image": self.target.image,
                    "command": [PYTHON],
                    "args": [bootstrap],
                    "env": sorted(names, key=lambda item: item["name"]),
                    "nonce": [variables["CORE_BUNDLE_Z"]],
                }
                for container in record["containers"]:
                    container["env"] = sorted(
                        container["env"], key=lambda item: item["name"]
                    )
                require(
                    record["name"] == name and record["containers"] == [expected],
                    "incident_execution_not_identified",
                )
                return name
            self.sleep(3)
        raise Stop("incident_execution_not_identified")

    def stop_and_confirm(self, name):
        """Only execution-scoped stop. A failed stop remains an incident."""
        deadline = self.clock() + 60
        failed = False
        try:
            self.job("stop", "--job-execution-name", name, output="none")
        except Stop:
            failed = True
        while self.clock() < deadline:
            try:
                terminal = self.status(name) in TERMINAL
            except (Stop, KeyError, TypeError) as error:
                raise Stop("incident_stop_unconfirmed") from error
            if terminal:
                require(not failed, "incident_stop_failed_terminal")
                return
            self.sleep(3)
        raise Stop("incident_stop_unconfirmed")

    def run_once(self, before, bootstrap, variables, *, wait_seconds=300):
        deadline = self.clock() + wait_seconds
        try:
            start = self.job(
                "start",
                "--container-name",
                self.target.container,
                "--image",
                self.target.image,
                "--command",
                PYTHON,
                "--args=" + bootstrap,
                "--env-vars",
                *(f"{key}={value}" for key, value in variables.items()),
                "--query",
                "{name:name}",
                timeout=60,
            )
            name = start.get("name") if isinstance(start, dict) else None
            require(
                execution_name(name, self.target.job) and name not in before,
                "ambiguous_start",
            )
        except Stop:
            try:
                name = self.reconcile(before, bootstrap, variables, self.clock() + 45)
            except (Stop, KeyError, TypeError) as error:
                raise Stop("incident_start_unresolved") from error
            # The execution is positively identified even if its status read
            # fails. A failed read must still take the scoped cleanup path.
            try:
                terminal = self.status(name) in TERMINAL
            except (Stop, KeyError, TypeError):
                terminal = False
            if not terminal:
                self.stop_and_confirm(name)
            raise Stop("start_response_lost_execution_terminal") from None

        while self.clock() < deadline:
            try:
                status = self.status(name, timeout=min(15, deadline - self.clock()))
            except (Stop, KeyError, TypeError):
                self.stop_and_confirm(name)
                raise Stop("execution_status_unavailable_stopped") from None
            if status in TERMINAL:
                require(status == "Succeeded", "execution_terminal_failure")
                return name
            if status not in ACTIVE:
                self.stop_and_confirm(name)
                raise Stop("execution_unknown_status_stopped")
            self.sleep(min(5, max(0, deadline - self.clock())))
        self.stop_and_confirm(name)
        raise Stop("execution_timed_out_stopped")

    def query_logs(self, query, *, deadline=None):
        pages = self.pages(
            f"https://api.loganalytics.io/v1/workspaces/{self.target.workspace}/query",
            "api.loganalytics.io",
            "analytics_response",
            "--resource",
            "https://api.loganalytics.io",
            method="post",
            body=(
                "--headers",
                "Content-Type=application/json",
                "--body",
                json.dumps({"query": query}),
            ),
            deadline=self.clock() + 60 if deadline is None else deadline,
        )
        columns, rows = None, []
        for page in pages:
            tables = page.get("tables")
            require(
                set(page) <= {"tables", "nextLink"}
                and isinstance(tables, list)
                and len(tables) == 1,
                "analytics_response",
            )
            table = page["tables"][0]
            require(
                isinstance(table, dict)
                and isinstance(table.get("rows"), list)
                and isinstance(table.get("columns"), list)
                and (columns is None or columns == table["columns"]),
                "analytics_response",
            )
            columns = table["columns"]
            rows.extend(table["rows"])
        return {"columns": columns, "rows": rows}

    def finished_logs(self, name):
        require(execution_name(name, self.target.job), "invalid_execution_name")
        # Finished replicas can lose their stream. Use the existing Log
        # Analytics path directly. Scope to this job container and exact
        # execution (or its one replica suffix), never a substring search.
        query = (
            "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(30m) "
            f"| where ContainerName_s == '{self.target.container}' "
            f"| where ContainerGroupName_s matches regex '^{name}(-[a-z0-9]+)?$' "
            "| order by TimeGenerated asc | project log=Log_s"
        )
        deadline = self.clock() + 240
        lines = []
        while self.clock() < deadline:
            table = self.query_logs(query, deadline=deadline)
            require(
                table["columns"] == [{"name": "log", "type": "string"}]
                and all(
                    type(row) is list and len(row) == 1 and type(row[0]) is str
                    for row in table["rows"]
                ),
                "analytics_response",
            )
            lines = [row[0] for row in table["rows"]]
            if len(lines) == 18 and lines[-1] == "RUN_DONE=1":
                break
            self.sleep(min(5, max(0, deadline - self.clock())))
        return lines

    def verify_only_execution(self, before, name):
        after = self.executions()
        require(
            after.keys() - before.keys() == {name}
            and after[name] == "Succeeded"
            and all(state in TERMINAL for state in after.values()),
            "execution_count_or_status_changed",
        )


def run_production(carrier, candidate, run_id, *, checkout_sha, before_start=None):
    verify_package()
    require(checkout_sha == candidate, "checkout_sha")
    variables = encoded_inputs(candidate)
    bootstrap = (PACKAGE / "bootstrap-args.txt").read_bytes().decode()
    selector = carrier.serving_selector()
    template_before = carrier.template()
    preflight, before = carrier.preflight()
    require(all(state in TERMINAL for state in before.values()), "migration_job_busy")
    variables = {"DATABASE_URL": f"secretref:{selector}", **variables}
    if before_start is not None:
        before_start()
    try:
        name = carrier.run_once(before, bootstrap, variables)
        receipt = receipt_header(candidate, run_id, name, selector)
        lines = carrier.finished_logs(name)
        carrier.verify_only_execution(before, name)
    except Exception:
        try:
            require(carrier.template() == template_before, "migration_template_changed")
        except Exception:
            print("Production job template could not be reverified after the failure.")
        raise
    require(carrier.template() == template_before, "migration_template_changed")
    counts, shape = parse_markers(lines)
    receipt.update(
        template_unchanged=True,
        template_sha256=template_before[0],
        retry_limit=0,
        replica_timeout_seconds=template_before[1],
        execution_count=1,
        g2_shape=shape,
        access_counts=counts,
        preflight=preflight,
    )
    require(shape == "match", "g2_shape_differs")
    verify_checks_receipt(receipt, candidate, run_id)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--output", action="store_true")
    mode.add_argument("--admitted", action="store_true")
    args = parser.parse_args()

    def admit():
        require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "run_attempt")
        return check_context(CHECKS_WORKFLOW, os.environ, admitted=not args.output)

    try:
        if args.output or args.admitted:
            candidate = admit()
            if args.output:
                with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                    output.write(f"sha={candidate}\n")
            return 0
        run_id = int(os.environ["GITHUB_RUN_ID"])
        require(run_id > 0, "run_id")
        receipt = run_production(
            Carrier(PRODUCTION),
            os.environ["GITHUB_SHA"],
            run_id,
            checkout_sha=os.environ["CHECKOUT_SHA"],
            before_start=admit,
        )
        path = Path(os.environ["RUNNER_TEMP"]) / RECEIPT_FILE
        with path.open("x") as output:
            output.write(json.dumps(receipt) + "\n")
        print(
            "Production checks passed. Review the immutable production-checks receipt."
        )
    except Stop as error:
        if not (args.output or args.admitted) and str(error) not in INCIDENT_CODES:
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write("execution_reconciled=true\n")
        print(f"Production checks stopped ({error}).")
        return 1
    except Exception:
        print("Production checks stopped (invalid_or_unavailable_metadata).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
