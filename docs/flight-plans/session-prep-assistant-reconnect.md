# Explicit assistant reconnection after access loss

Tier 2: existing provider authority and record-bound browser session lifecycle.
No backend authorization, schema, dependency, cohort, vendor asset or deployment
configuration change. The existing provider-assistant feature gate still applies.

An idle mount token can expire before the next Session Prep action. The pinned
panel correctly clears its record, content and pending work, but leaves no way
to establish a new authenticated session without reloading the Notes page.

The host now offers an explicit Reconnect assistant action after access loss.
It discards the old authority immediately. Reconnection destroys the old panel,
rechecks support-view status, requests Clerk-backed platform authority and binds
the current appointment in a fresh mount generation. It does not rotate an
expired session, retry automatically, or replay prior turns, drafts or briefs.
Denied access remains closed. Obsolete callbacks cannot alter the new generation.

Synthetic tests execute the real pinned panel for idle expiry, explicit restart,
view-as/sign-out/exchange denial, and a late exchange after record navigation.
Keep the existing terminal-401 and preparation-completion regressions passing.
Run TypeScript and changed-file lint/format checks. Independent review remains
required before release; local synthetic tests do not prove live acceptance.

Release with a separately selected, reviewed Core candidate. Recovery restores
the previous application image; no stored records or database schema are changed.
