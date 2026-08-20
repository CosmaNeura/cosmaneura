import { CopyButton } from "~/components/shared/CopyButton"
import type { PracticeStatus } from "~/lib/practice-api"

export function PracticeJoinPanel({ practice }: { practice: PracticeStatus }) {
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
      {isOwner ? (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <code className="bg-muted rounded-md px-3 py-1.5 font-mono text-lg tracking-[0.2em]">
            {practice.join_code}
          </code>
          <CopyButton text={practice.join_code} />
        </div>
      ) : null}
      <p className="text-muted-foreground mt-2 text-xs">
        {isOwner
          ? "Share this code with clinicians on the same work-email domain. They attest under this BAA — they do not sign a second agreement."
          : "You attested under this practice’s Cosmaneura Inc BAA."}
      </p>
    </div>
  )
}
