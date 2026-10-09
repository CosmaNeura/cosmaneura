import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  medicationCreate,
  medicationDelete,
  medicationDrugReference,
  medicationList,
  medicationNutrientDepletions,
  medicationUpdate,
  patientReferenceResources,
} from "~/configuration/client"
import type {
  DrugReferencePanel,
  MedicationData,
  NutrientDepletionPanel,
  PatientResourcePanel,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

const EVIDENCE_LABELS: Record<string, string> = {
  well_established: "well established",
  moderate: "moderate evidence",
  limited: "limited evidence",
}

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
  const [drugReference, setDrugReference] = useState<DrugReferencePanel | null>(
    null,
  )
  const [depletions, setDepletions] = useState<NutrientDepletionPanel | null>(
    null,
  )
  const [loading, setLoading] = useState(true)
  const [newName, setNewName] = useState("")

  async function refresh() {
    const [meds, res, ref, dep] = await Promise.all([
      medicationList({ path: { patient_id: patientId } }),
      patientReferenceResources({ path: { patient_id: patientId } }),
      medicationDrugReference({ path: { patient_id: patientId } }),
      medicationNutrientDepletions({ path: { patient_id: patientId } }),
    ])
    if (meds.data) setMedications(meds.data)
    if (res.data) setResources(res.data)
    if (ref.data) setDrugReference(ref.data)
    if (dep.data) setDepletions(dep.data)
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
  const drugRefByMedId = new Map(
    (drugReference?.medications ?? []).map((m) => [m.medication_id, m]),
  )
  const depletionsByMedId = new Map(
    (depletions?.medications ?? []).map((m) => [m.medication_id, m.depletions]),
  )
  // a medication can be listed with an empty depletions array, so count the
  // depletions themselves rather than the entries
  const hasAnyDepletion = (depletions?.medications ?? []).some(
    (m) => m.depletions.length > 0,
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
                      className="text-primary text-xs hover:underline"
                    >
                      {link.label}
                      {link.requires_subscription
                        ? " ↗ (subscription)"
                        : " ↗"}
                    </a>
                  ))}
                </div>
              )}
              {(() => {
                const deps = depletionsByMedId.get(m.id) ?? []
                if (deps.length === 0) return null
                return (
                  <div className="border-warning/40 bg-warning/5 mt-1 ml-16 rounded-md border p-2">
                    <div className="text-xs font-medium">
                      Possible nutrient depletions
                    </div>
                    <ul className="mt-1 space-y-1">
                      {deps.map((d) => (
                        <li key={d.nutrient} className="text-xs">
                          <span className="font-medium">{d.nutrient}</span>
                          <span className="text-muted-foreground">
                            {" "}
                            — {EVIDENCE_LABELS[d.evidence] ?? d.evidence}
                          </span>
                          <div className="text-muted-foreground">
                            {d.mechanism}{" "}
                            <a
                              href={d.citation.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-primary hover:underline"
                              title={d.citation.reference}
                            >
                              source ↗
                            </a>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                )
              })()}
              {(() => {
                const ref = drugRefByMedId.get(m.id)
                if (!ref) return null
                if (ref.status === "pending") {
                  return (
                    <p className="text-muted-foreground mt-1 ml-16 text-xs italic">
                      Fetching FDA label info…
                    </p>
                  )
                }
                if (ref.status === "ready" && ref.sections.length > 0) {
                  return (
                    <details className="mt-1 ml-16">
                      <summary className="text-muted-foreground cursor-pointer text-xs">
                        FDA label info ({ref.sections.length})
                      </summary>
                      <div className="mt-1 space-y-2">
                        {ref.sections.map((s) => (
                          <div key={s.key}>
                            <div className="text-xs font-medium">{s.label}</div>
                            <p className="text-muted-foreground text-xs whitespace-pre-line">
                              {s.text}
                            </p>
                            {s.truncated && (
                              <p className="text-xs italic">
                                Shortened for display — this is not the complete
                                section.{" "}
                                {s.source_url && (
                                  <a
                                    className="underline"
                                    href={s.source_url}
                                    target="_blank"
                                    rel="noreferrer"
                                  >
                                    Read the full label on DailyMed
                                  </a>
                                )}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    </details>
                  )
                }
                return null
              })()}
            </li>
          )
        })}
      </ul>

      {confirmed.length === 0 && unconfirmed.length === 0 && (
        <p className="text-muted-foreground text-sm">
          No medications recorded yet.
        </p>
      )}

      {/* The depletion reference is a small curated seed, not a complete
          database. The server ships this caveat with the data; show it
          wherever a depletion is shown. */}
      {depletions?.disclaimer && hasAnyDepletion && (
        <p
          className="text-muted-foreground mt-3 text-xs"
          data-testid="depletion-disclaimer"
        >
          {depletions.disclaimer}
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
