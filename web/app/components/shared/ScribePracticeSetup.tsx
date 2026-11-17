import type { ReactNode } from "react"

import type { PracticeStatus } from "~/lib/practice-api"

export const SCRIBE_SETUP_ENROLLMENT = "/onboarding?scribe=setup"

export function isScribeSetupRequest(url: string) {
  const request = new URL(url)
  const values = request.searchParams.getAll("scribe")
  return (
    request.pathname === "/onboarding" &&
    values.length === 1 &&
    values[0] === "setup"
  )
}

export type ScribeSetupGuidance =
  | "enrollment"
  | "owner-baa"
  | "owner-review"
  | "restricted"
  | "check-access"

/** Guidance only: native/Core authorization still checks every access condition. */
export function scribeSetupGuidance(
  practice: PracticeStatus | null,
): ScribeSetupGuidance {
  if (!practice) return "enrollment"
  if (practice.access_revoked || practice.access_ended) return "restricted"
  if (!practice.baa_accepted) {
    return practice.role === "owner" &&
      practice.authorization_basis !== "organization_roster"
      ? "owner-baa"
      : "owner-review"
  }
  if (
    !practice.access_authorized ||
    (!practice.attested &&
      practice.authorization_basis !== "organization_roster")
  ) {
    return "owner-review"
  }
  return "check-access"
}

export function ScribePracticeSetup({
  practice,
  viewAs = false,
  enrollment,
  agreement,
}: {
  practice: PracticeStatus | null
  viewAs?: boolean
  enrollment?: ReactNode
  agreement?: ReactNode
}) {
  const guidance = scribeSetupGuidance(practice)
  if (viewAs)
    return (
      <section>
        <h1 className="text-2xl font-semibold">Use your own account</h1>
        <p className="mt-3">End view-as before opening Scribe setup.</p>
      </section>
    )
  if (guidance === "enrollment" && enrollment) return <>{enrollment}</>
  if (guidance === "owner-baa" && agreement) return <>{agreement}</>
  return (
    <section className="max-w-2xl space-y-5">
      <h1 className="text-3xl font-semibold">Scribe practice setup</h1>
      {guidance === "enrollment" ? (
        <>
          <p>
            Join or create your practice using your own verified account.
            Complete the existing practice enrollment and agreement steps.
          </p>
          <a className="inline-block underline" href={SCRIBE_SETUP_ENROLLMENT}>
            Open practice enrollment
          </a>
        </>
      ) : guidance === "owner-baa" ? (
        <>
          <p>
            Your practice needs the current Business Associate Agreement. Review
            it before deciding whether you are authorized to accept on behalf of
            your practice.
          </p>
          <a className="inline-block underline" href={SCRIBE_SETUP_ENROLLMENT}>
            Review current BAA
          </a>
        </>
      ) : guidance === "owner-review" ? (
        <>
          <p>
            Your practice owner must review the current agreement or your
            workforce enrollment before a new Scribe connection can be created.
          </p>
          <p>
            Joining again does not change an existing membership. Ask your
            practice owner or Cosmaneura support to review the missing
            enrollment step.
          </p>
          <a className="inline-block underline" href="/support">
            Contact support
          </a>
        </>
      ) : guidance === "restricted" ? (
        <>
          <p>
            This membership has been revoked or ended. Scribe setup cannot
            restore access. Contact your practice owner or Cosmaneura support
            for review.
          </p>
          <a className="inline-block underline" href="/support">
            Contact support
          </a>
        </>
      ) : (
        <>
          <p>
            Return to Scribe and tap Check access. Scribe will recheck your
            current account, practice, entitlement and rollout access before
            opening a workspace.
          </p>
          <p>
            This page does not create a Scribe connection or restore cancelled
            access.
          </p>
        </>
      )}
      <p>
        <a className="underline" href="/notes">
          Continue in Core
        </a>
      </p>
    </section>
  )
}
