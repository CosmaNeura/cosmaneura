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
  | "deferred"
  | "checkout"

export function billingActionKind(
  practice: PracticeStatus,
  now = new Date(),
): BillingActionKind {
  // Existing charges must always stay visible/manageable. A comp or a paused
  // acquisition rollout cannot hide the portal for an existing subscription.
  if (practice.billing_checkout_resumable) return "checkout"
  if (practice.billing_subscription_manageable) {
    return practice.billing_management_enabled ? "portal" : "portal-unavailable"
  }
  if (!practice.billing_enabled || practice.billing_exempt_active) return "none"
  if (
    practice.billing_checkout_available_at &&
    new Date(practice.billing_checkout_available_at) > now
  ) {
    return "deferred"
  }
  return "checkout"
}

function BillingActions({ practice }: { practice: PracticeStatus }) {
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
      ) : action === "deferred" ? (
        <Button size="sm" disabled>
          Checkout opens when trial ends
        </Button>
      ) : (
        <Button
          size="sm"
          disabled={pending}
          onClick={() =>
            open(createBillingCheckout, "Could not start checkout")
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
          To preserve every remaining trial hour, checkout becomes available on{" "}
          {new Date(practice.billing_checkout_available_at!).toLocaleString()}.
        </p>
      ) : action === "checkout" && practice.entitled ? (
        <p className="text-muted-foreground mt-1 text-xs">
          Checkout carries the exact remaining trial end into Stripe; billing
          starts only after that timestamp.
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
}: {
  practice: PracticeStatus
  showBillingActions?: boolean
}) {
  const isOwner = practice.role === "owner"

  return (
    <div className="w-full rounded-lg border p-4 text-left">
      <p className="text-xs font-semibold tracking-[0.18em] text-[var(--brand-gold)] uppercase">
        Practice
      </p>
      <p className="mt-1 text-lg font-semibold">{practice.legal_name}</p>
      <p className="text-muted-foreground mt-1 text-sm">
        Workforce must sign in from @{practice.email_domain}
      </p>
      {practice.baa_accepted ? (
        <p className="mt-1 text-sm">
          Cosmaneura Inc BAA is in force
          {practice.pastoral_pack ? " · pastoral pack on" : ""}.
        </p>
      ) : (
        <p className="text-destructive mt-1 text-sm">
          BAA is not in force yet. Patient information stays locked.
        </p>
      )}
      {practice.billing_subscription_manageable ? (
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
      {isOwner && showBillingActions ? (
        <BillingActions practice={practice} />
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
        {isOwner
          ? practice.join_code
            ? "Share this code with clinicians on the same work-email domain. They attest under this BAA — they do not sign a second agreement."
            : "Workforce access is managed through the organization’s approved roster."
          : practice.authorization_basis === "organization_roster"
            ? "Your access is authorized through the organization’s approved roster under this BAA; you did not personally attest to it."
            : "You attested under this practice’s Cosmaneura Inc BAA."}
      </p>
    </div>
  )
}
