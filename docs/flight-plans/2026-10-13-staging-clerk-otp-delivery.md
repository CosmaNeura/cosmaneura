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
- CLERK_OTP_VERIFIED_CONTRACT=verification_code:six-digits:v1
- CLERK_OTP_WEBHOOK_SECRET: a dedicated webhook signing secret, kept server-side.
- Existing SMTP_URL and one EMAIL_FROM_ADDRESS at the exact cosmaneura.com domain.

The contract marker is an operator attestation after inspecting a fresh signed
payload from this exact instance. It is not automatically set by this code.
The [official webhook schema](https://github.com/clerk/openapi-specs/blob/3b078e5e0c17ad5261f06cb00e0a0943614dacb0/webhooks/2025-04-15.yml)
and current event overview support object=event, type=email.created, instance_id,
and the original event timestamp in milliseconds. The email resource must have
object=email, an id, slug=verification_code, delivered_by_clerk=false, no
suppression_reason, one ASCII recipient address, and a typed six-digit ASCII
data.otp_code inside event.data.data. Unsupported signed shapes return a visible
non-2xx response without a success receipt. No seconds-to-milliseconds heuristic
or template fallback is permitted.

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

1. Confirm a fresh signed payload from the exact existing instance has the
   required slug, timestamp units, ownership flag and nested six-digit shape.
   Do not copy an actual recipient/code/body into evidence.
2. Confirm the existing company's SMTP transport supports mandatory verified
   TLS and a verified sender suitable for Apple inbox delivery. Metadata
   presence alone does not establish this.
3. Prepare the dedicated signing secret through the approved credential flow,
   the exact webhook subscription, and the configuration change for this
   dormant route. Review the concrete release/config diff and recovery.
4. Enable and verify the healthy staging adapter before changing Clerk delivery.
   Change only the existing verification_code template's Clerk delivery switch
   and the required email.created subscription. If the switch is global, it
   routes codes for every staging recipient, including non-Apple users; it is
   not an Apple-only cohort switch. Other templates remain Clerk-delivered.
5. With separate exact recipient/send authorization, prove fresh Apple and
   non-Apple inbox delivery and actual Clerk OTP completion in Scribe. Keep
   outcome evidence numeric/status-only. API SMTP acceptance is insufficient.
6. If any proof fails, restore Clerk delivery of this template first, then
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
