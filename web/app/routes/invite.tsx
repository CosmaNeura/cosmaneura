import { useState } from "react"
import { href, useNavigate } from "react-router"

import type { Route } from "./+types/invite"

import { BrandLogo } from "~/components/brand/BrandLogo"
import { WorkforceAttestation } from "~/components/shared/WorkforceAttestation"
import { Button } from "~/components/ui/button"
import { getClient } from "~/configuration/clerk"
import {
  type PracticeInvitationInspection,
  acceptPracticeInvitation,
  inspectPracticeInvitation,
} from "~/configuration/client"
import { COMPANY } from "~/lib/baa"
import { errorDetail } from "~/lib/error-detail"
import {
  capturePendingInvitation,
  clearPendingInvitation,
  keepPendingInvitationThroughSignOut,
  parkPendingInvitation,
  unparkPendingInvitation,
} from "~/lib/pending-invitation"

import { useClerk } from "@clerk/clerk-react"

export const ACCOUNT_IN_ANOTHER_PRACTICE =
  "This account already belongs to another practice. Sign up with a different email to join a new one."

type InviteLoad =
  | { kind: "missing" }
  | { kind: "signing_in" }
  | { kind: "unknown" }
  | { kind: "error"; detail: string }
  | {
      kind: "inspected"
      token: string
      inspection: PracticeInvitationInspection
    }

// Terminal answers: keeping the token would only bounce the doctor back here.
const SPENT_STATES = new Set<PracticeInvitationInspection["state"]>([
  "expired",
  "revoked",
  "used",
  "member_elsewhere",
  "already_member",
  "managed_account",
])

export async function clientLoader(): Promise<InviteLoad> {
  // Capture before getClient(): a signed-out visitor is sent to sign-in with
  // only the path, and the fragment would be lost.
  const token = capturePendingInvitation()
  if (!token) return { kind: "missing" }
  // null while a signed-out visitor is being sent to sign-in
  if (!(await getClient())) return { kind: "signing_in" }
  const { data, error, response } = await inspectPracticeInvitation({
    body: { token },
  })
  if (data) {
    if (SPENT_STATES.has(data.state)) clearPendingInvitation()
    else if (data.state === "ready") unparkPendingInvitation()
    else parkPendingInvitation(token)
    return { kind: "inspected", token, inspection: data }
  }
  if (response?.status === 404) {
    clearPendingInvitation()
    return { kind: "unknown" }
  }
  parkPendingInvitation(token)
  return {
    kind: "error",
    detail: errorDetail(error) ?? "This invitation could not be checked.",
  }
}

function Notice({
  title,
  children,
}: {
  title: string
  children?: React.ReactNode
}) {
  return (
    <div className="max-w-xl">
      <h1
        className="text-4xl font-semibold text-[var(--brand-navy-deep)]"
        style={{ fontFamily: "'Cormorant Garamond', serif" }}
      >
        {title}
      </h1>
      <div className="text-muted-foreground mt-4 space-y-4 leading-relaxed">
        {children}
      </div>
    </div>
  )
}

function ContinueButton({ label }: { label: string }) {
  const navigate = useNavigate()
  return (
    <Button
      variant="outline"
      onClick={() => {
        clearPendingInvitation()
        void navigate(href("/onboarding"))
      }}
    >
      {label}
    </Button>
  )
}

function AcceptInvitation({
  token,
  inspection,
}: {
  token: string
  inspection: PracticeInvitationInspection
}) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const legalName = inspection.legal_name ?? "this practice"

  return (
    <div className="max-w-xl">
      <p className="text-xs font-semibold tracking-[0.28em] text-[var(--brand-gold)] uppercase">
        Invitation
      </p>
      <h1
        className="mt-3 text-4xl font-semibold text-[var(--brand-navy-deep)]"
        style={{ fontFamily: "'Cormorant Garamond', serif" }}
      >
        Join {legalName}
      </h1>
      <p className="text-muted-foreground mt-3">
        You attest under the practice’s BAA with {COMPANY}. You do not become a
        separate party.
      </p>
      <div className="bg-card mt-6 rounded-xl border p-5">
        <p className="text-xs font-semibold tracking-[0.18em] text-[var(--brand-gold)] uppercase">
          Covered entity
        </p>
        <p
          className="mt-1 text-2xl"
          style={{ fontFamily: "'Cormorant Garamond', serif" }}
        >
          {legalName}
        </p>
        <p className="text-muted-foreground mt-1 text-sm">BAA on file</p>
        <WorkforceAttestation
          legalName={legalName}
          busy={busy}
          onAttest={(attestation) => {
            setBusy(true)
            setError(null)
            void acceptPracticeInvitation({ body: { token, ...attestation } })
              .then(({ data, error: failure }) => {
                if (data) {
                  clearPendingInvitation()
                  void navigate(href("/onboarding"))
                  return
                }
                setError(
                  errorDetail(failure) ??
                    "The invitation could not be accepted.",
                )
              })
              .catch(() => setError("The invitation could not be accepted."))
              .finally(() => setBusy(false))
          }}
        />
      </div>
      {error ? (
        <p role="alert" className="text-destructive mt-4 text-sm">
          {error}
        </p>
      ) : null}
      <div className="mt-6">
        <ContinueButton label="Not now" />
      </div>
    </div>
  )
}

function InvitationState({
  token,
  inspection,
}: {
  token: string
  inspection: PracticeInvitationInspection
}) {
  const clerk = useClerk()
  switch (inspection.state) {
    case "ready":
      return <AcceptInvitation token={token} inspection={inspection} />
    case "wrong_account":
      return (
        <Notice title="This invitation is for another email">
          <p>
            This invitation was sent to a different email address. Sign in with
            that address.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button
              onClick={() => {
                keepPendingInvitationThroughSignOut()
                void clerk.signOut({
                  redirectUrl: `/sign-in?redirect_url=${encodeURIComponent("/invite")}`,
                })
              }}
            >
              Sign in with another account
            </Button>
            <ContinueButton label="Not now" />
          </div>
        </Notice>
      )
    case "member_elsewhere":
      return (
        <Notice title="This account belongs to another practice">
          <p>{ACCOUNT_IN_ANOTHER_PRACTICE}</p>
          <Button
            onClick={() => void clerk.signOut({ redirectUrl: "/sign-up" })}
          >
            Sign out
          </Button>
        </Notice>
      )
    case "already_member":
      return (
        <Notice title={`You’re already a member of ${inspection.legal_name}`}>
          <ContinueButton label="Continue" />
        </Notice>
      )
    case "practice_not_ready":
      return (
        <Notice title="This practice isn’t ready yet">
          <p>
            {inspection.legal_name} can’t add clinicians until its agreement
            with {COMPANY} is in place. Ask the practice owner, then open the
            link again.
          </p>
          <ContinueButton label="Not now" />
        </Notice>
      )
    case "managed_account":
      return (
        <Notice title="This account uses organization-managed access">
          <p>
            Your organization enrolls you directly, so this invitation can’t be
            used with this account. Contact your organization administrator or
            Cosmaneura support.
          </p>
          <ContinueButton label="Continue" />
        </Notice>
      )
    case "expired":
    case "revoked":
    case "used":
      return (
        <Notice title="This invitation is no longer active">
          <p>Ask the practice owner for a new one.</p>
          <ContinueButton label="Continue" />
        </Notice>
      )
  }
}

export default function InvitePage({ loaderData }: Route.ComponentProps) {
  return (
    <div className="bg-background min-h-screen">
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-5xl items-center px-5">
          <BrandLogo variant="dark" size="sm" />
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-5 py-10">
        {loaderData.kind === "signing_in" ? null : loaderData.kind ===
          "inspected" ? (
          <InvitationState
            token={loaderData.token}
            inspection={loaderData.inspection}
          />
        ) : loaderData.kind === "missing" ? (
          <Notice title="Open your invitation link">
            <p>
              Use the link from your invitation email, or ask the practice owner
              to send it again.
            </p>
          </Notice>
        ) : loaderData.kind === "unknown" ? (
          <Notice title="This invitation link isn’t valid">
            <p>Ask the practice owner for a new one.</p>
          </Notice>
        ) : (
          <Notice title="We couldn’t check this invitation">
            <p role="alert">{loaderData.detail}</p>
            <ContinueButton label="Not now" />
          </Notice>
        )}
      </main>
    </div>
  )
}
