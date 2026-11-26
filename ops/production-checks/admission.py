"""Check hosted production workflow admission against fresh master."""

import argparse
import json
import os
import urllib.request

from contract import Stop, exact_sha, require

REPOSITORY = "CosmaNeura/cosmaneura"
REF = "refs/heads/master"
FOUNDER = "JohnClem"
WORKFLOW_DIR = ".github/workflows/"
API = f"https://api.github.com/repos/{REPOSITORY}/"
WORKFLOWS = ("infra_production_plan_local.yml", "infra_apply_local.yml")


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path):
    request = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(NoRedirects())
    limit = 2 * 1024 * 1024
    with opener.open(request, timeout=15) as response:
        body = response.read(limit + 1)
    require(len(body) <= limit, "github_response_size")
    return json.loads(body)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", choices=WORKFLOWS)
    parser.add_argument("--admitted", action="store_true")
    parser.add_argument("--output", action="store_true")
    args = parser.parse_args()
    try:
        candidate = check_context(args.workflow, os.environ, admitted=args.admitted)
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
