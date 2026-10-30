# Recall terminal inbox projection

Risk: Tier 2 because this changes the minimized payload at signed webhook ingress. It adds operational status metadata only. Signature verification, ownership checks, encryption, retention, authorization and capture fences remain in the existing paths.

When Recall sends a terminal bot event, the inbox currently retains only bot/recording/transcript identifiers. The terminal handler requires `data.data.code`, `sub_code` and `updated_at`, so the normally ingressed event fails validation and never updates the bound capture's status.

The change retains those three fields only for the four already-supported terminal bot events. The event code must agree with the signed event name. Subcodes are null or bounded ASCII identifiers. The timestamp must be bounded, valid and timezone-aware; it is normalized to UTC. Other provider details remain excluded. Invalid DTOs still reach the existing permanent-rejection path without arbitrary detail retention.

Acceptance before integration:

- The new projection-to-dispatch tests fail on the previous source for valid terminal callbacks and pass on the change.
- Existing recording correspondence and deadline checks pass.
- Focused lint, type checks and whitespace checks pass.
- Independent review covers the final pushed commit and records any findings durably.

Staging acceptance after founder-controlled deployment:

- A fresh, normally authorized synthetic meeting completes the recording-permission step and captures synthetic audio.
- A signed terminal callback reaches the inbox and updates the exact bound capture's terminal status.
- Capture, note generation and saved-note/dashboard usability remain separate live acceptance gates. Source tests alone do not qualify them.

No migration, schema change, additional service, event replay, provider mutation or environment change is part of this PR. Existing consumed authorizations are not reset, and a failed occurrence is not reused for a second capture.

Recovery: revert this source commit and use the normal reviewed staging candidate process. This restores the previous minimized projection and its known inability to process terminal bot callbacks. Do not broaden payload retention, replay events or reset encounter/occurrence bindings as a recovery shortcut.
