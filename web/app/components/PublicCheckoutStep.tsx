import { useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type PublicOnboardingState,
  createBillingCheckout,
} from "~/lib/practice-api"

export function subscriptionStatusLabel(
  state: PublicOnboardingState["subscription_state"],
) {
  const labels: Record<PublicOnboardingState["subscription_state"], string> = {
    none: "Checkout not started",
    incomplete: "Checkout incomplete",
    incomplete_expired: "Checkout expired",
    pending_verification: "Billing verification pending",
    invalid: "Billing status unavailable",
    trialing: "14-day trial active",
    active: "Subscription active",
    past_due: "Payment past due",
    unpaid: "Payment required",
    paused: "Subscription paused",
    canceled: "Subscription canceled",
  }
  return labels[state] ?? "Billing status unavailable"
}

export function trialEndCopy(state: PublicOnboardingState) {
  if (!state.checkout_started) return "The trial has not started."
  if (!state.trial_ends_at) return "The authoritative trial end is unavailable."
  const end = new Date(state.trial_ends_at)
  if (Number.isNaN(end.valueOf())) {
    return "The authoritative trial end is unavailable."
  }
  return `Trial ends ${new Intl.DateTimeFormat(undefined, {
    dateStyle: "long",
    timeStyle: "short",
  }).format(end)}.`
}

export function PublicCheckoutStep({
  state,
}: {
  state: PublicOnboardingState
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const owner = state.practice?.role === "owner"
  const checkoutPreviouslyStarted = state.checkout_started

  async function startCheckout() {
    setBusy(true)
    setError(null)
    try {
      const { url } = await createBillingCheckout("/onboarding")
      window.location.href = url
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start Checkout.")
      setBusy(false)
    }
  }

  return (
    <div className="max-w-2xl">
      <p className="text-xs font-semibold tracking-[0.28em] text-[var(--brand-gold)] uppercase">
        05 · Checkout
      </p>
      <h1
        className="mt-3 text-4xl font-semibold text-[var(--brand-navy-deep)] sm:text-5xl"
        style={{ fontFamily: "'Cormorant Garamond', serif" }}
      >
        {checkoutPreviouslyStarted
          ? "Continue secure Checkout"
          : "Start the public pilot"}
      </h1>
      <div className="bg-card mt-6 rounded-xl border p-6">
        <p className="text-lg font-semibold text-[var(--brand-navy-deep)]">
          $89 per active clinician each month
        </p>
        <p className="text-muted-foreground mt-3 leading-relaxed">
          {checkoutPreviouslyStarted
            ? "Your fixed 14-day trial clock began when the first Checkout Session was created. Returning to Checkout does not restart or extend it."
            : "A card is collected in secure Checkout. Your fixed 14-day trial begins immediately when Checkout is authoritatively created—even if credential review is still pending."}
        </p>
        {checkoutPreviouslyStarted ? (
          <p className="text-muted-foreground mt-3 text-sm leading-relaxed">
            {subscriptionStatusLabel(state.subscription_state)}.{" "}
            {trialEndCopy(state)}
          </p>
        ) : null}
        <p className="text-muted-foreground mt-3 leading-relaxed">
          Credential approval is a separate safety gate. Patient-data (PHI)
          capabilities remain locked until your credentials are approved, and
          the practice can have at most 20 active clinician seats.
        </p>
      </div>
      {owner ? (
        <Button
          type="button"
          size="lg"
          className="mt-6"
          disabled={busy}
          onClick={() => void startCheckout()}
        >
          {busy
            ? "Opening secure Checkout…"
            : checkoutPreviouslyStarted
              ? "Continue secure Checkout"
              : "Add card and start 14-day trial"}
        </Button>
      ) : (
        <p className="bg-muted/40 mt-6 rounded-xl border p-4 text-sm">
          A practice owner must{" "}
          {checkoutPreviouslyStarted ? "continue" : "start"} secure Checkout.
          Your access remains locked while you wait.
        </p>
      )}
      {error ? (
        <p role="alert" className="text-destructive mt-4 text-sm">
          {error}
        </p>
      ) : null}
    </div>
  )
}
