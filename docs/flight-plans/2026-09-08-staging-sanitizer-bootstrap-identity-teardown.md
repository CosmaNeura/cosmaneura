# Staging sanitizer bootstrap identity teardown

## Outcome and boundary

This Tier 2 slice makes the isolated bootstrap-identity root teardown-only for
rehearsal `21506aaeec3c`. It removes the UAMI lifecycle guard that otherwise
blocks destruction, fixes both the resource-group read and the UAMI at count
zero, and retains one explicit Terraform tombstone protected from destruction.
It cannot create, replace, extend, attach, federate, authorize, or use an
identity. Merge performs no Azure action.

The exact temporary identity is
`cosmaneura-staging-restore-bootstrap-job` in
`cosmaneura-staging-rehearsal-control`. Its hard cleanup deadline is
`2026-09-08T17:40:01Z`.

## Evidence and claims

Owner-only live-readback bundle
`sha256:2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9`
covers `2026-09-08T14:12:20Z` through `2026-09-08T14:18:55Z`. It records:

- stable exact UAMI IDs and lifecycle tags before and after the sweep;
- zero federated credentials and zero direct or inherited role assignments;
- six subscription Container Apps jobs and zero bootstrap-UAMI attachments;
- five PostgreSQL servers, only one with Entra authentication enabled, and no
  bootstrap principal in that server's administrator list; and
- isolated Terraform lineage `a0c8b939-2fb2-0604-3e70-d925aeee5950`, serial 2,
  with only the resource-group read, UAMI, and activation guard.

This historical evidence supports only the source transition and teardown-plan
preparation. It is not continuous proof and grants no apply authority.

Independent `codex-auto-review` response
`resp_015e93cd057684d8006aa0197aaf708190931ece07a331d6c2` found no
actionable findings. The exact request SHA-256 is
`99b0cbd008c6c584818b20e6f147285322bfa4654d459dca365a908d22bd3e2c`;
the response artifact SHA-256 is
`b591686dde1c5e964b73631b7c8a3596069e769051898733e0dc53c67b1373a9`.

## Fail-closed controls

The default plan fails. Planning requires the exact reviewed evidence digest,
the exact rehearsal ID, and an explicit teardown boolean. Former creation
inputs are rejected, the UAMI count is a source-level constant zero, and future
creation requires another reviewed source change. The resulting public output
is a non-authoritative tombstone with `founder_apply_required=true` and
`apply_authorized=false`. The guard preserves its exact creation-time
replacement triggers and updates in place; its own `prevent_destroy` causes a
blanket destroy-mode plan to fail. The UAMI also explicitly depends on the
guard so a normal targeted plan cannot omit its preconditions.

Secretless validation passes all seven mocked plans. A backend-disabled,
refresh-disabled dry run against a private copy of serial-2 state produced
exactly `0 add, 1 in-place guard update, 1 UAMI destroy`; its plan JSON SHA-256
is `1b48159572d1ff775e91ca2f8743959d70ef1ba22509b0b140e3e184a7507a55`.
A default targeted plan failed on the guard. Exact-input targeted normal and
targeted destroy plans were also generated only as negative tests: the former
omits the complete output transition and the latter omits both the guard update
and complete outputs, so the teardown validator rejects both. Their decoded
JSON SHA-256 values are respectively
`873f9dd6254eed8030436a45c4a8eb76e3f0681fd419c8b430bcfa61096e5fbc`
and `a846cda81c7bd0e7c1cff0caace8232ebbc9903ddb3f8608ccdf695fee5b5653`.
Neither artifact is a live or apply-authorized plan; every local plan is marked
never-apply and must never be used against Azure.

The teardown-specific saved-plan validator is the mandatory execution gate.
It decodes with the pinned OpenTofu and AzureRM binaries and requires the exact
protected source archive, backend tuple, lineage `a0c8b939-2fb2-0604-3e70-d925aeee5950`,
serial 2, UAMI resource/client/principal/tenant IDs and tags, guard state,
variables, checks, full output transition, and the sole action set of one UAMI
delete plus one in-place guard update. It emits a receipt with
`apply_authorized=false` and `founder_apply_required=true`; a refusal or any
receipt difference is an abort.

After merge, the founder may prepare a fresh saved plan from exact protected
`master` and the isolated backend. The plan must show only destruction of the
exact UAMI, an in-place guard-tombstone update, and the expected output
transition. Run
`scripts/validate_staging_sanitizer_bootstrap_identity_teardown_plan.py`
against that protected 0400 saved plan, its exact SHA-256, the exact merged
`master` SHA, the pinned provider package, and the mode-0600 backend metadata
file from the same initialized execution data directory. A fresh read-only sweep must
again prove no federation, RBAC, job attachment, PostgreSQL administrator
binding, or state change immediately before apply. The saved plan, validator
receipt, and fresh evidence require independent review. Agents do not apply it.

Abort on any extra resource action, drift, state lineage/serial change,
authority or attachment, changed identity IDs/tags, expired or ambiguous
evidence, source/master change, or inability to preserve the spent creation
plan as permanently unusable. Recovery before apply is a code revert. After a
successful teardown, verify the UAMI is absent and retain the Terraform
tombstone; do not remove the state record or recreate the identity in this
source.
