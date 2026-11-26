"""Operator-only rehearsal against a pre-authorized existing synthetic staging job."""

import argparse
import json
import re
import sys

import runner
from carrier import PRODUCTION, PRODUCTION_APP, TERMINAL, Carrier, Target
from contract import (
    REVIEWED_SOURCE,
    Stop,
    encoded_inputs,
    require,
    sha256,
    verify_package,
)
from probe_image import STAGING_IMAGE

UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
STAGING_NAME = r"(?:cn-stg-[a-z0-9-]+|cosmaneura-staging(?:-[a-z0-9]+)+)"
TIMEOUT_WAIT = 40
SYNTHETIC_START = "REHEARSAL_START=1"
SYNTHETIC_DONE = "REHEARSAL_PAYLOAD_OK=1"


def staging_target(args):
    production_names = {PRODUCTION.job, PRODUCTION.container, PRODUCTION_APP}
    require(
        args.resource_group == "cosmaneura-staging"
        and re.fullmatch(UUID, args.subscription)
        and re.fullmatch(UUID, args.workspace)
        and args.workspace != PRODUCTION.workspace
        and all(
            name not in production_names and "prod" not in name
            for name in (args.job, args.container)
        )
        and re.fullmatch(STAGING_NAME, args.job)
        and re.fullmatch(STAGING_NAME, args.container)
        and not args.image.startswith("cosmaneuraprodregistry")
        and re.fullmatch(STAGING_IMAGE, args.image),
        "staging_target_required",
    )
    return Target(
        args.subscription,
        args.resource_group,
        args.job,
        args.workspace,
        args.image,
        args.container,
    )


def rehearsal(carrier):
    verify_package()
    inputs = encoded_inputs(REVIEWED_SOURCE)
    variables = {key: inputs[key] for key in ("CORE_BUNDLE_Z", "CORE_MIGRATIONS_Z")}
    payload = variables["CORE_MIGRATIONS_Z"]
    require(90_000 <= len(payload) <= 95_000, "rehearsal_payload_size")
    expected = sha256(payload.encode())
    command = (
        "-cimport base64,hashlib,json,os,zlib;"
        f"print({SYNTHETIC_START!r},flush=True);"
        "p=os.environ['CORE_MIGRATIONS_Z'];"
        f"assert hashlib.sha256(p.encode()).hexdigest()=={expected!r};"
        "d=json.loads(zlib.decompress(base64.b64decode(p)));"
        "assert type(d) is dict and len(d)==97;"
        "manifest=json.dumps(sorted((path,hashlib.sha256(t.encode()).hexdigest()) for path,t in d.items()));"
        f"assert hashlib.sha256(manifest.encode()).hexdigest()=={runner.EXPECT_MIGRATIONS_MANIFEST!r};"
        f"print({SYNTHETIC_DONE!r},flush=True)"
    )
    template = carrier.template()
    require(template[1] > TIMEOUT_WAIT, "rehearsal_replica_timeout_too_short")
    preflight, before = carrier.preflight()
    require(all(state in TERMINAL for state in before.values()), "staging_job_busy")
    try:
        name = carrier.run_once(before, command, variables)
        require(
            carrier.finished_logs(name) == [SYNTHETIC_START, SYNTHETIC_DONE],
            "rehearsal_finished_log_invalid",
        )
        carrier.verify_only_execution(before, name)
    finally:
        require(carrier.template() == template, "rehearsal_template_changed")

    before = carrier.executions()
    require(all(state in TERMINAL for state in before.values()), "staging_job_busy")
    try:
        try:
            carrier.run_once(
                before,
                "-cimport time;time.sleep(600)",
                variables,
                wait_seconds=TIMEOUT_WAIT,
            )
        except Stop as error:
            require(
                str(error) == "execution_timed_out_stopped", "rehearsal_timeout_cleanup"
            )
        else:
            raise Stop("rehearsal_timed_out_execution_succeeded")
        after = carrier.executions()
        new = after.keys() - before.keys()
        require(
            len(new) == 1 and after[next(iter(new))] == "Stopped",
            "rehearsal_timeout_execution_count",
        )
    finally:
        require(carrier.template() == template, "rehearsal_template_changed")
    return {
        "payload_bytes": len(payload),
        "retry_limit": 0,
        "replica_timeout_seconds": template[1],
        "template_unchanged": True,
        "finished_log_readable": True,
        "timeout_stopped": True,
        "preflight": preflight,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "subscription",
        "resource-group",
        "job",
        "container",
        "workspace",
        "image",
    ):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(rehearsal(Carrier(staging_target(args)))))
    except Stop as error:
        print(f"Staging rehearsal stopped ({error}).")
        return 1
    except Exception:
        print("Staging rehearsal stopped (invalid_or_unavailable_metadata).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
