import { useState } from "react"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select"
import { updateSettingsData } from "~/configuration/client"
import type { Terminology } from "~/configuration/client"
import { queryClient } from "~/configuration/tanstack-query"
import { toast } from "~/hooks/use-toast"
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
    <section className="prose-headings prose w-full max-w-2xl space-y-4">
      <h2 className="!mt-0 text-lg font-semibold">Terminology</h2>
      <p className="text-muted-foreground text-sm">
        Choose how the app refers to you and the people you care for. This
        changes labels across the app and the wording of generated notes and
        intake questions.
      </p>
      <Select
        value={value}
        onValueChange={(next) => handleChange(next as Terminology)}
        disabled={saving}
      >
        <SelectTrigger className="w-72" aria-label="Terminology">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(TERMINOLOGY_SETS).map(([key, labels]) => (
            <SelectItem key={key} value={key}>
              {labels.displayName}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </section>
  )
}
