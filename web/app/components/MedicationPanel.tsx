import { useCallback, useEffect, useRef, useState } from "react"

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
import { appointmentDateFormat } from "~/lib/date"

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

// Confirmed medications remain patient-wide. Unconfirmed extraction suggestions
// are actionable only when their authoritative source is the open visit.
export function MedicationPanel({
  patientId,
  appointmentId,
}: {
  patientId: string
  appointmentId?: string
}) {
  const scopeKey = JSON.stringify([patientId, appointmentId ?? null])
  const liveScope = useRef(scopeKey)
  const scopeGeneration = useRef(0)
  if (liveScope.current !== scopeKey) scopeGeneration.current += 1
  liveScope.current = scopeKey
  const mutationPending = useRef(false)
  const requestSerial = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const [loadedScope, setLoadedScope] = useState<string | null>(null)
  const [medications, setMedications] = useState<MedicationData[]>([])
  const [resources, setResources] = useState<PatientResourcePanel | null>(null)
  const [drugReference, setDrugReference] = useState<DrugReferencePanel | null>(
    null,
  )
  const [depletions, setDepletions] = useState<NutrientDepletionPanel | null>(
    null,
  )
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [newName, setNewName] = useState("")

  const refresh = useCallback(async () => {
    const generation = scopeGeneration.current
    const serial = ++requestSerial.current
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const results = await Promise.allSettled([
      medicationList({
        path: { patient_id: patientId },
        signal: request.signal,
      }),
      patientReferenceResources({
        path: { patient_id: patientId },
        signal: request.signal,
      }),
      medicationDrugReference({
        path: { patient_id: patientId },
        signal: request.signal,
      }),
      medicationNutrientDepletions({
        path: { patient_id: patientId },
        signal: request.signal,
      }),
    ])
    if (
      request.signal.aborted ||
      liveScope.current !== scopeKey ||
      requestSerial.current !== serial ||
      scopeGeneration.current !== generation
    )
      return
    const [meds, res, ref, dep] = results
    const data = meds.status === "fulfilled" ? meds.value.data : undefined
    setMedications(data ?? [])
    setResources(res.status === "fulfilled" ? (res.value.data ?? null) : null)
    setDrugReference(
      ref.status === "fulfilled" ? (ref.value.data ?? null) : null,
    )
    setDepletions(dep.status === "fulfilled" ? (dep.value.data ?? null) : null)
    setLoadError(data === undefined)
    setLoadedScope(scopeKey)
    setLoading(false)
  }, [patientId, scopeKey])

  useEffect(() => {
    mutationPending.current = false
    setLoading(true)
    setLoadError(false)
    setBusy(false)
    setMedications([])
    setResources(null)
    setDrugReference(null)
    setDepletions(null)
    setNewName("")
    setLoadedScope(null)
    void refresh()
    return () => {
      scopeGeneration.current += 1
      requestSerial.current += 1
      controller.current?.abort()
    }
  }, [refresh])

  async function mutate(
    operation: () => Promise<{ error?: unknown }>,
    failure: string,
    after?: () => void,
  ) {
    if (
      mutationPending.current ||
      loadedScope !== scopeKey ||
      liveScope.current !== scopeKey
    )
      return
    const generation = scopeGeneration.current
    const current = () =>
      liveScope.current === scopeKey && scopeGeneration.current === generation
    mutationPending.current = true
    setBusy(true)
    try {
      const { error } = await operation()
      if (!current()) return
      if (error) {
        toast({ title: failure, variant: "destructive" })
        return
      }
      after?.()
      await refresh()
    } catch {
      if (current()) toast({ title: failure, variant: "destructive" })
    } finally {
      if (current()) {
        mutationPending.current = false
        setBusy(false)
      }
    }
  }

  async function confirm(id: string) {
    const medication = medications.find((medication) => medication.id === id)
    if (
      !appointmentId ||
      !medication ||
      medication.confirmed ||
      medication.appointment_id !== appointmentId
    )
      return
    await mutate(
      () =>
        medicationUpdate({
          path: { medication_id: id },
          body: { confirmed: true },
        }),
      "Could not confirm",
    )
  }

  async function remove(id: string) {
    await mutate(
      () => medicationDelete({ path: { medication_id: id } }),
      "Could not remove",
    )
  }

  async function add() {
    const name = newName.trim()
    if (!name) return
    await mutate(
      () =>
        medicationCreate({ path: { patient_id: patientId }, body: { name } }),
      "Could not add",
      () => setNewName(""),
    )
  }

  function displayKey(medication: MedicationData) {
    // Dosage carries the extracted frequency. Keep distinct descriptions and
    // statuses separate; no semantic dose equivalence is inferred here.
    return JSON.stringify([
      medication.appointment_id ?? `unknown:${medication.id}`,
      medication.display_name ?? medication.name,
      medication.dosage ?? null,
      medication.kind,
      medication.status,
    ])
  }

  function groupExact(rows: MedicationData[]) {
    const groups = new Map<string, MedicationData[]>()
    rows.forEach((medication) => {
      const key = displayKey(medication)
      groups.set(key, [...(groups.get(key) ?? []), medication])
    })
    return [...groups.values()]
  }

  function sourceLink(medication: MedicationData) {
    const date = medication.source_appointment_date
      ? appointmentDateFormat(medication.source_appointment_date)
      : "Date unavailable"
    return medication.appointment_id ? (
      <a
        className="text-primary text-xs underline underline-offset-4"
        href={`/notes/${encodeURIComponent(medication.appointment_id)}`}
        target="_blank"
        rel="noopener noreferrer"
      >
        Source visit · {date}
        <span className="sr-only"> · Open in a new tab</span>
      </a>
    ) : (
      <span className="text-muted-foreground text-xs">
        Source visit unavailable · {date}
      </span>
    )
  }

  function extractionRow(group: MedicationData[], actionable: boolean) {
    const medication = group[0]
    return (
      <li
        key={displayKey(medication)}
        className="rounded-md border p-2 text-sm"
        data-testid={
          actionable ? "unconfirmed-medication" : "historical-medication"
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground text-xs">
            {KIND_LABELS[medication.kind]}
          </span>
          <span className="flex-1">
            {medication.display_name ?? medication.name}
            {medication.dosage ? ` · ${medication.dosage}` : ""}
            {medication.status === "stopped" && (
              <span className="text-muted-foreground">
                {" "}
                (stopped in source)
              </span>
            )}
          </span>
          {actionable && (
            <>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => confirm(medication.id)}
              >
                Confirm for patient list
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => remove(medication.id)}
              >
                Dismiss
              </Button>
            </>
          )}
        </div>
        {sourceLink(medication)}
        {group.length > 1 && (
          <p className="text-muted-foreground text-xs">
            {group.length} matching extractions from this source visit.
          </p>
        )}
      </li>
    )
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

  if (loading || loadedScope !== scopeKey) {
    return (
      <section className="mt-6 rounded-lg border p-4">
        <h2 className="text-sm font-semibold">Medications &amp; supplements</h2>
        <p className="text-muted-foreground mt-2 text-sm">Loading…</p>
      </section>
    )
  }

  if (loadError)
    return (
      <section className="mt-6 rounded-lg border p-4">
        <h2 className="text-sm font-semibold">Medications &amp; supplements</h2>
        <p className="mt-2 text-sm" role="status">
          Could not load the medication list for this patient.
        </p>
        <Button
          className="mt-2"
          size="sm"
          variant="outline"
          onClick={() => {
            setLoading(true)
            void refresh()
          }}
        >
          Retry medication list
        </Button>
      </section>
    )

  const confirmed = medications.filter((medication) => medication.confirmed)
  const confirmedKeys = new Set(confirmed.map(displayKey))
  const unconfirmed = medications.filter(
    (medication) =>
      !medication.confirmed && !confirmedKeys.has(displayKey(medication)),
  )
  const currentExtractions = groupExact(
    unconfirmed.filter(
      (medication) =>
        appointmentId && medication.appointment_id === appointmentId,
    ),
  )
  const historicalExtractions = groupExact(
    unconfirmed.filter(
      (medication) =>
        !appointmentId || medication.appointment_id !== appointmentId,
    ),
  )
  const descriptions = new Map<string, Set<string>>()
  unconfirmed.forEach((medication) => {
    if (!medication.dosage) return
    const name = medication.display_name ?? medication.name
    descriptions.set(
      name,
      new Set([...(descriptions.get(name) ?? []), medication.dosage]),
    )
  })
  const varyingDescriptions = [...descriptions]
    .filter(([, doses]) => doses.size > 1)
    .map(([name]) => name)

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="mb-3 text-sm font-semibold">
        Medications &amp; supplements
      </h2>

      {varyingDescriptions.length > 0 && (
        <p
          className="border-warning/40 bg-warning/5 mb-3 rounded-md border p-2 text-sm"
          role="note"
        >
          Different extracted dose or schedule descriptions for{" "}
          {varyingDescriptions.join(", ")}. Review the dated sources before
          confirming the current regimen.
        </p>
      )}
      {currentExtractions.length > 0 && (
        <div className="mb-4">
          <h3 className="mb-1 text-sm font-medium">
            Extracted from this visit · needs confirmation
          </h3>
          <p className="text-muted-foreground mb-2 text-xs">
            Confirm adds to the patient’s confirmed medication list. The source
            visit note remains as recorded.
          </p>
          <ul className="space-y-2">
            {currentExtractions.map((group) => extractionRow(group, true))}
          </ul>
        </div>
      )}
      {historicalExtractions.length > 0 && (
        <details className="mb-4 rounded-md border p-3">
          <summary className="cursor-pointer text-sm font-medium">
            Historical and undated extraction suggestions ·{" "}
            {historicalExtractions.length}
          </summary>
          <p className="text-muted-foreground mt-2 text-xs">
            These source suggestions are read-only here. Confirm the current
            regimen with the patient and add it below when appropriate.
          </p>
          <ul className="mt-2 space-y-2">
            {historicalExtractions.map((group) => extractionRow(group, false))}
          </ul>
        </details>
      )}
      {confirmed.length > 0 && (
        <h3 className="mb-2 text-sm font-medium">
          Confirmed patient medication list
        </h3>
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
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => remove(m.id)}
                >
                  Remove
                </Button>
              </div>
              <div className="mt-1 ml-16">
                {m.source === "manual" ? (
                  <span className="text-muted-foreground text-xs">
                    Added manually to the patient list
                  </span>
                ) : (
                  sourceLink(m)
                )}
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
          aria-label="Add a medication or supplement to the patient list"
          disabled={busy}
          placeholder="Add a medication or supplement…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <Button size="sm" onClick={add} disabled={busy || !newName.trim()}>
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
