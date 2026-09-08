# Staging sanitizer bootstrap identity

Status: **teardown-only and fail-closed by default**. Rehearsal `21506aaeec3c`
created the authority-free UAMI, and its hard cleanup deadline is
`2026-09-08T17:40:01Z`. This source can no longer create, replace, or extend an
identity. Its isolated backend key is
`staging-sanitizer-bootstrap-identity.tfstate`; never initialize it against an
application or broader rehearsal state.

The root contains one Azure resource:
`cosmaneura-staging-restore-bootstrap-job`. It creates no resource group,
federated credential, role assignment, compute, network, storage, database
resource, administrator, workflow binding, or production resource. The exact
subscription, tenant, resource group, location, identity name, and complete tag
key set are pinned in source; callers cannot redirect or relabel the resource.
The exact control resource group is a read-only data lookup.

The prior creation controls remain only so a stale invocation fails visibly.
Any non-default value for them is rejected. Teardown planning requires all
three exact values:

```hcl
authorize_sanitizer_bootstrap_identity_teardown = true
sanitizer_bootstrap_teardown_rehearsal_id       = "21506aaeec3c"
sanitizer_bootstrap_teardown_evidence_sha256    = "sha256:2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9"
```

The pinned owner-only evidence bundle records two stable live identity reads,
zero federated credentials, zero direct or inherited role assignments, zero
attachments across all six subscription Container Apps jobs, no bootstrap
principal among the sole Entra-enabled PostgreSQL server's administrators, and
isolated state lineage `a0c8b939-2fb2-0604-3e70-d925aeee5950` at serial 2. The
bundle authorizes only teardown planning. It does not authorize apply or make a
historical snapshot current at apply time.

The always-present Terraform guard rejects the default plan, partial inputs,
the wrong rehearsal, the wrong digest, and every former creation input. The
guard preserves its exact creation-time replacement triggers, updates in place
as a state tombstone, and retains its own `prevent_destroy` so blanket destroy
mode fails. The identity resource is fixed at count zero and its
`prevent_destroy` is deliberately removed. Its explicit dependency on the
guard makes a normal targeted plan evaluate the same authorization checks. A
future bootstrap attempt therefore requires a separate reviewed source change
rather than reusing or extending this expired lifecycle.

Pull-request verification is credential-free:

```sh
tofu -chdir=infra-staging-sanitizer-bootstrap-identity init -backend=false -input=false
tofu -chdir=infra-staging-sanitizer-bootstrap-identity fmt -check -diff -recursive
tofu -chdir=infra-staging-sanitizer-bootstrap-identity validate -no-color
tofu -chdir=infra-staging-sanitizer-bootstrap-identity test -test-directory=tests -no-color
```

No workflow in this slice plans against live state or applies this root. A live
teardown plan must use the checked-in backend from exact protected `master`, be
saved, prove only one UAMI destroy plus an in-place guard-tombstone update,
receive independent exact-SHA and exact-plan review, and be applied by the
founder. An immediate second read-only authority/attachment sweep is required
before that apply; any changed state invalidates the plan and evidence.

The existing creation-plan verifier cannot authorize teardown and must not be
used for this plan. The teardown-specific generator/validator is mandatory: it
initializes the exact backend and creates the full-refresh saved plan in the
same fresh execution directory before binding the archive to protected source,
the isolated backend tuple, lineage
`a0c8b939-2fb2-0604-3e70-d925aeee5950` at serial 2, exact UAMI IDs/tags,
complete outputs, and exactly one UAMI delete plus one guard update. It rejects
targeted plans because they omit the
complete action or output contract. Its receipt remains non-authoritative with
`apply_authorized=false`; the founder-only apply decision remains separate. The
spent creation plan must never be reused for teardown.

Pass only a new protected plan path, a new sibling execution-data directory,
and the exact protected `master` SHA. There is no external-plan validation mode.
Any backend coordinate override or nonempty credential setting is rejected,
and the exact backend-metadata digest is included in the receipt.

```sh
python scripts/validate_staging_sanitizer_bootstrap_identity_teardown_plan.py \
  --saved-plan "$RUN/teardown.tfplan" --execution-data-dir "$RUN/tfdata" \
  --expected-source-commit "$EXACT_MASTER_SHA"
```
