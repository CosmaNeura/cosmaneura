#######################
# Python
#######################

# this is used for jinja + HTML linting, if you put templates elsewhere, you'll need to update this
JINJA_TEMPLATE_DIR := "app/templates"

# create venv and install packages
py_setup:
	[ -d ".venv" ] || uv venv

	# don't include debugging-extras on CI
	# use the same locked sources in CI and locally, otherwise git-sourced packages like activemodel
	# can resolve differently and cause schema drift between environments
	if [ -z "${CI:-}" ]; then \
		uv sync --group=debugging-extras; \
	else \
		uv sync; \
	fi

	# Local setup retains the full browser. CI workflows install the headless
	# shell explicitly and only in the integration worker that uses it.
	if [ -z "${CI:-}" ]; then \
		uv run playwright install chromium; \
	fi

# clean entire py project without rebuilding
py_clean:
	# pycache should never appear because of PYTHON* vars

	rm -rf .pytest_cache .ruff_cache .venv || true
	rm -rf tests/**/snapshot_tests_failures || true

	# rm -rf $PLAYWRIGHT_BROWSERS_PATH
	# TODO should remove pnpm global cache:   pnpm store path
	# rm -rf $(pnpm store path)


# rebuild the venv from scratch
py_nuke: py_clean && py_setup
	# reload will recreate the venv and reset VIRTUAL_ENV and friends
	direnv reload

py_upgrade:
	# https://github.com/astral-sh/uv/issues/6794
	uv sync -U --group=debugging-extras
	uv tool upgrade --all
	git add pyproject.toml uv.lock

# open up a python development server
py_dev:
	# if the server doesn't quit perfectly, it can still consume the port, let's avoid having to think about that
	kill -9 $(lsof -t -i :${PYTHON_SERVER_PORT}) 2>/dev/null || true
	PORT=$PYTHON_SERVER_PORT uv run python main.py

py_play:
	./playground.py

# TODO should have additional tool for workers and all server processes

# run all linting operations and fail if any fail
[script]
py_lint +FILES=".":
	# + indicates one more arguments being required in Justfile syntax

	# NOTE this is important: we want all operations to run instead of fail fast
	set +e

	# TODO we should either abstract this out or remove it...
	# Define a more detailed colored PS4 without current directory so -x output is easier to read
	setopt prompt_subst
	export PS4='%F{green}+%f '
	set -x

	if [ -n "${CI:-}" ]; then
		# TODO I'm surprised that ruff doesn't auto detect github... need to double check on this
		# NOTE `uv run` (not `uv tool run`) so ruff resolves to the version pinned in
		# uv.lock — `uv tool run` grabs latest and drifts from CI/local expectations
		uv run ruff check --output-format=github {{FILES}} || exit_code=$?
		uv run ruff format --check {{FILES}} || exit_code=$?

		uv run pyright {{FILES}} --outputjson > pyright_report.json || exit_code=$?
		# TODO this is a neat trick, we should use it in other places too + document
		# https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#setting-a-warning-message
		# https://github.com/jakebailey/pyright-action/blob/b7d7f8e5e5f195796c6f3f0b471a761a115d3b2c/src/main.ts#L62
		# Filter out diagnostics from .venv/ — third-party libs surface infos
		# (e.g. clerk_backend_api's authenticate_request, sqlalchemy cartesian
		# product warnings) when our code imports them. Real errors in our
		# code keep showing up as annotations.
		jq -r '.generalDiagnostics[] | select(.file | contains("/.venv/") | not) | "::\(.severity) file=\(.file),line=\(.range.start.line),endLine=\(.range.end.line),col=\(.range.start.character),endColumn=\(.range.end.character)::\(.message)"' < pyright_report.json
		rm pyright_report.json

		# check jinja2 template language
		uv run j2lint --extension j2,html {{JINJA_TEMPLATE_DIR}} --json > j2link_report.json || exit_code=$?
		jq -r '(.ERRORS[] | "::\(if .severity == "HIGH" then "error" else "warning" end) file=\(.filename),line=\(.line_number),title=\(.id)::\(.message)"), (.WARNINGS[] | "::warning file=\(.filename),line=\(.line_number),title=\(.id)::\(.message)")' < j2link_report.json
		rm j2link_report.json
	else
		uv run ruff check {{FILES}} || exit_code=$?
		uv run pyright {{FILES}} || exit_code=$?
		uv run j2lint --extension j2,html {{JINJA_TEMPLATE_DIR}}
	fi

	# TODO should only run if {{FILES}} contains a template
	# NOTE djlint does *not* check jinja syntax, only HTML. GH friendly output is automatically enabled.
	uv run djlint {{JINJA_TEMPLATE_DIR}} --profile=jinja

	# TODO right now, this tool doesn't work with manual maps :/
	# TODO https://github.com/fpgmaas/deptry/issues/610#issue-2190147786
	# TODO https://github.com/fpgmaas/deptry/issues/740
	# uv tool run deptry --experimental-namespace-package . || exit_code=$?

	if [[ -n "${exit_code:-}" ]]; then
		echo "One or more commands failed"
		exit 1
	fi

# automatically fix linting errors
py_lint_fix:
	uv run ruff check . --fix
	uv run ruff format

	uv run djlint --profile=jinja --reformat {{JINJA_TEMPLATE_DIR}}

	# NOTE pyright and other linters do not have an automatic fix flow

# build js for py e2e tests
py_js-build:
	# integration tests should mimic production as closely as possible
	# to do this, we build the app and serve it like it will be served in production
	{{EXECUTE_IN_TEST}} env \
		PNPM_GLOBAL_FLAGS="--silent" \
		VITE_CLERK_PUBLIC_KEY="${VITE_CLERK_PUBLIC_KEY:-pk_test_placeholder}" \
		VITE_INTAKE_CHAT_DOMAIN="${VITE_INTAKE_CHAT_DOMAIN:-cosma.localhost}" \
		VITE_POSTHOG_KEY="${VITE_POSTHOG_KEY:-test-posthog-key}" \
		VITE_POSTHOG_HOST="${VITE_POSTHOG_HOST:-https://app.posthog.com}" \
		VITE_SENTRY_DSN="${VITE_SENTRY_DSN:-https://examplePublicKey@o0.ingest.sentry.io/0}" \
		VITE_ENABLE_TEST_AUTH="true" \
		just js_build

PYTEST_COV_PARAMS := "--cov --cov-report=html:${TEST_RESULTS_DIRECTORY}/htmlcov --cov-report=term"

# CI runs these fourteen selectors on isolated runners. Keep the selectors explicit
# so every Python test belongs to exactly one shard and `py_test` remains the
# single combined local/CI-compatible entry point. Application and integration
# tests both require built web assets; only integration requires browser routing.
[script]
py_test-ci-application:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-application is CI-only" >&2; exit 1; }
	just _banner_echo "Building Javascript for Application Tests"
	just py_js-build
	uv run pytest tests --verbosity=2 \
		--ignore=tests/infrastructure \
		--ignore=tests/integration \
		{{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-general:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-general is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure --verbosity=2 \
		--ignore-glob='tests/infrastructure/test_staging_controller_receipt_v2*.py' \
		--ignore-glob='tests/infrastructure/test_staging_restore_*.py' \
		{{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-controller:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-controller is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_controller_receipt_v2.py tests/infrastructure/test_staging_controller_receipt_v2_{arm_observation,database_observation}.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-receipt-io:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-receipt-io is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_controller_receipt_v2_{builder,writer}.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-foundation:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-foundation is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_restore_sanitizer.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-source-state-authority:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-source-state-authority is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_restore_fixed_catalog_source_state_authority.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-source-state-context:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-source-state-context is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_restore_fixed_catalog_source_state_context.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-source-state-integrity:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-source-state-integrity is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_restore_fixed_catalog_source_state_integrity.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-evidence:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-evidence is CI-only" >&2; exit 1; }
	uv run pytest \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_policy_is_hash_pinned_closed_world_and_dark \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_trusted_evidence_policy_mutations_fail_closed \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_every_evidence_digest_is_bound_to_the_controller_intent \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_exact_api_rejects_mappings_partials_subclasses_and_forged_seals \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_module_has_no_runtime_or_mutation_surface \
		--verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-evidence-integrity:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-evidence-integrity is CI-only" >&2; exit 1; }
	uv run pytest \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_real_chain_joins_final_evidence_after_historical_expiry \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_source_restore_and_run_context_cannot_be_substituted \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_mixed_real_chains_and_tampered_outputs_refuse \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_clock_shape_regression_and_target_expiry_refuse \
		tests/infrastructure/test_staging_restore_sanitizer_evidence.py::test_output_cannot_be_constructed_or_serialized \
		--verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-executor:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-executor is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_restore_sanitizer_executor.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-sanitizer-data-contract-executor:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-sanitizer-data-contract-executor is CI-only" >&2; exit 1; }
	uv run pytest tests/infrastructure/test_staging_restore_sanitizer_{data_contract_executor,fixed_catalog_post_projection,source_snapshot}.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-infrastructure-restore:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-infrastructure-restore is CI-only" >&2; exit 1; }
	restore_tests=(tests/infrastructure/test_staging_restore_*.py)
	dedicated_restore_tests=(tests/infrastructure/test_staging_restore_sanitizer.py tests/infrastructure/test_staging_restore_fixed_catalog_source_state*.py tests/infrastructure/test_staging_restore_sanitizer_*.py)
	restore_tests=("${(@)restore_tests:|dedicated_restore_tests}")
	uv run pytest "${restore_tests[@]}" tests/infrastructure/test_staging_controller_receipt_v2_{isolation_inventory,private_reader,production_authority}.py --verbosity=2 {{PYTEST_COV_PARAMS}}

[script]
py_test-ci-integration:
	[[ "${CI:-}" == "true" ]] || { echo "py_test-ci-integration is CI-only" >&2; exit 1; }
	just _banner_echo "Building Javascript for Integration Tests"
	just py_js-build
	just _banner_echo "Running Integration"
	uv run pytest tests/integration --verbosity=2 {{PYTEST_COV_PARAMS}}

# run tests with the exact same environment that will be used on CI
[script]
py_test:
	# Define a more detailed colored PS4 without current directory so -x output is easier to read
	setopt prompt_subst
	export PS4='%F{green}+%f '
	set -x

	# TODO we don't need to see all of the details for this part of the build, since we are primarily testing javascript

	# TODO I wonder if I could make EXECUTE_IN_TEST blank if in the test environment...
	# NOTE unfortunately, because of the asyncio loop + playwright, we need to run the playwright integration tests separately
	if [[ -n "${CI:-}" ]]; then
		just _banner_echo "Building Javascript for Integration Tests"
		just py_js-build
	  just _banner_echo "Running Non-Integration Tests"
		uv run pytest . --verbosity=2 --ignore tests/integration {{PYTEST_COV_PARAMS}}
		# ensure the Playwright headless shell exists for integration tests in CI-mode runs
		uv run playwright install chromium --only-shell
	  just _banner_echo "Running Integration"
		uv run pytest tests/integration --verbosity=2 --cov-append {{PYTEST_COV_PARAMS}}
	else
		# when not running in CI, JS is built automatically
		{{EXECUTE_IN_TEST}} uv run pytest . --verbosity=2 --ignore tests/integration {{PYTEST_COV_PARAMS}}
		{{EXECUTE_IN_TEST}} uv run pytest tests/integration --verbosity=2 --cov-append {{PYTEST_COV_PARAMS}}
	fi

# open playwright trace viewer on last trace zip. --remote to download last failed remote trace
[macos]
py_playwright_trace remote="":
		mkdir -p ${PLAYWRIGHT_RESULT_DIRECTORY}

		# helpful to download to unique folder for two reasons: (a) easier to match up to web GHA view and (b) eliminates risk of gh-cli erroring out bc the directory already exists
		if [ "{{remote}}" = "--remote" ]; then \
				failed_run_id=$(just _gha_last_failed_run_id) && \
				mkdir -p ${PLAYWRIGHT_RESULT_DIRECTORY}/${failed_run_id} && \
				gh run --dir ${PLAYWRIGHT_RESULT_DIRECTORY}/${failed_run_id} download $failed_run_id; \
		fi

		# NOTE it's insane, but fd does not have a "find last modified file"
		# https://github.com/sharkdp/fd/issues/196
		uv run playwright show-trace $(fd --no-ignore-vcs . ${PLAYWRIGHT_RESULT_DIRECTORY} -e zip -t f --exec-batch stat -f '%m %N' | sort -n | tail -1 | cut -f2- -d" ")

# download the visual snapshots from the last failed playwright run, useful for updating non-macos screenshot versions
[macos]
py_playwright_visual-download:
	failed_run_id=$(just _gha_last_failed_run_id) && \
		rm -rf ${PLAYWRIGHT_RESULT_DIRECTORY}/${failed_run_id} && \
		mkdir -p ${PLAYWRIGHT_RESULT_DIRECTORY}/${failed_run_id} && \
		gh run --dir ${PLAYWRIGHT_RESULT_DIRECTORY}/${failed_run_id} download $failed_run_id && \
		cp -R ${PLAYWRIGHT_RESULT_DIRECTORY}/${failed_run_id}/test-results/${PLAYWRIGHT_VISUAL_SNAPSHOT_DIRECTORY}/ ${PLAYWRIGHT_VISUAL_SNAPSHOT_DIRECTORY}/


# record playwright interactions for integration tests and dump them to a file
[macos]
[script]
py_playwright-record:
	mkdir -p tmp/playwright
	recorded_interaction=tmp/playwright/$(date +%m-%d-%s).py

	uv run playwright codegen \
		--target python-pytest \
		--output $recorded_interaction \
		https://${JAVASCRIPT_SERVER_HOST}

	echo $recorded_interaction
	pbcopy < $recorded_interaction

# open mailpit web ui, helpful for inspecting emails
py_mailpit_open:
	open "https://$(echo $SMTP_URL | cut -d'/' -f3 | cut -d':' -f1)"

# run py-spy on all python processes with a cwd matching the current project
py_spy:
	ps -eo pid,command | \
		awk -v dir="$(pwd)" '$2 ~ /python/ && system("lsof -p " $1 " 2>/dev/null | grep -q " dir) == 0 {print $1}' | \
		xargs -I{} sudo py-spy dump --pid {}
