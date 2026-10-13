# Staging Clerk OTP delivery adapter

Tier 2: authentication delivery and sensitive transient credentials. Based on
Core 8333339cd30df6efbb2845024414860f5b5451c6. The authorized work is a dormant
implementation, synthetic local tests, and independent review. No deployed
runtime, Clerk setting, identity, sender credential, or recipient is changed by
this commit. GitHub Actions remains disabled.

## Problem and preserved authority

Clerk's active [development-instance Apple delivery incident](https://status.clerk.com/incidents/01M3M9SSWNTTRMRDFV59B28GCB)
matches the observed Apple SMTP 554 5.7.1 [HM07] rejections. Non-Apple delivery
was successful. This adapter can use the existing company SMTP transport for
the existing staging instance; it does not create an identity provider or
account, change login eligibility, or validate OTPs. Clerk retains OTP issuance,
rate limits and code verification. SMTP acceptance does not prove inbox delivery.

The retired enhancedEmailDeliverability field is not a fix. The current Clerk
Backend API schema marks it ineffective; no deployment decision relies on it.

## Exact contract and boundaries

The hidden POST /external/v1/webhooks/clerk-otp is off by default, unavailable in
production, and requires these exact enabled-runtime settings:

- CLERK_OTP_DELIVERY_ENABLED=true
- AUTHORITY_CLERK_ISSUER=https://resolved-emu-53.clerk.accounts.dev
- CLERK_OTP_INSTANCE_ID=ins_2onn3Q7NDfrwC5oVXgzGe6vA1u4
- CLERK_OTP_CONTRACT_VERSION=verification_code:six-digits:v1
- CLERK_OTP_WEBHOOK_SECRET: a dedicated webhook signing secret, kept server-side.
- Existing SMTP_URL and one EMAIL_FROM_ADDRESS at the exact cosmaneura.com domain.

The contract version explicitly selects the expected strict parser. It is not
an attestation that live delivery was already proved. A bounded authorized
pilot must verify the real custom-owned payload and delivery after healthy
enablement and the template switch, as described below.
The [official webhook schema](https://github.com/clerk/openapi-specs/blob/3b078e5e0c17ad5261f06cb00e0a0943614dacb0/webhooks/2025-04-15.yml)
and current event overview support object=event, type=email.created, instance_id,
and the original event timestamp in milliseconds. The email resource must have
object=email, an id, slug=verification_code, delivered_by_clerk=false, no
suppression_reason, one ASCII recipient address, and a typed six-digit ASCII
data.otp_code inside event.data.data. Unsupported custom-owned shapes return a
visible non-2xx response without a success receipt. No seconds-to-milliseconds
heuristic or custom-template fallback is permitted.

The email.created subscription is event-wide. A valid signed, same-instance
email with an id, a nonempty slug, one valid recipient and the explicit literal
delivered_by_clerk=true returns 200 ignored_clerk_owned. This acknowledges event
receipt only, never delivery. It reads no OTP metadata, sends no message, opens
no Redis ledger and persists no content. Replays and late original events still
owned by Clerk receive the same receipt acknowledgement with a fresh valid Svix
signature; OTP dispatch freshness applies only to custom-owned delivery. Bad
signatures, foreign instances, malformed email resources or ambiguous ownership
remain non-2xx. An unknown template with delivered_by_clerk=false also remains
non-2xx; it is never silently ignored as a custom delivery.

The request is limited to 64 KiB and five seconds of body reading. Svix HMAC
verification uses the exact raw body and bounded headers, with a five-minute
signature freshness check. The original event timestamp separately allows at
most 120 seconds of delivery queue age, a ten-second future skew, and twelve
seconds remaining before dispatch. A retry's new Svix timestamp never resets
the original age. This is a queue-freshness policy, not an exact code-expiry
claim. Clerk documents ten-minute OTP validity and remains its verifier.

Only the six digits enter a fixed Scribe Beta text/HTML email. Subject, HTML,
links, requested IP and other provider metadata are never forwarded. The actual
runtime sender and recipient are independently validated as single addresses;
the envelope has no cc/bcc, reply-to or custom headers. Existing SMTPTransport
connection and credential parameters are reused, with certificate verification
and either mandatory STARTTLS or implicit TLS. Console/file/memory transports,
plaintext fallback and Mailer preprocessors are excluded. The existing general
email path is unchanged; no library or vendor is added.

## Custody, delivery outcome and recovery

Recipient and code exist only in the bounded request/SMTP call. No database,
broker, file, log or telemetry event receives them. Both Sentry error and
transaction hooks drop this endpoint; global exception-local suppression remains
enabled. Fixed responses exclude provider replies and exception details.

Redis stores only keyed HMACs, a random ownership nonce, fixed states, times and
attempt counts, with a 660-second metadata TTL. The original email id identifies
replays even when Svix delivery ids change. A changed signed projection under
the same email id conflicts. Atomic owner fencing prevents concurrent sends.

SMTP connect/authentication/HELO/sender/recipient/DATA rejection proves the
message was not accepted and allows at most three attempts with bounded
backoff. Read timeout, disconnection, outer timeout, cancellation or an
unrecognized result is uncertain. An uncompleted dispatch lease also becomes
uncertain. Those cases stay non-2xx and cannot automatically resend. A returned
recipient refusal is never success. An accepted message whose Redis completion
fails also remains uncertain rather than being sent again. This intentionally
avoids false success and unbounded duplicates; it cannot guarantee exactly-once
inbox delivery. It does not retry a code beyond the original freshness cap.

## Concrete future activation and rollback

Activation requires a separate reviewed rollout decision. The following work
has not been performed or authorized by the local-test approval:

1. Review the exact expected parser version and existing instance binding.
   Confirm the existing company's SMTP transport supports mandatory verified
   TLS and a verified sender suitable for Apple inbox delivery. Metadata
   presence alone does not establish this.
2. Prepare the dedicated signing secret through the approved credential flow,
   the exact webhook subscription, and the configuration change for this
   dormant route. Review the concrete release/config diff and recovery.
3. Enable and verify the healthy staging adapter and email.created subscription
   while templates still use Clerk delivery. Signed Clerk-owned events receive
   receipt-only acknowledgements. This does not prove the custom-owned OTP
   shape or company SMTP delivery, and no prior proof is falsely attested.
4. Start a separately authorized, bounded pilot with exact test recipients and
   sending scope. Change only the existing verification_code template's Clerk
   delivery switch. If global, it routes codes for every staging recipient,
   including non-Apple users; it is not an Apple-only cohort switch. Other
   templates remain Clerk-delivered and are acknowledged without custom sends.
5. During that pilot, confirm a fresh signed custom-owned payload has the
   required slug, original millisecond timestamp, literal false ownership and
   nested six-digit shape. Unsupported shapes fail visibly and halt the pilot.
   Prove fresh Apple/non-Apple inbox delivery and actual Clerk OTP completion
   in Scribe. Do not copy a recipient, code or body into evidence; keep outcome
   evidence numeric/status-only. SMTP acceptance is insufficient.
6. If any contract/delivery proof fails, restore Clerk delivery first, then
   disable the adapter and remove its subscription/configuration if warranted.
   Apple delivery can remain blocked by the original provider incident after
   rollback; restoring the old path does not claim that incident is resolved.

No schema migration or existing inbox rewrite is required. The current latency
release excludes this adapter. A later release must identify the final reviewed
head and preserve unrelated staging settings.

## Local verification and review

The focused suite uses fictional email addresses, synthetic six-digit codes,
a fake aiosmtplib seam behind a real configured SMTPTransport, and one uniquely
owned internal-only disposable Redis fixture. Lua runs in real Redis. Tests
cover raw-body signatures, foreign instance/template, timestamp units and age,
receipt-only Clerk-owned events/replays with no send or ledger, unknown
custom-owned templates and malformed ownership,
duplicate/malformed/oversize payloads, replays and conflicting fingerprints,
concurrent ownership, bounded definite retries, ambiguous outcomes and stale
leases, Redis failures before/after dispatch, cancellation/deadlines, required
TLS, refused recipients, non-SMTP suppression, disabled/production guards, fixed
content, and both telemetry hooks. Existing worker mail and Sentry regressions
also run without external mail.

Changed-code Ruff, focused Pyright, diff-budget checks and independent review
must pass at the exact final pushed head before merge. The test receipt and
review identify that head separately. These virtual tests establish fail-closed
behavior; they do not prove the live Clerk payload or Apple inbox delivery.
