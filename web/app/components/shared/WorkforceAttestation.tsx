import { useState } from "react"

import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import { Label } from "~/components/ui/label"
import { WORKFORCE_CHECKS } from "~/lib/baa"

export type WorkforceAttestationSubmission = {
  attestation_name: string
  checks: string[]
}

export function Field({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5 text-left">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

export function CheckRow({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  children: React.ReactNode
}) {
  return (
    <label className="flex gap-3 text-left text-sm leading-snug">
      <input
        type="checkbox"
        className="mt-0.5 size-4 accent-[var(--brand-navy-deep)]"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{children}</span>
    </label>
  )
}

/** The clinician's own workforce attestation, for a join code or an invitation. */
export function WorkforceAttestation({
  legalName,
  busy,
  onAttest,
}: {
  legalName: string
  busy: boolean
  onAttest: (submission: WorkforceAttestationSubmission) => void
}) {
  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [name, setName] = useState("")
  const ready =
    WORKFORCE_CHECKS.every((item) => checks[item.id]) && name.trim().length >= 2

  return (
    <div className="mt-5 grid gap-4">
      {WORKFORCE_CHECKS.map((item) => (
        <CheckRow
          key={item.id}
          checked={Boolean(checks[item.id])}
          onChange={(next) =>
            setChecks((prev) => ({ ...prev, [item.id]: next }))
          }
        >
          {item.label}
        </CheckRow>
      ))}
      <Field label="Typed name" htmlFor="attestation">
        <Input
          id="attestation"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Button
        type="button"
        disabled={!ready || busy}
        onClick={() =>
          onAttest({
            attestation_name: name,
            checks: WORKFORCE_CHECKS.map((item) => item.id).filter(
              (id) => checks[id],
            ),
          })
        }
        size="lg"
      >
        {busy ? "Joining…" : `Attest and join ${legalName}`}
      </Button>
    </div>
  )
}
