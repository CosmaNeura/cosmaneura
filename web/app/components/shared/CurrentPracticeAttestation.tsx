import { useState } from "react"

import {
  WorkforceAttestation,
  type WorkforceAttestationSubmission,
} from "~/components/shared/WorkforceAttestation"
import {
  type PracticeStatus,
  attestCurrentPracticeMember,
} from "~/lib/practice-api"

export function CurrentPracticeAttestation({
  practice,
  viewAsActive,
  onPracticeChange,
}: {
  practice: PracticeStatus
  viewAsActive: boolean
  onPracticeChange: (practice: PracticeStatus) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (
    viewAsActive ||
    practice.self_attestation_available !== true ||
    !practice.attestation_practice_ref ||
    !practice.attestation_baa_reference ||
    practice.attested ||
    !practice.baa_accepted ||
    practice.access_revoked ||
    practice.access_ended ||
    practice.access_ends_at !== null
  ) {
    return null
  }

  async function record(submission: WorkforceAttestationSubmission) {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const next = await attestCurrentPracticeMember({
        practice_ref: practice.attestation_practice_ref!,
        baa_reference: practice.attestation_baa_reference!,
        ...submission,
      })
      onPracticeChange(next)
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Could not record attestation.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="not-prose rounded-lg border p-4 text-sm">
      <h3 className="font-semibold">Finish Scribe workforce setup</h3>
      <p className="text-muted-foreground mt-2">
        You are already a member of {practice.legal_name}. Scribe setup needs
        your own workforce acknowledgements under this practice’s BAA. Review
        each acknowledgement and type your name.
      </p>
      <p className="text-muted-foreground mt-2">
        Agreement: {practice.attestation_baa_reference}. Scribe also checks
        current practice access before recording can begin.
      </p>
      <WorkforceAttestation
        key={JSON.stringify([
          practice.attestation_practice_ref,
          practice.attestation_baa_reference,
          practice.legal_name,
        ])}
        legalName={practice.legal_name}
        busy={busy}
        busyLabel="Recording…"
        submitLabel="Record my workforce attestation"
        onAttest={(submission) => void record(submission)}
      />
      {error ? (
        <p role="alert" className="text-destructive mt-3">
          {error}
        </p>
      ) : null}
    </section>
  )
}
