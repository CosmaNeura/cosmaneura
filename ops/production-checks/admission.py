"""Credential-free production admission and provenance checks via GitHub."""

import argparse
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from contract import CHECKS_WORKFLOW, Stop, exact_sha, require, sha256, verify_receipt

REPOSITORY = "CosmaNeura/cosmaneura"
REF = "refs/heads/master"
FOUNDER = "JohnClem"
WORKFLOW_DIR = ".github/workflows/"
API = f"https://api.github.com/repos/{REPOSITORY}/"
RECEIPT_FILE = "production-checks-receipt.json"
WORKFLOWS = (
    "infra_production_plan_local.yml",
    "infra_apply_local.yml",
    "release_verified_candidate.yml",
    "build_and_publish_local.yml",
    CHECKS_WORKFLOW,
)


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path, *, archive=False):
    request = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(NoRedirects())
    try:
        response = opener.open(request, timeout=15)
    except urllib.error.HTTPError as error:
        with error:
            require(archive and error.code == 302, "github_metadata_unavailable")
            location = error.headers.get("Location", "")
        url = urllib.parse.urlsplit(location)
        require(
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and url.port in (None, 443),
            "github_artifact_location",
        )
        # The archive URL is pre-signed. Never forward the GitHub token.
        response = opener.open(location, timeout=15)
    with response:
        limit = 64 * 1024 if archive else 2 * 1024 * 1024
        body = response.read(limit + 1)
    require(len(body) <= limit, "github_response_size")
    return body if archive else json.loads(body)


def check_context(workflow, env, lookup=None, *, admitted=False):
    lookup = lookup or api
    candidate = env.get("GITHUB_SHA")
    require(exact_sha(candidate), "run_sha")
    expected = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/{WORKFLOW_DIR}{workflow}@{REF}",
        "GITHUB_WORKFLOW_SHA": candidate,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": REF,
        "GITHUB_ACTOR": FOUNDER,
        "TRIGGERING_ACTOR": FOUNDER,
    }
    require(
        all(env.get(key) == value for key, value in expected.items()), "run_context"
    )
    if workflow == CHECKS_WORKFLOW:
        require(env.get("GITHUB_RUN_ATTEMPT") == "1", "checks_rerun")
    if admitted:
        require(env.get("ADMITTED_SHA") == candidate, "admission_metadata")
    master = lookup("git/ref/heads/master")
    require(
        master["ref"] == REF
        and master["object"]["type"] == "commit"
        and master["object"]["sha"] == candidate,
        "stale_master",
    )
    return candidate


def unique_fields(fields):
    result = dict(fields)
    require(len(result) == len(fields), "receipt_duplicate_fields")
    return result


def require_production_receipt(candidate, lookup=api):
    # GitHub returns newest first. Include pending or failed runs so an incident
    # cannot fall back to a previous green run of the same commit.
    runs = lookup(
        f"actions/workflows/{CHECKS_WORKFLOW}/runs?"
        f"branch=master&event=workflow_dispatch&head_sha={candidate}&per_page=1"
    )
    require(len(runs["workflow_runs"]) == 1, "receipt_missing")
    run = runs["workflow_runs"][0]
    require(
        run["path"] == WORKFLOW_DIR + CHECKS_WORKFLOW
        and run["event"] == "workflow_dispatch"
        and run["head_branch"] == "master"
        and run["head_sha"] == candidate
        and run["repository"]["full_name"] == REPOSITORY
        and run["head_repository"]["full_name"] == REPOSITORY
        and run["actor"]["login"] == run["triggering_actor"]["login"] == FOUNDER
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        and type(run["run_attempt"]) is int
        and run["run_attempt"] == 1,
        "receipt_run_provenance",
    )
    run_id = run["id"]
    require(type(run_id) is int and run_id > 0, "receipt_run_id")
    jobs = lookup(f"actions/runs/{run_id}/attempts/1/jobs?per_page=10")
    require(
        jobs["total_count"] == 2
        and len(jobs["jobs"]) == 2
        and {job["name"] for job in jobs["jobs"]}
        == {"Authorize production checks", "Execute production checks"}
        and all(job["conclusion"] == "success" for job in jobs["jobs"]),
        "receipt_jobs",
    )
    listing = lookup(f"actions/runs/{run_id}/artifacts?per_page=10")
    require(
        listing["total_count"] == 1 and len(listing["artifacts"]) == 1,
        "receipt_artifact_count",
    )
    artifact = listing["artifacts"][0]
    digest = artifact.get("digest")
    require(
        artifact["name"] == f"production-checks-receipt-{run_id}-{candidate}"
        and artifact["expired"] is False
        and type(artifact["id"]) is int
        and artifact["id"] > 0
        and isinstance(digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
        "receipt_artifact_metadata",
    )
    archive = lookup(f"actions/artifacts/{artifact['id']}/zip", archive=True)
    require(f"sha256:{sha256(archive)}" == digest, "receipt_artifact_digest")
    with zipfile.ZipFile(io.BytesIO(archive)) as compressed:
        require(
            compressed.namelist() == [RECEIPT_FILE]
            and compressed.getinfo(RECEIPT_FILE).file_size <= 8192,
            "receipt_archive",
        )
        receipt = json.loads(
            compressed.read(RECEIPT_FILE), object_pairs_hook=unique_fields
        )
    verify_receipt(receipt, candidate, run_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", choices=WORKFLOWS)
    parser.add_argument("--admitted", action="store_true")
    parser.add_argument("--receipt", action="store_true")
    parser.add_argument("--output", action="store_true")
    args = parser.parse_args()
    try:
        candidate = check_context(args.workflow, os.environ, admitted=args.admitted)
        if args.receipt:
            require_production_receipt(candidate)
        if args.output:
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write(f"sha={candidate}\n")
    except Stop as error:
        print(f"Production admission stopped ({error}).")
        return 1
    except Exception:
        print("Production admission stopped (unavailable_or_invalid_metadata).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
