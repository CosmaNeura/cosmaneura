# fmt: off
from __future__ import annotations  # noqa: I001
import os
import re
import subprocess
import sys
def refuse(msg: str) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(1)
def main() -> None:
    run_id = os.environ.get("TERRAFORM_APPLY_RUN_ID", "")
    target = os.environ.get("TARGET_SHA", "")
    if not re.fullmatch(r"[0-9]+", run_id):
        refuse("No successful Terraform Apply is available for this production deploy.")
    raw = subprocess.check_output(
        [
            "gh", "api",
            f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}",
            "--jq", "[.name,.path,.conclusion,.event,.head_branch,.head_sha]|@tsv",
        ],
        text=True,
    ).strip().split("\t")
    if (
        raw[:3] != ["Terraform Apply", ".github/workflows/infra_apply_local.yml", "success"]
        or raw[4] != "master"
    ):
        refuse("The supplied run is not a successful Terraform Apply on master.")
    if raw[3] != "workflow_dispatch":
        refuse("The supplied Terraform Apply run has an invalid trigger.")
    apply_sha = raw[5]
    if not apply_sha or subprocess.call(["git", "merge-base", "--is-ancestor", apply_sha, target]) != 0:
        refuse("The supplied Terraform Apply commit is not an ancestor of this deploy.")
    paths = [
        "infra", ".env", ".env.production.backend", ".env.production.frontend", ".envrc",
        ".tool-versions", "Justfile", "just", "Procfile", ".github/actions",
        ".github/workflows/infra_apply_local.yml", ".github/workflows/infra_production_plan_local.yml",
        ".github/workflows/build_and_publish.yml", ".github/workflows/build_and_publish_local.yml",
        ".github/workflows/release_verified_candidate.yml", ".github/workflows/infra_production_plan.yml",
        ".github/workflows/infra_apply.yml", ".github/workflows/build_and_publish_manual.yml",
        ".env.shared", "docker-compose.yml", "scripts/local_release_runner.py",
        "scripts/local_ci_services.py", "scripts/local-runner", "scripts/production_core_candidate.py",
        "scripts/production_core_runtime_binding.py", "scripts/verify_release_verified_candidate.py",
        "scripts/release_verified_candidate_ops.py", "scripts/release_verified_candidate_gate.py",
        "scripts/session_prep_production_frontend_admission.py",
        "scripts/superadmin_capability_config_preflight.py",
    ]
    if subprocess.call(["git", "diff", "--quiet", apply_sha, target, "--", *paths]) != 0:
        refuse("Production infrastructure changed after the supplied Terraform Apply.")
if __name__ == "__main__":
    main()
