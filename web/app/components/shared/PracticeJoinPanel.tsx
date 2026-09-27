import { useState } from "react"

import { CopyButton } from "~/components/shared/CopyButton"
import { Button } from "~/components/ui/button"
import { toast } from "~/hooks/use-toast"
import {
  type PracticeStatus,
  createBillingCheckout,
  createBillingPortal,
} from "~/lib/practice-api"

const SUBSCRIBED_STATUSES = new Set(["active", "trialing"])

export type BillingActionKind =
  | "none"
  | "portal"
  | "portal-unavailable"
  | "checkout-unavailable"
  | "deferred"
  | "checkout"

export function billingActionKind(
  practice: PracticeStatus,
  _now = new Date(),
): BillingActionKind {
  // A grandfather grant replaces acquisition billing for this exact account,
  // while an existing subscription must remain visible for maintenance.
  if (practice.pilot_access_grant_active) {
    if (practice.billing_subscription_manageable) {
      return practice.billing_management_enabled
        ? "portal"
        : "portal-unavailable"
    }
    return "none"
  }
  // Existing charges must always stay visible/manageable. A comp or a paused
  // acquisition rollout cannot hide the portal for an existing subscription.
  if (practice.billing_checkout_resumable) return "checkout"
  if (practice.billing_subscription_manageable) {
    return practice.billing_management_enabled ? "portal" : "portal-unavailable"
  }
  if (practice.billing_exempt_active) return "none"
  if (!practice.billing_enabled) {
    return practice.entitled ? "none" : "checkout-unavailable"
  }
  // Kept only for safe mixed-version deployments. The current backend always
  // returns null because it extends short trials instead of deferring Checkout.
  if (
    practice.billing_checkout_available_at &&
    new Date(practice.billing_checkout_available_at) > _now
  ) {
    return "deferred"
  }
  return "checkout"
}

function formatMoney(minorUnits: number, currency: string) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
    minimumFractionDigits: minorUnits % 100 === 0 ? 0 : 2,
  }).format(minorUnits / 100)
}

function seats(count: number) {
  return `${count} ${count === 1 ? "seat" : "seats"}`
}

function clinicians(count: number) {
  return `${count} active ${count === 1 ? "clinician" : "clinicians"}`
}

function SeatSummary({ practice }: { practice: PracticeStatus }) {
  const summary = practice.seat_summary
  if (!summary || !practice.billing_subscription_manageable) return null
  const { billed_seats: billed, active_clinicians: active } = summary
  const trialing = practice.subscription_status === "trialing"
  // Only a paid-up subscription is "paying"; past_due, unpaid, paused and
  // incomplete keep a quantity while payment is failing or not yet collected.
  const paying = practice.subscription_status === "active"

  let line: string | null = null
  if (billed !== null) {
    const cost = `${formatMoney(billed * summary.unit_amount, summary.currency)}/${summary.interval}`
    line =
      summary.account_kind === "solo"
        ? `Solo practice · ${seats(billed)} · ${cost}${trialing ? " after your trial" : ""}`
        : paying
          ? `You are paying for ${seats(billed)} · ${cost}`
          : `Your subscription covers ${seats(billed)} · ${cost}${trialing ? " after your trial" : ""}`
  }
  if (!line && !summary.seat_sync_pending) return null

  return (
    <div className="mt-1 text-sm" data-testid="seat-summary">
      {line ? <p>{line}</p> : null}
      {summary.seat_sync_pending ? (
        <p className="text-muted-foreground mt-1">
          {billed !== null
            ? `You have ${clinicians(active)}. Your billed seats are being updated to match.`
            : `You have ${clinicians(active)}. Your billed seats are being updated to ${seats(active)}.`}
        </p>
      ) : null}
    </div>
  )
}

function BillingActions({
  practice,
  returnTo,
}: {
  practice: PracticeStatus
  returnTo?: string
}) {
  const [pending, setPending] = useState(false)
  const action = billingActionKind(practice)
  const showSecondaryPortal =
    practice.billing_portal_available &&
    action !== "portal" &&
    action !== "portal-unavailable"
  if (action === "none" && !showSecondaryPortal) return null

  async function open(
    create: () => Promise<{ url: string }>,
    failureTitle: string,
  ) {
    setPending(true)
    try {
      const { url } = await create()
      window.location.assign(url)
    } catch (error) {
      setPending(false)
      toast({
        title: failureTitle,
        description: error instanceof Error ? error.message : String(error),
        variant: "destructive",
      })
    }
  }

  return (
    <div className="mt-3">
      {action === "none" ? null : action === "portal" ? (
        <Button
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => open(createBillingPortal, "Could not open billing")}
        >
          Manage billing
        </Button>
      ) : action === "portal-unavailable" ? (
        <>
          <Button variant="outline" size="sm" disabled>
            Manage billing unavailable
          </Button>
          <p className="text-destructive mt-1 text-xs">
            Billing maintenance is temporarily unavailable. Contact support;
            your existing subscription has not been hidden or replaced.
          </p>
        </>
      ) : action === "checkout-unavailable" ? (
        <>
          <Button size="sm" disabled>
            Reactivation temporarily unavailable
          </Button>
          <p className="text-muted-foreground mt-1 text-xs">
            Online billing is not available right now. Please contact support;
            do not create another practice or account.
          </p>
        </>
      ) : action === "deferred" ? (
        <Button size="sm" disabled>
          Checkout opens when trial ends
        </Button>
      ) : (
        <Button
          size="sm"
          disabled={pending}
          onClick={() =>
            open(
              () => createBillingCheckout(returnTo),
              "Could not start checkout",
            )
          }
        >
          {practice.billing_checkout_resumable
            ? "Resume checkout"
            : practice.entitled
              ? "Add payment now"
              : "Reactivate Cosmas"}
        </Button>
      )}
      {action === "deferred" ? (
        <p className="text-muted-foreground mt-1 text-xs">
          Your current trial remains active. Checkout becomes available on{" "}
          {new Date(practice.billing_checkout_available_at!).toLocaleString()}.
        </p>
      ) : action === "checkout" && practice.entitled ? (
        <p className="text-muted-foreground mt-1 text-xs">
          You keep every remaining trial hour. If Stripe requires more notice,
          your trial is extended—never shortened—and billing begins afterward.
        </p>
      ) : null}
      {showSecondaryPortal ? (
        <div className={action === "none" ? undefined : "mt-2"}>
          {practice.billing_management_enabled ? (
            <Button
              variant="outline"
              size="sm"
              disabled={pending}
              onClick={() =>
                open(createBillingPortal, "Could not open billing")
              }
            >
              Manage billing and invoices
            </Button>
          ) : (
            <>
              <Button variant="outline" size="sm" disabled>
                Manage billing unavailable
              </Button>
              <p className="text-destructive mt-1 text-xs">
                Billing maintenance is temporarily unavailable. Contact support
                for invoices or account changes.
              </p>
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

export function PracticeJoinPanel({
  practice,
  showBillingActions = false,
  billingReturnTo,
}: {
  practice: PracticeStatus
  showBillingActions?: boolean
  billingReturnTo?: string
}) {
  const isOwner = practice.role === "owner"

  return (
    <div className="w-full rounded-lg border p-4 text-left">
      <p className="text-xs font-semibold tracking-[0.18em] text-[var(--brand-gold)] uppercase">
        Practice
      </p>
      <p className="mt-1 text-lg font-semibold">{practice.legal_name}</p>
      <p className="text-muted-foreground mt-1 text-sm">
        {practice.join_code
          ? `Workforce must sign in from @${practice.email_domain}`
          : `@${practice.email_domain} is the practice email domain`}
      </p>
      {practice.pilot_access_grant_active ? (
        <p className="mt-1 text-sm">
          Grandfathered pilot access is active for this account.
        </p>
      ) : practice.baa_accepted ? (
        <p className="mt-1 text-sm">
          Cosmaneura Inc BAA is in force
          {practice.pastoral_pack ? " · pastoral pack coming soon" : ""}.
        </p>
      ) : (
        <p className="text-destructive mt-1 text-sm">
          BAA is not in force yet. Patient information stays locked.
        </p>
      )}
      {practice.pilot_access_grant_active &&
      practice.billing_subscription_manageable ? (
        <p className="mt-1 text-sm">
          Subscription {practice.subscription_status ?? "requires attention"}.
          Your pilot access does not require billing, but this existing
          subscription remains active until it is managed or canceled.
        </p>
      ) : practice.pilot_access_grant_active ? (
        <p className="text-muted-foreground mt-1 text-sm">
          Legacy pilot access — no billing required.
        </p>
      ) : practice.billing_subscription_manageable ? (
        <p className="mt-1 text-sm">
          Subscription {practice.subscription_status ?? "requires attention"}.
          {practice.billing_exempt_active
            ? " Complimentary access is also active; billing remains manageable."
            : ""}
        </p>
      ) : practice.billing_exempt_active ? (
        <p className="text-muted-foreground mt-1 text-sm">
          Early-adopter access — no billing yet.
        </p>
      ) : practice.billing_exempt ? (
        <p className="text-destructive mt-1 text-sm">
          Early-adopter access ended
          {practice.billing_exempt_until
            ? ` ${new Date(practice.billing_exempt_until).toLocaleDateString()}`
            : ""}
          .
        </p>
      ) : practice.subscription_status &&
        SUBSCRIBED_STATUSES.has(practice.subscription_status) ? (
        <p className="mt-1 text-sm">Subscription active.</p>
      ) : practice.trial_ends_at ? (
        <p className="mt-1 text-sm">
          {practice.entitled ? "Free trial through " : "Free trial ended "}
          {new Date(practice.trial_ends_at).toLocaleDateString(undefined, {
            month: "long",
            day: "numeric",
          })}
          .
        </p>
      ) : null}
      {!practice.pilot_access_grant_active &&
      practice.baa_accepted &&
      practice.access_authorized &&
      !practice.access_revoked &&
      !practice.entitled ? (
        <div className="bg-muted/50 mt-3 rounded-md border p-3" role="alert">
          <p className="text-sm font-semibold">Practice access is paused</p>
          <p className="text-muted-foreground mt-1 text-sm">
            {isOwner
              ? "Billing needs attention before clinical tools can be reopened. Your existing records remain stored and unchanged."
              : "Please contact your practice owner about billing. Only the owner can restore practice access; your existing records remain stored and unchanged."}
          </p>
        </div>
      ) : null}
      {isOwner && showBillingActions ? (
        <>
          <SeatSummary practice={practice} />
          <BillingActions practice={practice} returnTo={billingReturnTo} />
        </>
      ) : null}
      {isOwner && practice.join_code ? (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <code className="bg-muted rounded-md px-3 py-1.5 font-mono text-lg tracking-[0.2em]">
            {practice.join_code}
          </code>
          <CopyButton text={practice.join_code} />
        </div>
      ) : null}
      <p className="text-muted-foreground mt-2 text-xs">
        {practice.pilot_access_grant_active
          ? "Your account is authorized through the approved legacy pilot cohort; no personal BAA attestation is required."
          : isOwner
            ? practice.join_code
              ? "Share this code with clinicians on the same work-email domain. They attest under this BAA — they do not sign a second agreement."
              : "This practice does not accept reusable join-code enrollment."
            : practice.authorization_basis === "organization_roster"
              ? "Your access is authorized through the organization’s approved roster under this BAA; you did not personally attest to it."
              : "You attested under this practice’s Cosmaneura Inc BAA."}
      </p>
    </div>
  )
}
