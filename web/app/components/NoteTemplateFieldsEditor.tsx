import { useState } from "react"

import { TemplateSectionFields } from "~/components/TemplateSectionFields"
import { Button } from "~/components/ui/button"
import {
  type TemplateField,
  type TemplateFields,
  getNoteTemplateFields,
  updateNoteTemplateFields,
} from "~/configuration/client"

export function NoteTemplateFieldsEditor({
  schemaId,
  onSaved,
}: {
  schemaId: string
  onSaved: () => void
}) {
  const [baseline, setBaseline] = useState<TemplateFields | null>(null)
  const [fields, setFields] = useState<TemplateField[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [conflict, setConflict] = useState(false)
  const [saved, setSaved] = useState(false)

  async function load() {
    setBusy(true)
    setError("")
    try {
      const { data } = await getNoteTemplateFields({
        path: { schema_id: schemaId },
      })
      if (!data) throw new Error("load failed")
      setBaseline(data)
      setFields(data.fields)
      setConflict(false)
      setSaved(false)
    } catch {
      setError(
        "Could not load the template. Your edits have been kept. Try again.",
      )
    } finally {
      setBusy(false)
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!baseline) return
    setBusy(true)
    setError("")
    setSaved(false)
    try {
      const { data, response } = await updateNoteTemplateFields({
        path: { schema_id: schemaId },
        body: { expected_revision: baseline.revision, fields },
      })
      if (!data) {
        const stale = response?.status === 409
        setConflict(stale)
        setError(
          stale
            ? "This template changed elsewhere. Your edits are still here. Reloading replaces them with the saved template."
            : "Could not save. Use unique, nonblank headings (up to 200 characters) and guidance up to 4,000 characters per section. Your edits have been kept; try again.",
        )
        return
      }
      setBaseline(data)
      setFields(data.fields)
      setConflict(false)
      setSaved(true)
      onSaved()
    } catch {
      setError(
        "Save was not confirmed. Your edits have been kept; try saving again.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="not-prose mt-4 space-y-3">
      {!baseline ? (
        <Button type="button" variant="outline" disabled={busy} onClick={load}>
          {busy ? "Loading template…" : "Edit sections and guidance"}
        </Button>
      ) : (
        <form onSubmit={save} className="space-y-4">
          <p className="text-muted-foreground text-sm">
            These edits apply when creating future notes. Saved notes keep their
            headings and text. Use general headings such as “Labs”; put visit
            dates, findings and attestations in the note itself.
          </p>
          <fieldset disabled={busy} className="space-y-4">
            <TemplateSectionFields
              fields={fields}
              onChange={(next) => {
                setSaved(false)
                setFields(next)
              }}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                type="submit"
                disabled={
                  conflict ||
                  JSON.stringify(fields) === JSON.stringify(baseline.fields)
                }
              >
                {busy ? "Saving…" : "Save template"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setBaseline(null)
                  setError("")
                  setSaved(false)
                }}
              >
                {JSON.stringify(fields) === JSON.stringify(baseline.fields)
                  ? "Close editor"
                  : "Discard changes and close"}
              </Button>
              {conflict && (
                <Button type="button" variant="outline" onClick={load}>
                  Discard edits and reload
                </Button>
              )}
            </div>
          </fieldset>
        </form>
      )}
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      {saved && (
        <p role="status" className="text-sm">
          Template saved.
        </p>
      )}
    </div>
  )
}
