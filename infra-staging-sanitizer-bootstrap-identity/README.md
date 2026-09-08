# Staging sanitizer bootstrap identity

Status: **default-off and unapplied**. This isolated OpenTofu root may own one
authority-free user-assigned managed identity in the existing staging rehearsal
control resource group. Its backend key is
`staging-sanitizer-bootstrap-identity.tfstate`; never initialize it against an
application or broader rehearsal state.

The root contains one Azure resource:
`cosmaneura-staging-restore-bootstrap-job`. It creates no resource group,
federated credential, role assignment, compute, network, storage, database
resource, administrator, workflow binding, or production resource. The exact
subscription, tenant, resource group, location, identity name, and complete tag
key set are pinned in source; callers cannot redirect or relabel the resource.
The exact control resource group is a read-only data lookup.

Creation requires all four bounded values in one independently reviewed saved
plan:

```hcl
enable_sanitizer_bootstrap_identity         = true
sanitizer_bootstrap_identity_plan_approved  = true
sanitizer_bootstrap_rehearsal_id            = "<12 lowercase hex>"
sanitizer_bootstrap_lifetime_hours          = 6 # integer from 1 through 24
```

The exact cleanup deadline is derived from the immutable plan timestamp and
the bounded lifetime, then checked again on the UAMI immediately before
creation. Lifecycle metadata is replacement-only, and its guard is a UAMI
replacement trigger. Consequently, a later plan cannot refresh the deadline or
rehearsal ID in place: `prevent_destroy` rejects that replacement as well as
turning the switch off. A later teardown change may remove that protection only
after exact live readback proves no federation, RBAC, job attachment, active
execution, PostgreSQL administrator, session, provider authority, or database
authority.

Pull-request verification is credential-free:

```sh
tofu -chdir=infra-staging-sanitizer-bootstrap-identity init -backend=false -input=false
tofu -chdir=infra-staging-sanitizer-bootstrap-identity fmt -check -diff -recursive
tofu -chdir=infra-staging-sanitizer-bootstrap-identity validate -no-color
tofu -chdir=infra-staging-sanitizer-bootstrap-identity test -test-directory=tests -no-color
```

No workflow in this slice plans against live state or applies this root. A live
plan must use the checked-in backend, be saved, and receive independent
exact-SHA and exact-plan review before a founder-operated apply.

The follow-on verifier is deliberately offline and non-authoritative. From a
clean checkout at the exact reviewed `origin/master` commit, place the saved
plan in an owner-controlled `0700` directory, set the plan file to `0400`, and
run:

```sh
python scripts/validate_staging_sanitizer_bootstrap_identity_plan.py \
  --saved-plan /absolute/path/bootstrap.tfplan \
  --expected-saved-plan-sha256 '<64 lowercase hex>' \
  --expected-source-commit '<40 lowercase hex>' \
  --expected-state-lineage '<reviewed UUID>' \
  --expected-state-serial '<reviewed positive integer>'
```

It installs the pinned decoder with `-backend=false`, invokes only `tofu show
-json`, and emits `apply_authorized=false`. It neither creates a plan nor reads,
initializes, locks, or changes the live backend. Separate founder-controlled
state capture, live resource readback, review, and apply gates remain required.
