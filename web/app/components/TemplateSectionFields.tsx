import { useState } from "react"

import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import type { TemplateField } from "~/configuration/client"

export function TemplateSectionFields({
  fields,
  onChange,
}: {
  fields: TemplateField[]
  onChange: (fields: TemplateField[]) => void
}) {
  const [removed, setRemoved] = useState<{
    field: TemplateField
    index: number
  } | null>(null)
  const [status, setStatus] = useState("")

  function change(index: number, key: keyof TemplateField, value: string) {
    onChange(
      fields.map((field, i) =>
        i === index ? { ...field, [key]: value } : field,
      ),
    )
  }

  function move(index: number, offset: number) {
    const next = [...fields]
    const [field] = next.splice(index, 1)
    next.splice(index + offset, 0, field)
    onChange(next)
    setStatus(`Section moved to position ${index + offset + 1}.`)
  }

  return (
    <div className="space-y-3">
      {fields.map((field, index) => (
        <div key={index} className="space-y-2 rounded-md border p-3">
          <label className="block text-sm font-medium">
            Heading {index + 1}
            <Input
              value={field.heading}
              required
              maxLength={200}
              onChange={(event) => change(index, "heading", event.target.value)}
            />
          </label>
          <details>
            <summary className="cursor-pointer py-2 text-sm">
              Writing guidance {index + 1}
            </summary>
            <label className="block text-sm font-medium">
              Guidance {index + 1}
              <textarea
                className="w-full rounded-md border p-2"
                rows={3}
                value={field.guidance}
                maxLength={4000}
                onChange={(event) =>
                  change(index, "guidance", event.target.value)
                }
              />
            </label>
          </details>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={index === 0}
              aria-label={`Move section ${index + 1} up`}
              onClick={() => move(index, -1)}
            >
              Move up
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={index === fields.length - 1}
              aria-label={`Move section ${index + 1} down`}
              onClick={() => move(index, 1)}
            >
              Move down
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={fields.length === 1}
              aria-label={`Remove section ${index + 1}`}
              onClick={() => {
                setRemoved({ field, index })
                onChange(fields.filter((_, i) => i !== index))
                setStatus(
                  "Section removed from this template draft. Saved notes are unchanged.",
                )
              }}
            >
              Remove
            </Button>
          </div>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={fields.length >= 100}
          onClick={() => {
            onChange([...fields, { heading: "", guidance: "" }])
            setStatus(`Section ${fields.length + 1} added. Enter its heading.`)
          }}
        >
          Add section
        </Button>
        {removed && (
          <Button
            type="button"
            variant="outline"
            disabled={fields.length >= 100}
            onClick={() => {
              const next = [...fields]
              next.splice(
                Math.min(removed.index, next.length),
                0,
                removed.field,
              )
              onChange(next)
              setRemoved(null)
              setStatus("Removed section restored with its writing guidance.")
            }}
          >
            Undo removal
          </Button>
        )}
        <span className="text-muted-foreground text-sm">
          {fields.length} / 100 sections
        </span>
      </div>
      {status && (
        <p role="status" className="text-sm">
          {status}
        </p>
      )}
    </div>
  )
}
