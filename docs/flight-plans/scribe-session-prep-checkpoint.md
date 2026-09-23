# Scribe and Session Prep integration checkpoint

This branch preserves the Scribe Core source snapshot associated with staging
image `sha256:7d493481b8805c150be5b096c8d630d1fd09e4cd43ab7ab7e647f265ecf59bd7`
and combines it with the merged provider beta. It is a reproducible integration
checkpoint, not permission to overwrite Scribe's newer work or deploy an
unmerged branch. Tier 2 applies to the patient record and authority paths.

The provider notes dashboard retains Scribe's per-note transcript selection and
Session Prep's exact-revision review controls. Both the existing transcript
handoff and newer capture interfaces remain available. A Scribe draft becomes a
source for a later encounter only after provider review. Editing that note
invalidates the previous brief; reviewing the replacement permits a new brief
with the corrected source citation. Transcript refresh preserves typed entries
without changing stored content or discarding pending edits.

The generated merge migration `56f2b01ac226` joins the Scribe head `c8526f5e8d80`
and provider beta head `de9260834611`. Both upgrade paths were rehearsed against
populated synthetic databases and preserved all seeded rows. No history was
rewritten and no live migration has run.

Local verification completed on this source: 1,542 Core application cases,
136 affected capture/source/edit/handoff cases, 329 browser cases, Python and
browser type checks, and the browser build. Opt-in live-model and separate
cross-repository tests remain outside this result. Both ordinary capture and
reviewed-English capture passed the complete Core note-review-correction to
later Session Prep flow with deterministic remote responses. The runtime audit
found no known advisories in 137 registry packages; the source-installed
ActiveModel dependency is outside that registry scan.

## Next staging release

1. Capture the next stable Scribe Core commit and reconcile its changes with
   this preserved snapshot. Keep Scribe's desktop/mobile and service releases
   separate from the shared Core dashboard release.
2. Split/import the Scribe-owned source into reviewable PRs and complete review
   after the final pushes. This checkpoint includes a large preserved source
   snapshot and must not be submitted as an oversized single PR.
3. Refresh the existing local build/publish/deploy controller, which remains
   pinned to historical PR #385 and its schema. Keep a reviewed immutable
   candidate, fixed staging targets, exact source/image identities, and an
   observed schema/recovery plan. Do not remove those checks or substitute an
   unchecked deployment command.
4. Release Core web, worker and scheduler from one combined image. Rehearse the
   final schema path before applying its migrations once. Keep Scribe service
   targets and the Sovereign API target distinct; retain compatible API
   contracts and the restricted provider-beta capabilities.
5. Verify the provider dashboard and Scribe-to-Prep workflow in staging before
   opening the selected-provider beta. Any application rollback leaves additive
   schema structures dormant and preserves Scribe's stored records.

The current staging deployment and Scribe's active development checkout were
not changed while producing this checkpoint.
