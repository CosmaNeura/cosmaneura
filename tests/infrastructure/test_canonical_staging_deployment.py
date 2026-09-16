from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy_staging_candidate.yml"
LEGACY_WORKFLOW = ROOT / ".github" / "workflows" / "build_and_publish_manual.yml"
COMMON_SETUP = ROOT / ".github" / "actions" / "common-setup" / "action.yml"
LOCAL_PUBLISHER = ROOT / "infra-staging" / "publish-local.sh"
UNTAGGED_UPLOADER = ROOT / "scripts" / "staging_acr_untagged_upload.py"
BOUND_ACR = ROOT / "scripts" / "staging_acr_cli_bound.py"
BOUND_AZURE_CLI = ROOT / "scripts" / "staging_azure_cli_bound.py"


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job(source: str, name: str) -> str:
    start = source.index(f"  {name}:\n")
    match = re.search(r"\n  [a-z][a-z0-9_-]*:\n", source[start + 1 :])
    if match is None:
        return source[start:]
    return source[start : start + 1 + match.start()]


def _step(job: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = job.index(marker)
    end = job.find("\n      - ", start + len(marker))
    return job[start:] if end == -1 else job[start:end]


def test_workflow_is_manual_only_non_cancelling_and_requires_release_contract() -> None:
    source = _source()
    trigger = source[: source.index("permissions:")]
    assert "  workflow_dispatch:" in trigger
    assert "\n  push:" not in trigger
    assert "\n  pull_request:" not in trigger
    for required_input in (
        "candidate_sha",
        "approval_pr_number",
        "synthetic_only_confirmed",
        "synthetic_evidence_sha256",
        "staging_identity_fingerprints_sha256",
        "expected_schema_before",
        "expected_schema_after",
        "recovery_digest",
    ):
        assert f"      {required_input}:" in trigger
    assert "group: cosmaneura-staging-container-apps" in source
    assert "cancel-in-progress: false" in source


def test_legacy_and_canonical_staging_paths_are_ref_disjoint() -> None:
    legacy = LEGACY_WORKFLOW.read_text(encoding="utf-8")
    authorization = _job(legacy, "authorize-staging")
    legacy_deploy = _job(legacy, "deploy-staging")
    canonical_deploy = _job(_source(), "deploy")

    assert "name: Hold legacy staging deployment for canonical cutover" not in legacy
    assert "github.ref == 'refs/heads/staging'" in authorization
    assert '"$DEPLOYMENT_REF" != "refs/heads/staging"' in authorization
    assert "environment:" not in authorization
    assert "github.ref == 'refs/heads/staging'" in legacy_deploy
    assert "environment: staging" in legacy_deploy
    assert "refs/heads/master" not in authorization + legacy_deploy

    assert "github.ref == 'refs/heads/master'" in canonical_deploy
    assert "environment: staging" in canonical_deploy
    identity = _step(
        canonical_deploy, "Verify observed staging Azure identity, trust, and scope"
    )
    assert "repo:CosmaNeura/cosmaneura:environment:staging" in identity
    assert "refs/heads/staging" not in identity


def test_authorization_is_founder_only_protected_master_and_secretless() -> None:
    authorize = _job(_source(), "authorize")
    assert "\n    if:" not in authorize
    assert "scripts/verify_staging_candidate.py" in authorize
    assert "CANDIDATE_SHA: ${{ inputs.candidate_sha }}" in authorize
    assert "APPROVAL_PR_NUMBER: ${{ inputs.approval_pr_number }}" in authorize
    assert "EXPECTED_SCHEMA_AFTER: ${{ inputs.expected_schema_after }}" in authorize
    assert "^sha256:[0-9a-f]{64}$" in authorize
    assert "^[0-9a-f]{12}$" in authorize
    assert "SYNTHETIC_ONLY_CONFIRMED" in authorize
    assert "environment:" not in authorize
    assert "id-token: write" not in authorize
    assert "secrets." not in authorize


def test_candidate_jobs_checkout_and_test_the_exact_authorized_sha() -> None:
    source = _source()
    for name in ("backend-quality", "backend-tests", "web"):
        job = _job(source, name)
        assert "needs: authorize" in job
        prerequisites = _step(
            job,
            (
                "Install protected-master backend test prerequisites"
                if name != "web"
                else "Install protected-master frontend test prerequisites"
            ),
        )
        candidate = _step(job, "Check out exact candidate")
        verification = _step(job, "Verify exact candidate checkout")
        candidate_toolchain = _step(
            job, "Install exact candidate toolchain without shared caches"
        )
        candidate_environment = _step(job, "Load exact candidate test environment")
        assert "ref: ${{ needs.authorize.outputs.verified_revision }}" in candidate
        assert job.index(prerequisites) < job.index(candidate)
        assert (
            job.index(candidate)
            < job.index(verification)
            < job.index(candidate_toolchain)
            < job.index(candidate_environment)
        )
        assert 'cache: "false"' in candidate_toolchain
        assert "github-action-direnv-load-and-mask@" in candidate_environment
        assert "uses: ./.github/actions/common-setup" not in job
        assert job.count("persist-credentials: false") == 1
        assert 'test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"' in job
        if name == "backend-tests":
            localias_install = _step(job, "Install SHA-verified Localias")
            assert (
                job.index(verification)
                < job.index(localias_install)
                < job.index(candidate_toolchain)
            )
            candidate_routing = _step(
                job, "Start exact candidate SHA-verified local test routing"
            )
            assert "bash scripts/install_pinned_localias.sh" in localias_install
            assert "bash scripts/start_pinned_localias_ci.sh" in candidate_routing
            assert "matrix.shard == 'integration'" in localias_install
            assert "matrix.shard == 'integration'" in candidate_routing
            assert (
                job.index("just py_setup")
                < job.index(candidate_routing)
                < job.index("just db_migrate")
            )
            candidate_commands = (
                "just up --fast",
                "just py_setup",
                "just py_test-ci-${{ matrix.shard }}",
            )
        elif name == "backend-quality":
            candidate_commands = (
                "just up --fast",
                "just py_setup",
                "just py_lint",
                "just db_lint",
            )
        else:
            candidate_commands = ("just js_setup", "pnpm run openapi", "just js_test")
        for command in candidate_commands:
            assert job.index(command) > job.index(verification)
    assert "needs.authorize.outputs.candidate_sha" not in source
    assert "just py_lint" in _job(source, "backend-quality")
    assert "just db_migrate" in _job(source, "backend-quality")
    assert "just db_lint" in _job(source, "backend-quality")
    assert "just py_test-ci-${{ matrix.shard }}" in _job(source, "backend-tests")
    assert "name: Candidate Backend" in _job(source, "app")
    assert "needs: [backend-quality, backend-tests]" in _job(source, "app")
    assert "pnpm run openapi" in _job(source, "web")
    assert "just js_lint" in _job(source, "web")
    assert "just js_test" in _job(source, "web")


def test_candidate_build_and_protected_publication_use_fresh_jobs() -> None:
    source = _source()
    config = _job(source, "config")
    build = _job(source, "build")
    publish = _job(source, "publish")
    deploy = _job(source, "deploy")
    assert "needs: [authorize, app, web]" in config
    assert "environment: staging" in config
    assert "secrets.OP_STAGING_SERVICE_ACCOUNT_TOKEN" in config
    assert "needs: [authorize, app, web, config]" in build
    assert "environment: staging" not in build
    assert "secrets." not in build
    assert "id-token: write" not in build
    assert "needs: [authorize, app, web, config, build]" in publish
    assert "environment: staging" in publish
    assert "id-token: write" not in publish
    assert "needs: [authorize, app, web, config, build, publish]" in deploy
    assert "environment: staging" in deploy
    assert "id-token: write" in deploy
    assert '"$ENABLE_STAGING_PILOT_ZOOM_SKIP" != "false"' in deploy
    for job in (publish, deploy):
        assert '"$ENABLE_PROVIDER_V7" != "true"' in job
        assert '"$ENABLE_SOVEREIGN_RECORD_GRANTS" != "true"' in job
    assert "name: Check out protected-master deployment controls" in deploy
    assert "ref: ${{ github.sha }}" in deploy
    assert "uses: ./.github/actions/common-setup" in deploy
    assert "apt-packages: zsh" in deploy
    build_setup = _step(
        build, "Set up protected-master build runner without shared caches"
    )
    nixpacks_setup = _step(build, "Install protected-master Nixpacks tooling")
    candidate_checkout = _step(build, "Check out exact candidate for image build")
    assert 'enable_cache: "false"' in build_setup
    assert "ref: ${{ needs.authorize.outputs.verified_revision }}" in candidate_checkout
    assert "path: candidate" in candidate_checkout
    assert (
        build.index(build_setup)
        < build.index(nixpacks_setup)
        < build.index(candidate_checkout)
    )
    candidate_build = _step(
        build, "Build exact candidate image without protected credentials"
    )
    assert "working-directory: candidate" in candidate_build
    assert "CONTAINER_REGISTRY_HOST: cache.invalid" in candidate_build
    assert "just build" in candidate_build
    assert "docker save" in build
    assert "actions/upload-artifact@" in build
    assert "actions/download-artifact@" in publish
    assert "docker load --input candidate-image.tar" in publish
    assert publish.index("Verify and load candidate image archive") < publish.index(
        "Log in to staging registry"
    )
    assert "working-directory: candidate" not in publish
    assert "just build" not in publish
    assert "OP_STAGING_SERVICE_ACCOUNT_TOKEN" not in publish
    assert "azure/login" not in publish
    assert "just build" not in deploy
    assert "just build_push" not in source


def test_candidate_revision_is_verified_before_checkout_and_caches_are_disabled() -> (
    None
):
    source = _source()
    authorize = _job(source, "authorize")
    common_setup = COMMON_SETUP.read_text(encoding="utf-8")

    assert (
        "verified_revision: ${{ steps.verify.outputs.verified_revision }}" in authorize
    )
    assert (
        "verification_result=$(python3 scripts/verify_staging_candidate.py)"
        in authorize
    )
    assert ".candidate_sha | select(" in authorize
    assert '"$verified_revision" != "$CANDIDATE_SHA"' in authorize
    assert 'echo "verified_revision=$verified_revision"' in authorize
    assert "candidate_sha: ${{ steps.verify.outputs.candidate_sha }}" not in authorize

    assert "enable_cache:" in common_setup
    assert 'default: "true"' in common_setup
    assert "cache: ${{ inputs.enable_cache }}" in common_setup
    assert "if: inputs.enable_cache == 'true'" in common_setup

    for name in ("backend-quality", "backend-tests", "web", "build"):
        job = _job(source, name)
        candidate_marker = (
            "      - name: Check out exact candidate for image build\n"
            if name == "build"
            else "      - name: Check out exact candidate\n"
        )
        candidate_offset = job.index(candidate_marker)
        if name == "build":
            assert 'enable_cache: "false"' in job[:candidate_offset]
        else:
            assert 'cache: "false"' in job[candidate_offset:]
        assert "uses: ./.github/actions/" not in job[candidate_offset:]
        assert "uses: actions/cache" not in job[candidate_offset:]


def test_provenance_is_rechecked_before_each_protected_boundary() -> None:
    source = _source()
    config = _job(source, "config")
    publish = _job(source, "publish")
    deploy = _job(source, "deploy")
    config_credential_gate = config.index(
        "- name: Reverify candidate before protected configuration credentials"
    )
    first_config_secret = config.index("secrets.OP_STAGING_SERVICE_ACCOUNT_TOKEN")
    publication_credential_gate = publish.index(
        "- name: Reverify candidate before protected credentials"
    )
    first_publication_secret = publish.index("secrets.CONTAINER_REGISTRY_HOST")
    publish_gate = publish.index(
        "- name: Reverify candidate before immutable image publication"
    )
    registry_login = publish.index("- name: Log in to staging registry")
    publication = publish.index('docker push "$candidate_image"')
    azure_credential_gate = deploy.index(
        "- name: Reverify candidate before Azure credentials"
    )
    first_azure_secret = deploy.index("secrets.AZURE_CLIENT_ID")
    mutation_gate = deploy.index(
        "- name: Reverify candidate before migrations and Azure mutation"
    )
    mutation = deploy.index(
        'bash infra-staging/deploy.sh "$CANDIDATE_SHA" "$IMAGE_DIGEST"'
    )
    assert config_credential_gate < first_config_secret
    assert publication_credential_gate < first_publication_secret
    assert publish_gate < registry_login < publication
    assert azure_credential_gate < first_azure_secret
    assert mutation_gate < mutation
    assert publish.count("scripts/verify_staging_candidate.py") == 2
    assert config.count("scripts/verify_staging_candidate.py") == 1
    assert deploy.count("scripts/verify_staging_candidate.py") == 2
    assert source.count("python3 scripts/verify_staging_candidate.py") == 6


def test_publication_is_sha_only_and_deployment_is_digest_pinned() -> None:
    source = _source()
    publish_job = _job(source, "publish")
    deploy = _job(source, "deploy")
    publish = _step(
        publish_job, "Publish candidate SHA tag and resolve immutable digest"
    )
    assert (
        'candidate_image="$REGISTRY_HOST/cosmaneura/cosmaneura:$CANDIDATE_SHA"'
        in publish
    )
    assert 'docker push "$candidate_image"' in publish
    assert "immutable candidate tag already exists" in publish
    assert "^sha256:[0-9a-f]{64}$" in publish
    assert "docker buildx imagetools inspect" in publish
    assert '"$candidate_image" --raw | sha256sum' in publish
    assert ":latest" not in publish
    assert "build_push" not in publish
    assert "docker load --input candidate-image.tar" in publish_job
    assert "EXPECTED_ARCHIVE_SHA256" in publish_job
    deploy_step = _step(
        deploy, "Deploy candidate digest with protected-master controls"
    )
    assert (
        'bash infra-staging/deploy.sh "$CANDIDATE_SHA" "$IMAGE_DIGEST"' in deploy_step
    )
    assert "CONTAINER_REGISTRY_PASSWORD" not in deploy_step
    assert "CONTAINER_REGISTRY_USERNAME" not in deploy_step
    for field in (
        "EXPECTED_SCHEMA_BEFORE",
        "EXPECTED_SCHEMA_AFTER",
        "EXPECTED_RECOVERY_DIGEST",
        "EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256",
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
    ):
        assert f"          {field}:" in deploy_step


def test_evidence_is_allowlisted_non_secret_and_always_uploaded() -> None:
    deploy = _job(_source(), "deploy")
    evidence = _step(deploy, "Assemble non-secret staging release evidence")
    deploy_script = (ROOT / "infra-staging" / "deploy.sh").read_text(encoding="utf-8")
    for field in (
        "workflow_control_sha",
        "source_sha",
        "synthetic_evidence_sha256",
        "image_digest",
        "schema_before",
        "schema_after",
        "workloads_before",
        "workloads_after",
        "observed_configuration",
        "recovery_digest",
        "ENABLE_PROVIDER_V7",
        "ENABLE_SOVEREIGN_RECORD_GRANTS",
        "ENABLE_STAGING_PILOT_ZOOM_SKIP",
        "ZOOM_WATCHDOG_ENABLED",
        "healthcheck_http_status",
        "smoke_result",
    ):
        assert field in evidence
    assert "steps.deploy.outputs.schema_before" in evidence
    assert "steps.deploy.outputs.schema_after" in evidence
    assert "steps.deploy.outputs.source_sha" in evidence
    assert "steps.deploy.outputs.recovery_digest" in evidence
    assert "steps.deploy.outputs.workloads_before" in evidence
    assert "steps.deploy.outputs.workloads_after" in evidence
    assert "steps.deploy.outputs.configuration_evidence" in evidence
    assert "if: always()" in evidence
    assert ".containerCount == 1" in evidence
    assert '(.secretRefs | type == "object")' in evidence
    assert "containerCount: ($revision.containers | length)" in deploy_script
    assert "secretRefs: $secret_refs" in deploy_script
    assert "deployment_outcome" in evidence
    assert "release_contract_result" in evidence
    assert "secrets." not in evidence
    smoke_gate = _step(deploy, "Require staging health smoke to pass")
    assert "if: always()" in smoke_gate
    assert '"$DEPLOYMENT_OUTCOME" != "success"' in smoke_gate
    assert '"$SMOKE_RESULT" != "passed"' in smoke_gate
    assert '"$HEALTHCHECK_HTTP_STATUS" != "200"' in smoke_gate
    upload = _step(deploy, "Upload non-secret staging release evidence")
    assert "if: always()" in upload
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in upload
    assert "if-no-files-found: error" in upload
    assert "retention-days: 90" in upload
    attempt = _job(_source(), "attempt_evidence")
    assert "needs: [authorize, app, web, config, build, publish, deploy]" in attempt
    assert "if: ${{ always() }}" in attempt
    assert "secrets." not in attempt
    assert "staging_identity_fingerprints_sha256" in attempt
    for output in (
        "needs.build.outputs.archive_sha256",
        "needs.build.outputs.image_id",
        "needs.publish.outputs.image_digest",
        "needs.deploy.outputs.schema_before",
        "needs.deploy.outputs.schema_after",
        "needs.deploy.outputs.workloads_before",
        "needs.deploy.outputs.workloads_after",
        "needs.deploy.outputs.configuration_evidence",
        "needs.deploy.outputs.smoke_result",
        "needs.deploy.outputs.release_contract_result",
    ):
        assert output in attempt
    terminal = _step(attempt, "Require a complete successful staging attempt")
    assert '"$result" == "success"' in terminal
    assert '"$DEPLOYMENT_CONTRACT_RESULT" == "passed"' in terminal
    assert '"$SMOKE_RESULT" == "passed"' in terminal
    assert (
        "HEALTHCHECK_HTTP_STATUS: ${{ needs.deploy.outputs.healthcheck_http_status }}"
        in terminal
    )
    assert (
        "OBSERVED_CONFIGURATION: ${{ needs.deploy.outputs.configuration_evidence }}"
        in terminal
    )
    assert '"$SOURCE_SHA" == "$CANDIDATE_SHA_INPUT"' in terminal
    assert '"$SCHEMA_BEFORE" == "$EXPECTED_SCHEMA_BEFORE_INPUT"' in terminal
    assert '"$SCHEMA_AFTER" == "$EXPECTED_SCHEMA_AFTER_INPUT"' in terminal
    assert '"$RECOVERY_DIGEST" == "$RECOVERY_DIGEST_INPUT"' in terminal
    assert "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@" in terminal
    assert 'type == "array" and length == 3' in terminal
    assert "^sha256:[0-9a-f]{64}$" in terminal
    assert "^[0-9a-f]{40}$" in terminal
    assert "^[0-9a-f]{12}$" in terminal
    upload_attempt = _step(attempt, "Upload complete staging workflow-attempt evidence")
    assert "if: always()" in upload_attempt
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in attempt


def test_identity_fingerprint_digest_contract_is_canonical_across_files() -> None:
    workflow = _source()
    deploy = (ROOT / "infra-staging" / "deploy.sh").read_text(encoding="utf-8")

    assert "^sha256:[0-9a-f]{64}$" in workflow
    assert "^sha256:[0-9a-f]{64}$" in deploy
    assert 'fingerprint_set_sha256 = "sha256:" +' in deploy
    assert (
        "EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256: "
        "${{ needs.authorize.outputs.staging_identity_fingerprints_sha256 }}"
    ) in workflow


def test_azure_identity_is_pinned_and_legacy_branch_trust_is_rejected() -> None:
    deploy = _job(_source(), "deploy")
    identity = _step(deploy, "Verify observed staging Azure identity, trust, and scope")
    assert "45b940bf-2f4f-4b10-bcc3-4679d8855a6f" in deploy
    assert "a94600b7-c854-4121-bbcb-a0fa7baae14a" in identity
    assert "d51754d0-bba5-4810-820e-afac542844d6" in deploy
    assert "bab2a709-1fcb-4c87-a9fc-802f7477ec13" in deploy
    assert "identity federated-credential list" in identity
    assert "length == 1" in identity
    assert "repo:CosmaNeura/cosmaneura:environment:staging" in identity
    assert "refs/heads/staging" not in identity
    assert "role assignment list" in identity
    assert "--include-groups" in identity
    assert "--include-inherited" in identity
    assert "roleDefinitionId" in identity
    assert "startswith($staging_scope" not in identity
    assert "az account list" in identity


def test_local_founder_identity_fallback_is_explicit_and_fail_closed() -> None:
    workflow = _source()
    deploy = (ROOT / "infra-staging" / "deploy.sh").read_text(encoding="utf-8")
    bound_azure = BOUND_AZURE_CLI.read_text(encoding="utf-8")

    assert (
        'STAGING_DEPLOYMENT_IDENTITY_MODE="${STAGING_DEPLOYMENT_IDENTITY_MODE:-service-principal}"'
        in deploy
    )
    assert 'case "$STAGING_DEPLOYMENT_IDENTITY_MODE" in' in deploy
    assert "service-principal)" in deploy
    assert "founder-user)" in deploy
    assert 'STAGING_APPROVAL_PR_NUMBER="${STAGING_APPROVAL_PR_NUMBER:-}"' in deploy
    assert 'STAGING_BUILD_PLAN="${STAGING_BUILD_PLAN:-}"' in deploy
    assert (
        'EXPECTED_STAGING_BUILD_PLAN_SHA256="${EXPECTED_STAGING_BUILD_PLAN_SHA256:-}"'
        in deploy
    )
    assert deploy.count("verify_founder_release_source") == 3
    assert deploy.count("verify_azure_deployment_identity") == 3
    assert "scripts/staging_build_controller.py" in deploy
    assert '--verify-source "$STAGING_BUILD_PLAN"' in deploy
    assert 'FOUNDER_APPROVAL_PR_NUMBER="385"' in deploy
    assert 'FOUNDER_EXPECTED_SCHEMA_AFTER="9a2399212488"' in deploy
    assert 'FOUNDER_CANDIDATE_SHA="866bd4bc01e0e7be32c59eccdaf9dac3f495c7de"' in deploy
    assert "--actor" not in deploy
    assert "--triggering-actor" not in deploy
    assert "JohnClem" not in deploy
    assert "staging_azure_cli_bound.py" in deploy
    assert "account get-access-token" in deploy
    assert "https://management.azure.com/" in deploy
    assert (
        "acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a"
        in bound_azure
    )
    assert (
        "0839f64960f7e378762f2f1aab2d324b197c4c0d2198f87d9f595df4eb0b1f93"
        in bound_azure
    )
    assert 'username = claims.get("email")' in bound_azure
    assert 'claims.get("idtyp") == "user"' in bound_azure
    assert "Profile.get_login_credentials = get_login_credentials" in bound_azure
    assert "Profile.get_raw_token = get_raw_token" in bound_azure
    assert "token_record(3, MANAGEMENT_AUDIENCES)" in bound_azure
    assert "token_record(4, {REGISTRY_AUDIENCE})" in bound_azure
    assert ".source_archive_sha256 == $source" in deploy
    assert (
        '"$AZ_EXECUTABLE" "$@" --subscription "$PINNED_STAGING_SUBSCRIPTION_ID"'
        in deploy
    )
    assert "refuses configured service-principal inputs" in deploy
    assert "AZURE_CLIENT_SECRET" in deploy
    assert "AZURE_CLIENT_CERTIFICATE_PATH" in deploy
    assert deploy.index("AZURE_CLIENT_SECRET") < deploy.index("SCRIPT_DIR=")
    assert "PYTHON_BIN=/opt/homebrew/bin/python3" in deploy
    assert '"$PYTHON_BIN" -I -S -B' in deploy
    flight_plan = (
        ROOT / "docs/flight-plans/2026-09-02-canonical-staging-candidate.md"
    ).read_text(encoding="utf-8")
    assert "`DEPLOY_RUN_ID`" in flight_plan
    assert "`DEPLOY_RUN_ATTEMPT=1`" in flight_plan
    assert deploy.count('git -C "$REPOSITORY_ROOT"') >= 5
    assert "rev-parse --show-toplevel" in deploy
    assert deploy.index("founder-user) verify_founder_release_source") < deploy.index(
        'source "$REPOSITORY_ROOT/scripts/deploy_revision_suffix.sh"'
    )
    assert (
        "PATH=/opt/homebrew/Cellar/gh/2.97.0/bin:/opt/homebrew/bin:/usr/bin:/bin"
        in deploy
    )
    assert 'echo "Unsupported staging deployment identity mode."' in deploy
    assert "STAGING_DEPLOYMENT_IDENTITY_MODE" not in workflow


def test_local_publisher_preserves_build_and_registry_boundaries() -> None:
    publisher = LOCAL_PUBLISHER.read_text(encoding="utf-8")
    uploader = UNTAGGED_UPLOADER.read_text(encoding="utf-8")
    bound_acr = BOUND_ACR.read_text(encoding="utf-8")
    bound_azure = BOUND_AZURE_CLI.read_text(encoding="utf-8")

    assert "cosmaneurastagingregistry.azurecr.io" in publisher
    assert "866bd4bc01e0e7be32c59eccdaf9dac3f495c7de" in publisher
    assert "9a2399212488" in publisher
    assert "staging_build_controller.py" in publisher
    assert publisher.count("revalidate_build_plan") == 4
    assert publisher.count("verify_founder_source") == 3
    assert publisher.count("verify_founder_azure_identity") == 3
    assert publisher.count("remote_tag_absent") == 3
    assert '--verify-source "$BUILD_PLAN" "$EXPECTED_PLAN_SHA256"' in publisher
    assert "EXPECTED_CONTROLLER_RESULT_SHA256" in publisher
    assert "keys == [" in publisher
    assert 'username = claims.get("email")' in bound_azure
    assert "staging_azure_cli_bound.py" in publisher
    assert "staging_acr_cli_bound.py" in publisher
    assert "containerapp" not in bound_acr
    assert '"acr", "credential", "show"' not in bound_acr
    assert ".source_archive_sha256 == $source" in publisher
    assert ".source_archive_sha256 == $source_sha256" in publisher
    assert "secure_json_capture" in publisher
    assert "RESULT_CAPTURE=$(secure_json_capture" in publisher
    assert (
        '"$CONTROLLER_RESULT" 65536 "$EXPECTED_CONTROLLER_RESULT_SHA256"' in publisher
    )
    assert "CONTROLLER_RESULT_JSON=$(printf" in publisher
    assert "BUILD_METADATA_JSON=$(printf" in publisher
    assert '--slurpfile result "$CONTROLLER_RESULT"' not in publisher
    assert '--slurpfile plan "$BUILD_PLAN"' not in publisher
    assert 'secure_file_fingerprint "$CONTROLLER_RESULT" 65536' in publisher
    assert 'secure_file_fingerprint "$BUILD_METADATA" 65536' in publisher
    assert "OP_SERVICE_ACCOUNT_TOKEN" in publisher
    assert "RECALL_WEBHOOK_SECRET" in publisher
    assert "DOCKER_AUTH_CONFIG" in publisher
    assert '"$DOCKER" image load --input "$IMAGE_ARCHIVE"' in publisher
    assert '"$DOCKER" push' not in publisher
    assert '"$AZ" acr login' not in publisher
    assert 'bound_acr acr login --name "$REGISTRY_NAME"' in publisher
    assert "--expose-token" in publisher
    assert 'bound_acr acr import --name "$REGISTRY_NAME"' in publisher
    assert '--source "$REGISTRY_REPOSITORY@$IMAGE_DIGEST"' in publisher
    assert '--image "$REGISTRY_REPOSITORY:$CANDIDATE_SHA"' in publisher
    assert "--force" not in publisher
    assert "bound_acr acr manifest show-metadata" in publisher
    assert "bound_acr acr repository update" in publisher
    assert ".digest == $digest" in publisher
    assert "--write-enabled false --delete-enabled false" in publisher
    assert "publication-attempt.json" in publisher
    assert "durable_before_first_registry_write:true" in publisher
    assert "staging_acr_untagged_upload.py" in publisher
    assert "tag_created:false" not in publisher
    assert "tag_created == false" in publisher
    assert "JohnClem" not in publisher
    assert "--actor" not in publisher
    assert "publication-result.json" in publisher
    assert "automatic_retry_allowed:false" in publisher
    assert "deployment_performed:false" in publisher
    assert publisher.count('"$PYTHON" -I -S -B') == 7
    assert ":latest" not in publisher
    assert "infra-staging/deploy.sh" not in publisher
    assert "cosmaneuraprodregistry" not in publisher
    assert "Blob and manifest writes here are content-addressed" in uploader
    assert '"/oauth2/token"' in uploader
    assert 'authorization = "Bearer " + access_token' in uploader
    assert "upload_manifest_by_digest" in uploader
    assert "/manifests/{digest}" in uploader
    assert "tag_created" in uploader
    assert 'method in {"HEAD", "POST", "PUT"}' in uploader


def test_local_publisher_captures_exact_verified_json_bytes(tmp_path: Path) -> None:
    publisher = LOCAL_PUBLISHER.read_text(encoding="utf-8")
    function = publisher.index("secure_json_capture() {")
    start = publisher.index("<<'PY'\n", function) + len("<<'PY'\n")
    end = publisher.index("\nPY\n}", start)
    program = publisher[start:end]
    raw = b'{"nested":{"value":7},"text":"fixture"}\n'
    path = (tmp_path / "record.json").resolve()
    path.write_bytes(raw)
    path.chmod(0o400)
    digest = hashlib.sha256(raw).hexdigest()

    accepted = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-", str(path), "65536", digest],
        input=program,
        text=True,
        capture_output=True,
        check=False,
    )
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout) == {
        "bytes": len(raw),
        "sha256": digest,
        "value": {"nested": {"value": 7}, "text": "fixture"},
    }

    refused = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-", str(path), "65536", "0" * 64],
        input=program,
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode != 0


def test_staging_workflow_does_not_reference_production_controls() -> None:
    source = _source()
    for forbidden in (
        "environment: production",
        "OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN",
        "cosmaneuraprodregistry",
        "infra/deploy.sh",
        "refs/heads/staging",
    ):
        assert forbidden not in source


def test_every_external_action_is_pinned_to_a_commit() -> None:
    source = _source()
    all_uses = re.findall(
        r"^\s+(?:- )?uses: ([^\s]+)(?:\s+#.*)?$", source, re.MULTILINE
    )
    external_uses = [action for action in all_uses if not action.startswith("./")]
    assert external_uses
    for action in external_uses:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), action
