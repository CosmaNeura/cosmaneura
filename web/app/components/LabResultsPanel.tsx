import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  labEvaluations,
  labPanelCreate,
  labPanelDelete,
  labPanelList,
} from "~/configuration/client"
import type { LabEvaluationEntry, LabPanelData } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

const CLASSIFICATION: Record<string, { label: string; className: string }> = {
  optimal: { label: "Optimal", className: "bg-success/10 text-success" },
  within_standard: {
    label: "In standard range, outside optimal",
    className: "bg-warning/10 text-warning",
  },
  outside_standard: {
    label: "Outside standard range",
    className: "bg-destructive/10 text-destructive",
  },
  no_range: {
    label: "No reference range",
    className: "bg-muted text-muted-foreground",
  },
}

function rangeText(low: number | null, high: number | null): string {
  if (low !== null && high !== null) return `${low}–${high}`
  if (high !== null) return `< ${high}`
  if (low !== null) return `> ${low}`
  return "—"
}

interface PendingRow {
  name: string
  value: string
  unit: string
}

const EMPTY_ROW: PendingRow = { name: "", value: "", unit: "" }

// Patient lab results: view prior panels and enter a new set by hand. Range
// interpretation (standard vs functional-optimal) is layered on separately.
export function LabResultsPanel({ patientId }: { patientId: string }) {
  const [panels, setPanels] = useState<LabPanelData[]>([])
  const [evaluations, setEvaluations] = useState<LabEvaluationEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [collectedOn, setCollectedOn] = useState("")
  const [rows, setRows] = useState<PendingRow[]>([{ ...EMPTY_ROW }])

  async function refresh() {
    const [list, evals] = await Promise.all([
      labPanelList({ path: { patient_id: patientId } }),
      labEvaluations({ path: { patient_id: patientId } }),
    ])
    if (list.data) setPanels(list.data)
    if (evals.data) setEvaluations(evals.data.entries)
    setLoading(false)
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patientId])

  function updateRow(i: number, field: keyof PendingRow, val: string) {
    setRows((prev) =>
      prev.map((r, idx) => (idx === i ? { ...r, [field]: val } : r)),
    )
  }

  async function save() {
    const results = rows
      .filter((r) => r.name.trim() && r.value.trim() !== "")
      .map((r) => ({
        name: r.name.trim(),
        value: Number(r.value),
        unit: r.unit.trim() || null,
      }))
      .filter((r) => !Number.isNaN(r.value))

    if (results.length === 0) {
      toast({
        title: "Add at least one result with a number",
        variant: "destructive",
      })
      return
    }

    const { error } = await labPanelCreate({
      path: { patient_id: patientId },
      body: { collected_on: collectedOn || null, results },
    })
    if (error) {
      toast({ title: "Could not save labs", variant: "destructive" })
      return
    }
    setAdding(false)
    setRows([{ ...EMPTY_ROW }])
    setCollectedOn("")
    await refresh()
  }

  async function remove(id: string) {
    const { error } = await labPanelDelete({ path: { lab_panel_id: id } })
    if (error) {
      toast({ title: "Could not delete", variant: "destructive" })
      return
    }
    await refresh()
  }

  if (loading) {
    return (
      <section className="mt-6 rounded-lg border p-4">
        <h2 className="text-sm font-semibold">Labs</h2>
        <p className="text-muted-foreground mt-2 text-sm">Loading…</p>
      </section>
    )
  }

  return (
    <section className="mt-6 rounded-lg border p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Labs</h2>
        {!adding && (
          <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
            Add results
          </Button>
        )}
      </div>

      {panels.length === 0 && !adding && (
        <p className="text-muted-foreground text-sm">No labs recorded yet.</p>
      )}

      {evaluations.length > 0 && (
        <div className="mb-4" data-testid="lab-interpretation">
          <h3 className="text-muted-foreground mb-1 text-xs font-medium uppercase">
            Interpretation (latest per marker)
          </h3>
          <ul className="space-y-1">
            {evaluations.map((e) => {
              const meta =
                CLASSIFICATION[e.classification] ?? CLASSIFICATION.no_range
              return (
                <li key={e.biomarker} className="text-sm">
                  <span className="font-medium">{e.display_name}</span>{" "}
                  <span>
                    {e.value}
                    {e.unit ? ` ${e.unit}` : ""}
                  </span>{" "}
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs ${meta.className}`}
                  >
                    {meta.label}
                  </span>
                  {e.classification !== "no_range" && (
                    <span className="text-muted-foreground ml-2 text-xs">
                      standard {rangeText(e.standard_low, e.standard_high)} ·
                      optimal {rangeText(e.optimal_low, e.optimal_high)}
                      {e.source_url && (
                        <>
                          {" "}
                          <a
                            href={e.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 hover:underline"
                          >
                            source ↗
                          </a>
                        </>
                      )}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <ul className="space-y-3">
        {panels.map((panel) => (
          <li key={panel.id} data-testid="lab-panel">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">
                {panel.collected_on ?? "Date unknown"}
              </span>
              <span className="text-muted-foreground">· {panel.source}</span>
              {panel.status === "pending" && (
                <span className="text-muted-foreground italic">
                  · extracting…
                </span>
              )}
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto"
                onClick={() => remove(panel.id)}
              >
                Delete
              </Button>
            </div>
            <ul className="mt-1 space-y-0.5">
              {panel.results.map((r) => (
                <li key={r.id} className="text-sm">
                  <span className="font-medium">{r.raw_name}</span>{" "}
                  <span>
                    {r.value}
                    {r.unit ? ` ${r.unit}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

      {adding && (
        <div className="mt-3 space-y-2 border-t pt-3">
          <label className="text-xs">
            Collected on{" "}
            <input
              type="date"
              value={collectedOn}
              onChange={(e) => setCollectedOn(e.target.value)}
              className="rounded-md border px-2 py-1 text-sm"
            />
          </label>
          {rows.map((row, i) => (
            <div key={i} className="flex gap-2">
              <input
                className="flex-1 rounded-md border px-2 py-1 text-sm"
                placeholder="Biomarker (e.g. Vitamin D)"
                value={row.name}
                onChange={(e) => updateRow(i, "name", e.target.value)}
              />
              <input
                className="w-24 rounded-md border px-2 py-1 text-sm"
                placeholder="Value"
                value={row.value}
                onChange={(e) => updateRow(i, "value", e.target.value)}
              />
              <input
                className="w-24 rounded-md border px-2 py-1 text-sm"
                placeholder="Unit"
                value={row.unit}
                onChange={(e) => updateRow(i, "unit", e.target.value)}
              />
            </div>
          ))}
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRows((prev) => [...prev, { ...EMPTY_ROW }])}
            >
              Add row
            </Button>
            <Button size="sm" onClick={save}>
              Save labs
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}
