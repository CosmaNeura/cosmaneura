# Core Python tooling security qualification

Tier 1: bounded updates to existing development/debugging dependencies. Based
on protected master `49895cb66a305558393a0862a590ee313b60c194`. No application,
authentication, billing, schema, environment or deployment changes. Original
user-owned worktrees and dependency edits were left untouched.

## Locked candidate

| Package | Baseline | Qualified version |
| --- | --- | --- |
| black | 25.1.0 | 26.10.0 |
| datamodel-code-generator | 0.30.0 | 0.64.1 |
| filelock | 3.18.0 | 3.32.7 |
| pytest | 8.3.5 | 9.1.1 |
| pytest-asyncio | 0.26.0 | 1.4.0 |
| pytest-celery | 1.2.0 | 1.3.0 |
| setuptools | 79.0.0 | 83.0.0 |
| virtualenv | 20.30.0 | 21.14.5 |

Compatibility-required transitive changes: pathspec 1.1.1, pytokens 0.4.1,
pytest-playwright 0.9.0 and python-discovery 1.6.1. PyJWT remains at baseline
2.14.0; this PR does not qualify an authentication update.

Bounds prevent returning below the known security floors without upgrading the
entire dependency graph. The current vulnerability database requires virtualenv
21.7.13 or newer, beyond the earlier GitHub alert's 20.36.1 fix. setuptools 83
removes pkg_resources; pytest-celery 1.2 cannot start with it. Version 1.3 fixes
that compatibility issue, and normal plugin loading was retained for the
authority suite. pytest-asyncio 0.26 requires pytest below version 9.

The schema generator regression tests address the upstream
[schema import injection advisory](https://github.com/datamodel-code-generator/datamodel-code-generator/security/advisories/GHSA-5578-w22f-pfx9).
Fixtures contain only synthetic values and a harmless sentinel. Never execute
generated output from malicious test input.

## Verification

- Clean `uv sync --python 3.13 --all-groups --locked`; offline lock check passes.
- Isolated authority suite: 977 passed, 707 skipped, no failures/errors. Skips
  require explicitly owned PostgreSQL/other external fixtures; this is not the
  full database, browser, provider or device qualification.
- Five tooling regressions pass: Pydantic model generation/validation, both
  import-extension injection cases, isolated offline interpreter creation and
  real cross-process lock exclusion. The generator test module explicitly skips
  when optional debugging-extras is absent; locally all five executed.
- Negative control on the old baseline dependencies: three compatibility tests
  pass and both injection tests fail. The old import extension accepts the
  sentinel; the old custom type reaches a formatter failure rather than the
  patched identifier rejection. No malicious generated output was executed.
- Whole-repo Ruff check passes. The new tests pass scoped formatting and type
  checking. Whole-repo formatting has 30 pre-existing files, prepared separately.
- Whole-repo Pyright with the explicit Python interpreter reports 568 errors on
  both the protected baseline and candidate. They are not waived or represented
  as a clean gate. Do not use bare Pyright here: interpreter discovery can add
  misleading missing-import diagnostics.
- Installed-environment pip-audit after the updates reports one remaining
  finding: PyJWT 2.14.0, PYSEC-2026-4141, fixed in 2.15.0. Git-sourced packages
  are not fully covered by registry vulnerability scanning. This is not a
  zero-vulnerability release and default-branch alerts remain until merge.

Evidence retained locally:

- `/private/tmp/cosmaneura-core-python-qualification-authority.xml`
- `/private/tmp/cosmaneura-core-python-qualification-tools.xml`
- `/private/tmp/cosmaneura-core-python-qualification-negative-control.xml`
- `/private/tmp/cosmaneura-core-python-qualification-audit-final.json`

Reproduce isolated application tests without repository-wide Clerk cleanup:

```sh
TMP_DIRECTORY=/private/tmp TEST_RESULTS_DIRECTORY=/private/tmp \
PYTHON_TEST_SERVER_HOST=testserver PYTHONPATH=. .venv/bin/pytest -q \
  --confcutdir=tests/authority_kernel tests/authority_kernel
PYTHONPATH=. .venv/bin/pytest -p no:pretty_traceback -q \
  --confcutdir=tests/offline tests/offline/python_dependency_qualification_test.py
```

The second command disables only the traceback presentation plugin, whose
options are registered by the deliberately excluded root conftest; it does not
disable the Celery plugin or security assertions.

## Review, integration and recovery

Require independent agent review of the final pushed SHA and rerun on the exact
integrated release candidate. Actions remains disabled. Local tests are neither
GitHub approval nor deployment authorization. Qualify Core PyJWT separately with
explicit auth-update authorization, and fix the baseline type errors before
claiming all quality gates are clean.

Recovery: revert this PR and restore the predecessor lockfile/environment (or
retain the predecessor deployment image). This restores known vulnerable tools;
prefer a forward compatibility fix and explicitly accept any temporary security
risk. There is no migration, seed, live-provider call, or rollout in this work.
