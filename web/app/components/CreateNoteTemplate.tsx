import { useState } from "react"

import { TemplateSectionFields } from "~/components/TemplateSectionFields"
import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import { type TemplateField, createNoteTemplate } from "~/configuration/client"

const soap: TemplateField[] = [
  {
    heading: "Subjective",
    guidance:
      "Reported symptoms and history. Retain uncertainty and who reported each fact.",
  },
  {
    heading: "Objective",
    guidance:
      "Only observations, examination findings and results supported by the source.",
  },
  {
    heading: "Assessment",
    guidance:
      "Only the assessment discussed in the source. Do not infer a diagnosis.",
  },
  {
    heading: "Plan",
    guidance:
      "Only the plan discussed in the source, including conditions, corrections and follow-up.",
  },
]

export function CreateNoteTemplate({ onCreated }: { onCreated: () => void }) {
  const [startingPoint, setStartingPoint] = useState("soap")
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState("")
  const [fields, setFields] = useState<TemplateField[]>([])
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [error, setError] = useState("")
  const [saved, setSaved] = useState("")

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError("")
    try {
      const { data, response } = await createNoteTemplate({
        body: { note_type: name, fields },
      })
      if (!data) {
        const rejected = response?.status === 409 || response?.status === 422
        setUncertain(!rejected)
        setError(
          response?.status === 409
            ? "That template name is already used. Choose a different name."
            : rejected
              ? "Use a name and unique, nonblank section headings. Your draft is retained."
              : "Creation was not confirmed. Check the same request before changing this draft.",
        )
        return
      }
      setSaved(data.note_type)
      setEditing(false)
      setUncertain(false)
      onCreated()
    } catch {
      setUncertain(true)
      setError(
        "Creation was not confirmed. Check the same request before changing this draft.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="not-prose space-y-3 rounded-lg border p-4">
      {!editing ? (
        <>
          <label className="block text-sm font-medium">
            Starting point
            <select
              className="mt-1 block w-full rounded-md border p-2"
              value={startingPoint}
              onChange={(event) => setStartingPoint(event.target.value)}
            >
              <option value="soap">SOAP · 4 sections</option>
              <option value="custom">Custom · start with 1 section</option>
            </select>
          </label>
          <Button
            type="button"
            onClick={() => {
              setName("")
              setSaved("")
              setError("")
              setUncertain(false)
              setFields(
                startingPoint === "soap"
                  ? soap.map((field) => ({ ...field }))
                  : [{ heading: "Visit note", guidance: "" }],
              )
              setEditing(true)
            }}
          >
            Create a template
          </Button>
        </>
      ) : (
        <form onSubmit={create} className="space-y-4">
          <p className="text-muted-foreground text-sm">
            Choose the sections for future notes. Existing notes and uploaded
            examples keep their original text.
          </p>
          <fieldset disabled={busy || uncertain} className="space-y-4">
            <label className="block text-sm font-medium">
              Template name
              <Input
                value={name}
                maxLength={160}
                required
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <TemplateSectionFields fields={fields} onChange={setFields} />
          </fieldset>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              {busy
                ? "Saving…"
                : uncertain
                  ? "Check template creation"
                  : "Save new template"}
            </Button>
            {!uncertain && (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setEditing(false)
                  setError("")
                }}
              >
                Discard draft and close
              </Button>
            )}
          </div>
        </form>
      )}
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      {saved && (
        <p role="status">
          Template “{saved}” created. It is available for future notes.
        </p>
      )}
    </div>
  )
}
