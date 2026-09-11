# Staging private launch-mailbox storage foundation

Status: local source validation passed; final-push review and CI pending.
No Azure apply or launch.
Risk: **Tier 2**, private launch-instruction and receipt storage boundary.

## Deployable outcome and boundaries

Model a third, independently default-off East US Standard_ZRS StorageV2 account,
its auto-created default Blob service versioning update, two empty private
action-partitioned containers and one private Blob endpoint in `10.76.0.64/27`.
Reuse the existing control-VNet Blob DNS zone/link. No new DNS, target network,
public route, compute, job or broker is created.

The existing runtime validator requires mailbox account ID/host to differ from
marker, evidence AND runtime-record storage (cross-binding checks around 552).
Sharing the evidence account was considered and rejected. Container names are
exactly `restore-provision-launches-v1` and `restore-cleanup-launches-v1`; source
keys remain provision/cleanup so later per-action bindings cannot alias by order.

No reader/create grant, identity, signing key, object, receipt, runtime record,
retention lock, legal hold, publisher, job start or restore activation is added.
All live-proof/aggregate gates remain false. Empty versioned containers are not
immutable, create-only, signed or authenticated launch mailboxes.

## Provider and authority contract

Use the existing pinned AzAPI 2.1.0 ARM APIs at `2023-05-01` for the account,
default-service update and containers. New storage uses no data-plane probe,
ListKeys call, connection string or credential output. The explicit Blob update
targets the service auto-created by this exact new account, not an adopted
unmanaged service. Removing its state does not reverse versioning.

Require HTTPS/TLS 1.2, Standard DNS, OAuth default, private/default-deny network
with no bypass/IP/VNet/resource exceptions, shared key/public Blob/cross-tenant
access disabled, account-level Microsoft-managed Blob/File infrastructure
encryption, and no HNS/NFS/SFTP/local-user paths. Export only non-secret facts.
Account and containers remain destruction-guarded.

Paired default-false mailbox enable/exact-plan approval requires base/network
approval and both fresh CIDR checks. Reject equality with both other planned
account names even if their optional foundations are disabled. Neither an
evidence-storage approval nor a mailbox approval activates the other store.

## Validation and live acceptance

- Check all existing dormant/base/service plans stay mailbox-free; reject
  missing/partial approval, prerequisite/CIDR failures, both account collisions
  and invalid names. Positive mocks assert exact body/versioning/two keyed
  containers/private path, preserved markers, no grant or activation.
- Preserve all existing role/AzAPI declaration snapshots and extend only the
  three reviewed mailbox ARM declarations. Snapshots are edit detectors, not
  general HCL parsing or transitive-local semantic proof.
- Run format/validate, all provider-mocked tests, focused source/Ruff checks,
  change budget, independent exact-final-push review and mandatory CI.
- Before planning AND human apply: refresh identity/state/source/network;
  verify global name availability and authenticated exact-ID ARM absence,
  providers/subnet capacity, and independently review the exact saved plan.
  Reject conflicts or adoption. No operator window starts during an absence.
- After human apply: prove account/versioning/private container and endpoint/
  NIC/DNS posture, empty private object counts, effective account/container/
  ancestor RBAC, unchanged marker/evidence namespaces and empty target. Use an
  independently authorized private context; no broad job grant for convenience.

Locked immutable retention, provider-proven create-only semantics, scoped broker
and per-job reads, execution-name binding, signed handoff and no-retry start
semantics remain later independently reviewed prerequisites. No consumer may
claim `immutable_launch_record_mailbox_bound` from this foundation.

## Recovery

Before creation, hold/revert with flags false. After creation preserve account
and containers and use reviewed roll-forward. Flags are not rollback; removing
the update resource does not disable versioning or delete versions. Do not
delete version history/future immutable records as cleanup. Human-only apply and
founder-only staging/production remain unchanged. No data/schema migration,
user-visible feature flag or cross-repo rollout.

## Recorded local validation

This slice and the evidence-storage foundation are submitted in one coherent
private-storage PR within the 2,000-line limit, with independent creation gates.

OpenTofu 1.12.5 format/validate and all **59 provider-mocked runs passed** using
the locked providers without backend or Azure credentials. Thirteen mailbox
runs cover twelve refusals and exact positive body/account/versioning/action/
private-path semantics. Six existing default/base/network/registry/vault/
evidence runs additionally assert mailbox dormancy.

All **34 focused source tests** pass, including five mailbox-specific checks;
Ruff lint/format pass. The first local source-test run caught an incorrect
fixture spelling of the mailbox enable local; only that test spelling changed
and all five mailbox tests then passed. No HCL changed for that correction.
The whole-root edit inventory adds exactly three mailbox ARM declarations to
the previous fourteen, preserving all prior declaration hashes and eight role
assignments. Mocks/source tests do not establish live Azure acceptance,
connectivity, RBAC, object emptiness, retention or execution authority.
