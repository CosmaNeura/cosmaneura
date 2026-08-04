import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  medicationCreate,
  medicationDelete,
  medicationList,
  medicationUpdate,
  patientReferenceResources,
} from "~/configuration/client"
import type {
  MedicationData,
  PatientResourcePanel,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

const KIND_LABELS: Record<MedicationData["kind"], string> = {
  prescription: "Rx",
  otc: "OTC",
  supplement: "Supplement",
  unknown: "Unknown",
}

// Medications and supplements for a patient, plus one-click deep links into
// the reference resources the provider uses to check interactions and
// nutrient depletions. Extracted rows arrive unconfirmed and must be
// confirmed before they inform anything downstream.
export function MedicationPanel({ patientId }: { patientId: string }) {
  const [medications, setMedications] = useState<MedicationData[]>([])
  const [resources, setResources] = useState<PatientResourcePanel | null>(null)
  const [loading, setLoading] = useState(true)
  const [newName, setNewName] = useState("")

  async function refresh() {
    const [meds, res] = await Promise.all([
      medicationList({ path: { patient_id: patientId } }),
      patientReferenceResources({ path: { patient_id: patientId } }),
    ])
    if (meds.data) setMedications(meds.data)
    if (res.data) setResources(res.data)
    setLoading(false)
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patientId])

  async function confirm(id: string) {
    const { error } = await medicationUpdate({
      path: { medication_id: id },
      body: { confirmed: true },
    })
    if (error) {
      toast({ title: "Could not confirm", variant: "destructive" })
      return
    }
    await refresh()
  }

  async function remove(id: string) {
    const { error } = await medicationDelete({ path: { medication_id: id } })
    if (error) {
      toast({ title: "Could not remove", variant: "destructive" })
      return
    }
    await refresh()
  }

  async function add() {
    const name = newName.trim()
    if (!name) return
    const { error } = await medicationCreate({
      path: { patient_id: patientId },
      body: { name },
    })
    if (error) {
      toast({ title: "Could not add", variant: "destructive" })
      return
    }
    setNewName("")
    await refresh()
  }

  const linksByMedId = new Map(
    (resources?.medications ?? []).map((m) => [m.medication.id, m.links]),
  )

  if (loading) {
    return (
      <section className="mt-6 rounded-lg border p-4">
        <h2 className="text-sm font-semibold">Medications &amp; supplements</h2>
        <p className="text-muted-foreground mt-2 text-sm">Loading…</p>
      </section>
    )
  }

  const unconfirmed = medications.filter((m) => !m.confirmed)
  const confirmed = medications.filter((m) => m.confirmed)

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="mb-3 text-sm font-semibold">
        Medications &amp; supplements
      </h2>

      {unconfirmed.length > 0 && (
        <div className="mb-4">
          <h3 className="text-muted-foreground mb-1 text-xs font-medium uppercase">
            Extracted from the visit — confirm to use
          </h3>
          <ul className="space-y-1">
            {unconfirmed.map((m) => (
              <li
                key={m.id}
                className="flex items-center gap-2 text-sm"
                data-testid="unconfirmed-medication"
              >
                <span className="text-muted-foreground w-16 text-xs">
                  {KIND_LABELS[m.kind]}
                </span>
                <span className="flex-1">
                  {m.name}
                  {m.dosage ? ` · ${m.dosage}` : ""}
                </span>
                <Button size="sm" variant="ghost" onClick={() => confirm(m.id)}>
                  Confirm
                </Button>
                <Button size="sm" variant="ghost" onClick={() => remove(m.id)}>
                  Dismiss
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ul className="space-y-2">
        {confirmed.map((m) => {
          const links = linksByMedId.get(m.id) ?? []
          return (
            <li
              key={m.id}
              className="text-sm"
              data-testid="confirmed-medication"
            >
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground w-16 text-xs">
                  {KIND_LABELS[m.kind]}
                </span>
                <span className="flex-1">
                  {m.name}
                  {m.dosage ? ` · ${m.dosage}` : ""}
                  {m.status === "stopped" && (
                    <span className="text-muted-foreground"> (stopped)</span>
                  )}
                </span>
                <Button size="sm" variant="ghost" onClick={() => remove(m.id)}>
                  Remove
                </Button>
              </div>
              {links.length > 0 && (
                <div className="mt-1 ml-16 flex flex-wrap gap-x-3 gap-y-1">
                  {links.map((link) => (
                    <a
                      key={link.url}
                      href={link.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-600 hover:underline"
                    >
                      {link.label}
                      {link.requires_subscription
                        ? " ↗ (subscription)"
                        : " ↗"}
                    </a>
                  ))}
                </div>
              )}
            </li>
          )
        })}
      </ul>

      {confirmed.length === 0 && unconfirmed.length === 0 && (
        <p className="text-muted-foreground text-sm">
          No medications recorded yet.
        </p>
      )}

      <div className="mt-4 flex gap-2">
        <input
          className="flex-1 rounded-md border px-2 py-1 text-sm"
          placeholder="Add a medication or supplement…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <Button size="sm" onClick={add} disabled={!newName.trim()}>
          Add
        </Button>
      </div>

      {resources && !resources.has_natmed_subscription && (
        <p className="text-muted-foreground mt-3 text-xs">
          Connect your NatMed Pro subscription in Settings to add its
          interaction and nutrient-depletion links here.
        </p>
      )}

      <p className="text-muted-foreground mt-3 text-xs">
        Reference links only — verify against the source. Cosmas does not store
        or interpret this content.
      </p>
    </section>
  )
}
