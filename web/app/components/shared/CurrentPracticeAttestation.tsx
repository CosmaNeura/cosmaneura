import { useEffect, useRef, useState } from "react"

import {
  WorkforceAttestation,
  type WorkforceAttestationSubmission,
} from "~/components/shared/WorkforceAttestation"
import {
  type BrowserPrincipal,
  type BrowserPrincipalIdentity,
  sameBrowserPrincipal,
  useBrowserPrincipal,
} from "~/configuration/clerk"
import {
  type PracticeStatus,
  attestCurrentPracticeMember,
} from "~/lib/practice-api"

type Props = {
  practice: PracticeStatus
  viewAsActive: boolean
  onPracticeChange: (practice: PracticeStatus) => void
}

export function CurrentPracticeAttestation(props: Props) {
  const { identity } = useBrowserPrincipal()
  const principal = identity.current()
  const observed = useRef(principal)
  const revision = useRef(0)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const update = (next: BrowserPrincipal) => {
      if (!sameBrowserPrincipal(observed.current, next)) {
        observed.current = next
        revision.current += 1
        setVersion(revision.current)
      }
    }
    update(identity.current())
    return identity.subscribe(update)
  }, [identity])
  const { practice, viewAsActive } = props
  const contextKey = JSON.stringify([
    principal.isLoaded,
    principal.userId,
    principal.sessionId,
    version,
    practice.attestation_practice_ref,
    practice.attestation_baa_reference,
    practice.legal_name,
    viewAsActive,
    practice.self_attestation_available,
    practice.attested,
    practice.baa_accepted,
    practice.access_revoked,
    practice.access_ended,
    practice.access_ends_at,
  ])
  const currentContext = useRef(contextKey)
  currentContext.current = contextKey
  const expectedRevision = revision.current
  if (
    !principal.isLoaded ||
    !principal.userId ||
    !principal.sessionId ||
    viewAsActive ||
    practice.self_attestation_available !== true ||
    !practice.attestation_practice_ref ||
    !practice.attestation_baa_reference ||
    practice.attested ||
    !practice.baa_accepted ||
    practice.access_revoked ||
    practice.access_ended ||
    practice.access_ends_at !== null
  )
    return null

  return (
    <AttestationForm
      key={contextKey}
      {...props}
      principal={principal}
      identity={identity}
      isContextCurrent={() =>
        currentContext.current === contextKey &&
        revision.current === expectedRevision &&
        sameBrowserPrincipal(identity.current(), principal)
      }
    />
  )
}

function AttestationForm({
  practice,
  onPracticeChange,
  principal,
  identity,
  isContextCurrent,
}: Props & {
  principal: BrowserPrincipal
  identity: BrowserPrincipalIdentity
  isContextCurrent: () => boolean
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pending = useRef(false)
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  async function record(submission: WorkforceAttestationSubmission) {
    let read = identity.current
    const isCurrent = () =>
      mounted.current &&
      isContextCurrent() &&
      sameBrowserPrincipal(read(), principal)
    if (pending.current || !isCurrent()) return
    pending.current = true
    setBusy(true)
    setError(null)
    try {
      read = await identity.readCurrent()
      if (!isCurrent()) return
      const next = await attestCurrentPracticeMember(
        {
          practice_ref: practice.attestation_practice_ref!,
          baa_reference: practice.attestation_baa_reference!,
          ...submission,
        },
        { principal, isCurrent },
      )
      if (isCurrent()) onPracticeChange(next)
    } catch (failure) {
      if (isCurrent())
        setError(
          failure instanceof Error
            ? failure.message
            : "Could not record attestation.",
        )
    } finally {
      if (isCurrent()) {
        pending.current = false
        setBusy(false)
      }
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
