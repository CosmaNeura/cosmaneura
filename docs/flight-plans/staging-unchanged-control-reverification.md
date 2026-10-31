# Immutable staging candidate with an audited controller closure

Risk: Tier2; CI/deployment control. Owner approval is required before integration
or live use. This candidate changes no live workflow, environment or artifact.

Both staging runs41196465275 and41198604145 passed all candidate test gates,
then failed before protected configuration because master advanced during tests.
A further identical retry has the same race. The immutable candidate and its
founder-selected merge PR remained unchanged.

Initial dispatch still requires exact current protected master and the founder
original/rerun actor. Candidate merge-PR, ancestor and schema assertions remain.
At initial authorization AND each later check, the controller must match a fixed
SHA256-pinned canonical dependency audit manifest. It binds native Git blob IDs
and modes, with independent SHA256 digests, for the exact audited workflow,
helpers, local actions, root configuration/toolchain/policy and Just recipes.
New or unresolved loader/read/import changes cannot silently update this audit:
their source bytes cease to match and require separately reviewed manifest work.
The verifier and manifest self are excluded from the inner dependency hash to
avoid a cycle; both remain in the full outer controller inventory compared across
master advancement. The verifier's own stdlib-only imports and fixed transports
remain subject to exact source review and protected-master initial authorization.

Only five later privileged checks opt into --reverify. A newer protected master
must descend from the dispatched controller and retain the exact candidate.
Every tracked path outside candidate-only app/web/migration/test/flight-plan
trees must retain identical Git blob IDs and modes. All manifest-declared paths
remain protected even within those business trees, including the web lockfile
cache input and this policy flight plan. The last master read must still equal
the first within each verification. Descendant code is not imported or executed.

The explicit canonical execution audit follows these entrypoints: the workflow,
common-setup/install-op/install-nixpacks local actions, verifier/recovery/release
scripts, infra-staging/deploy.sh and its sourced suffix/helper scripts, runtime,
core_cab, superadmin, migration-template and API-only secret guards. Their Python
imports are stdlib or controlled scripts; the alternate human builder dynamically
loads only its explicit hash-bound scripts modules, all pinned here, and stays on
its strict human route. Just parses its root/imported recipes; controller calls
only direnv_export and protected configuration/toolchain setup. .envrc, .env
inputs and recipes are pinned. Application CLI/build/migration and billing
preflight execution occurs only in the separate immutable candidate checkout or
image, not from newer master app source. No current controller entrypoint loads
app/web/test/migration business files from its checkout. This is an audited fixed
source closure, not a generic dependency analyzer or a promise for arbitrary
future controller source. New controller source requires a new closure review.

Acceptance: real Git regressions permit unrelated business advancement only in
later checks; initial dispatch stays strict. Changed workflow/helper/action/env/
policy/lockfile source or mode, unknown controller imports/reads, non-descendant
master, lost protection and wrong candidate PR refuse. Existing source and strict
human tests remain. A synthetic unchanged workflow/helper that imports a changed
app controller helper fails because that controller is outside the pinned audit.

Alternatives: another identical retry retains the race; freezing other Core work
needs broader owner coordination; a separate deployment route violates the
canonical-only contract. The past twelve-business-path exception was abandoned
because it would not handle a future unrelated business merge.

Recovery: revert this source change, review current controller state and start a
new founder dispatch. Existing failed attempts cannot receive patched code or
be relabeled successful. No production/human route, runtime fingerprint, schema,
artifact/credential gate, notification or production activation changes.
