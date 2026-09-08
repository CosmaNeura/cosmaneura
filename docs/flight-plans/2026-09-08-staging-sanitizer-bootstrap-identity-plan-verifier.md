# Staging sanitizer bootstrap identity plan verifier

This Tier 2 slice validates an already-created saved OpenTofu plan for the
isolated sanitizer bootstrap identity root. It does not create a plan and can
never authorize or execute an apply.

## Exact bindings

The verifier requires explicit SHA-256, source-commit, state-lineage, and
state-serial inputs. It reads the plan only from an owner-controlled `0400`
regular file in an owner-controlled `0700` directory. It binds the archive's
Terraform files and provider lockfile byte-for-byte to the clean exact source
commit, then decodes those same in-memory plan bytes with pinned OpenTofu
1.12.5 and AzureRM 4.73.0. The reviewed source bytes come from the immutable Git
object while descriptor-based reads separately prove that the checkout matches
them without symlink, hardlink, or path-replacement ambiguity.

The decoder is deliberately platform-specific: both the Darwin arm64 OpenTofu
and AzureRM executable SHA-256 values are pinned in reviewed policy and checked
before and after use. A version string or freshly observed digest is not trust.

The accepted plan has exactly two create actions: one built-in activation guard
and one exact UAMI in the existing exact staging rehearsal control group. It
permits no update, delete, replacement, drift, deferred change, action
invocation, federation, RBAC, job, network, storage, database, production, or
PHI resource. Provider scope, source graph, prior state, output claims, tags,
and every lifecycle value must match the closed-world policy.

The plan timestamp and validated 1–24 hour lifetime must derive the same exact
cleanup deadline carried by the guard, its replacement trigger, the UAMI tag,
and the non-authoritative output. The saved plan is rejected once that deadline
is reached. The UAMI's apply-time expiry check remains unknown at plan time;
every other expected check must pass.

## Backend boundary

The verifier intentionally initializes only an offline decoder with
`tofu init -backend=false` and invokes only `tofu show -json`. It strips all
Azure, OpenTofu, proxy, custom-CA, and package-source environment overrides.
The signed provider package is constrained by the reviewed lockfile and its
executable must match the separately pinned platform digest before it can be
loaded. Initialization or showing the plan fails if it creates local backend
metadata. The verifier does not contact, initialize, lock, read, or change the
Azure backend.

This boundary is deliberate: a verifier-design probe established that ordinary
AzureRM backend initialization can materialize an empty state blob even without
plan or apply. Therefore backend state capture and live resource readback must
be separate, reviewed, founder-controlled evidence steps.

## Output and recovery

The receipt binds exact source, provider, plan, decoded JSON, and embedded state
digests, but always reports `live_remote_master_bound=false`,
`live_backend_readback_bound=false`, `live_resource_readback_bound=false`, and
`apply_authorized=false`. Failure is terminal and emits no partial receipt.
Recovery is deletion of this code-only validator; it has no cloud mutation
path.
