import { useState } from "react"

import { updateSettingsData } from "~/configuration/client"
import type { Terminology } from "~/configuration/client"
import { queryClient } from "~/configuration/tanstack-query"
import { toast } from "~/hooks/use-toast"
import { cn } from "~/lib/utils"
import { TERMINOLOGY_SETS } from "~/utils/terminology"

import { settingsDataOptions } from "client/@tanstack/react-query.gen"

// Persists the doctor's terminology choice. The settings POST requires the
// biography field (it always writes it), so the current biography is passed
// through unchanged.
export async function saveTerminology(
  terminology: Terminology,
  biography: string | null,
): Promise<boolean> {
  const { error } = await updateSettingsData({
    body: { biography, terminology },
  })

  if (error) {
    toast({
      title: "Could not update terminology",
      variant: "destructive",
    })
    return false
  }

  // useTerminology() readers pull from the settings query cache
  await queryClient.invalidateQueries({
    queryKey: settingsDataOptions().queryKey,
  })

  toast({
    title: "Terminology updated",
    description: "Role labels across the app now use your selection.",
  })
  return true
}

export function TerminologySettings({
  terminology,
  biography,
}: {
  terminology: Terminology
  biography: string | null
}) {
  const [value, setValue] = useState<Terminology>(terminology)
  const [saving, setSaving] = useState(false)

  async function handleChange(next: Terminology) {
    if (next === value) return
    const previous = value
    setValue(next)
    setSaving(true)

    const saved = await saveTerminology(next, biography)
    if (!saved) {
      setValue(previous)
    }

    setSaving(false)
  }

  return (
    <section className="prose-headings prose max-w-2xl space-y-4">
      <h3>Terminology</h3>
      <p className="text-muted-foreground text-sm">
        How Cosmas refers to you and the people you see — in notes and intake.
        You can change this at any time.
      </p>
      <div role="radiogroup" aria-label="Terminology" className="not-prose space-y-1">
        {(Object.keys(TERMINOLOGY_SETS) as Terminology[]).map((key) => {
          const labels = TERMINOLOGY_SETS[key]
          const selected = value === key
          return (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={saving}
              onClick={() => handleChange(key)}
              className={cn(
                "flex w-full min-h-11 items-start gap-3 rounded-lg px-3 py-3 text-left",
                selected ? "bg-muted" : "hover:bg-muted/60",
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                  selected ? "border-primary" : "border-muted-foreground/40",
                )}
                aria-hidden
              >
                {selected && (
                  <span className="bg-primary size-2 rounded-full" />
                )}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium">
                  {labels.displayName}
                </span>
                <span className="text-muted-foreground mt-0.5 block text-xs">
                  Notes and intake say {labels.provider} and {labels.patient}.
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
