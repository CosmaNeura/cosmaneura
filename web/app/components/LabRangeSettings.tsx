import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  labRangeList,
  labRangeReset,
  labRangeUpsert,
} from "~/configuration/client"
import type { LabRangeData } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

type Draft = {
  standard_low: string
  standard_high: string
  optimal_low: string
  optimal_high: string
  source_url: string
}

function toDraft(r: LabRangeData): Draft {
  return {
    standard_low: r.standard_low?.toString() ?? "",
    standard_high: r.standard_high?.toString() ?? "",
    optimal_low: r.optimal_low?.toString() ?? "",
    optimal_high: r.optimal_high?.toString() ?? "",
    source_url: r.source_url ?? "",
  }
}

function num(v: string): number | null {
  const t = v.trim()
  if (t === "") return null
  const n = Number(t)
  return Number.isNaN(n) ? null : n
}

// Lets the provider tune the standard/optimal reference ranges to their own
// functional-medicine source. Platform defaults apply until overridden; reset
// reverts a marker to the default.
export function LabRangeSettings() {
  const [ranges, setRanges] = useState<LabRangeData[]>([])
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [loading, setLoading] = useState(true)

  async function refresh() {
    const { data } = await labRangeList()
    if (data) {
      setRanges(data)
      setDrafts(Object.fromEntries(data.map((r) => [r.biomarker, toDraft(r)])))
    }
    setLoading(false)
  }

  useEffect(() => {
    refresh()
  }, [])

  function setField(biomarker: string, field: keyof Draft, val: string) {
    setDrafts((prev) => ({
      ...prev,
      [biomarker]: { ...prev[biomarker], [field]: val },
    }))
  }

  async function save(biomarker: string) {
    const d = drafts[biomarker]
    const { error } = await labRangeUpsert({
      path: { biomarker },
      body: {
        standard_low: num(d.standard_low),
        standard_high: num(d.standard_high),
        optimal_low: num(d.optimal_low),
        optimal_high: num(d.optimal_high),
        source_url: d.source_url.trim() || null,
      },
    })
    if (error) {
      toast({ title: "Could not save range", variant: "destructive" })
      return
    }
    toast({ title: "Range saved" })
    await refresh()
  }

  async function reset(biomarker: string) {
    const { error } = await labRangeReset({ path: { biomarker } })
    if (error) {
      toast({ title: "Could not reset", variant: "destructive" })
      return
    }
    await refresh()
  }

  if (loading) {
    return (
      <section className="prose-headings prose w-1/2 space-y-4">
        <h3>Lab reference ranges</h3>
        <p className="text-muted-foreground text-sm">Loading…</p>
      </section>
    )
  }

  return (
    <section className="prose-headings prose w-full space-y-4">
      <h3>Lab reference ranges</h3>
      <p className="text-muted-foreground text-sm">
        Standard ranges are common reference intervals; optimal ranges are
        editable defaults — tune them to your functional-medicine source and add
        a link. Reset reverts a marker to the platform default.
      </p>
      <div className="space-y-2">
        {ranges.map((r) => {
          const d = drafts[r.biomarker]
          if (!d) return null
          return (
            <div
              key={r.biomarker}
              data-testid={`lab-range-${r.biomarker}`}
              className="flex flex-wrap items-center gap-2 border-b py-2 text-sm"
            >
              <span className="w-40 font-medium">
                {r.display_name}
                {r.is_override && (
                  <span className="text-muted-foreground text-xs">
                    {" "}
                    (custom)
                  </span>
                )}
              </span>
              {(
                [
                  ["standard_low", "std low"],
                  ["standard_high", "std high"],
                  ["optimal_low", "opt low"],
                  ["optimal_high", "opt high"],
                ] as [keyof Draft, string][]
              ).map(([field, label]) => (
                <input
                  key={field}
                  aria-label={`${r.display_name} ${label}`}
                  className="w-20 rounded-md border px-2 py-1"
                  placeholder={label}
                  value={d[field]}
                  onChange={(e) => setField(r.biomarker, field, e.target.value)}
                />
              ))}
              <input
                aria-label={`${r.display_name} source`}
                className="w-48 rounded-md border px-2 py-1"
                placeholder="source URL"
                value={d.source_url}
                onChange={(e) =>
                  setField(r.biomarker, "source_url", e.target.value)
                }
              />
              <Button size="sm" onClick={() => save(r.biomarker)}>
                Save
              </Button>
              {r.is_override && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => reset(r.biomarker)}
                >
                  Reset
                </Button>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
