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
control resource group is a read-only data lookup.

Creation requires all four bounded values in one independently reviewed saved
plan:

```hcl
enable_sanitizer_bootstrap_identity         = true
sanitizer_bootstrap_identity_plan_approved  = true
sanitizer_bootstrap_rehearsal_id            = "<12 lowercase hex>"
sanitizer_bootstrap_cleanup_not_after       = "<future whole-second UTC>"
```

The deadline is checked at plan time and again on the UAMI immediately before
creation. `prevent_destroy` deliberately rejects turning the switch off after
creation. A later teardown change may remove that protection only after exact
live readback proves no federation, RBAC, job attachment, active execution,
PostgreSQL administrator, session, provider authority, or database authority.

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
