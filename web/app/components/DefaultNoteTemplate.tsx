import { useEffect, useId, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type TemplatePreference,
  getNoteTemplatePreference,
  updateNoteTemplatePreference,
} from "~/configuration/client"

export function DefaultNoteTemplate({
  schemas,
}: {
  schemas: { id: string; note_type: string }[]
}) {
  const selectId = useId()
  const [baseline, setBaseline] = useState<TemplatePreference | null>(null)
  const [selected, setSelected] = useState("")
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [error, setError] = useState("")
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let current = true
    void getNoteTemplatePreference()
      .then(({ data }) => {
        if (!current) return
        if (!data) throw new Error("Preference unavailable")
        setBaseline(data)
        setSelected(data.default_schema_ref ?? "")
      })
      .catch(() => {
        if (current)
          setError("Could not load your default. Reload it to make a change.")
      })
    return () => {
      current = false
    }
  }, [])

  async function reload() {
    setBusy(true)
    setError("")
    try {
      const { data } = await getNoteTemplatePreference()
      if (!data) throw new Error("Preference unavailable")
      setBaseline(data)
      setSelected(data.default_schema_ref ?? "")
      setConflict(false)
      setUncertain(false)
      setSaved(false)
    } catch {
      setError("Could not load your default. Your current choice is retained.")
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
      const { data, response } = await updateNoteTemplatePreference({
        body: {
          expected_revision: baseline.revision,
          default_schema_ref: selected || null,
        },
      })
      if (!data) {
        const stale = response?.status === 409
        const rejected =
          stale || response?.status === 404 || response?.status === 422
        setConflict(stale)
        setUncertain(!rejected)
        setError(
          stale
            ? "Your default changed elsewhere. Your choice is retained; reload before changing it."
            : rejected
              ? "This choice could not be saved. Select an available template or reload settings."
              : "Saving was not confirmed. Check the same choice before changing it.",
        )
        return
      }
      setBaseline(data)
      setSelected(data.default_schema_ref ?? "")
      setUncertain(false)
      setConflict(false)
      setSaved(true)
    } catch {
      setUncertain(true)
      setError(
        "Saving was not confirmed. Check the same choice before changing it.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={save} className="not-prose space-y-3 rounded-lg border p-4">
      <label htmlFor={selectId} className="block text-sm font-medium">
        Default Scribe template
      </label>
      <select
        id={selectId}
        className="mt-1 block min-h-11 w-full rounded-md border p-2"
        value={selected}
        disabled={!baseline || busy || uncertain || conflict}
        onChange={(event) => {
          setSelected(event.target.value)
          setSaved(false)
        }}
      >
        <option value="">Choose each time</option>
        {schemas.map((schema) => (
          <option key={schema.id} value={schema.id}>
            {schema.note_type}
          </option>
        ))}
      </select>
      <p className="text-muted-foreground text-sm">
        The starting choice for new Scribe notes. You can choose another
        template each time. Saved notes are unchanged.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="submit"
          className="min-h-11"
          disabled={
            !baseline ||
            busy ||
            conflict ||
            (!uncertain && selected === (baseline.default_schema_ref ?? ""))
          }
        >
          {busy ? "Saving…" : uncertain ? "Check default save" : "Save default"}
        </Button>
        {(!baseline || conflict) && (
          <Button
            type="button"
            className="min-h-11"
            variant="outline"
            disabled={busy}
            onClick={reload}
          >
            {conflict ? "Discard choice and reload" : "Reload default"}
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      {saved && (
        <p role="status">
          {selected
            ? "Default template saved."
            : "Default cleared. Choose a template for each new note."}
        </p>
      )}
    </form>
  )
}
