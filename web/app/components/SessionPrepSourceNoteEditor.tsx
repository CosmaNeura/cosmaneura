import { useEffect, useRef, useState } from "react"
import { useRevalidator } from "react-router"

import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import { correctSessionPrepSourceNote } from "~/configuration/client"

type EditorProps = {
  noteId: string
  digest: string
  fields: Record<string, string>
  onEditingChange: (editing: boolean) => void
}

export function SessionPrepSourceNoteEditor(props: EditorProps) {
  return (
    <SourceNoteEditorSession
      key={`${props.noteId}:${props.digest}`}
      {...props}
    />
  )
}

function SourceNoteEditorSession({
  noteId,
  digest,
  fields,
  onEditingChange,
}: EditorProps) {
  const revalidator = useRevalidator()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(Object.entries(fields))
  const [pending, setPending] = useState(false)
  const [closed, setClosed] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState("")
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])
  useEffect(() => {
    onEditingChange(editing)
  }, [editing, onEditingChange])

  function open() {
    setDraft(Object.entries(fields))
    setError("")
    setClosed(false)
    setConfirmed(false)
    setEditing(true)
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (pending || closed) return
    if (
      !draft.length ||
      draft.some(([heading]) => !heading.trim()) ||
      new Set(draft.map(([heading]) => heading)).size !== draft.length
    ) {
      setError("Use unique, nonblank section headings.")
      return
    }
    setPending(true)
    setError("")
    try {
      const result = await correctSessionPrepSourceNote({
        path: { note_id: noteId },
        body: {
          expected_content_sha256: digest,
          fields: Object.fromEntries(draft),
        },
        throwOnError: false,
      })
      if (!active.current) return
      if (result.data?.note_id && result.data.content_sha256) {
        setClosed(true)
        setConfirmed(true)
        setError("Changes saved. Loading the corrected note for your review…")
        try {
          await revalidator.revalidate()
          if (
            active.current &&
            result.data.note_id === noteId &&
            result.data.content_sha256 === digest
          ) {
            setEditing(false)
          }
        } catch {
          if (active.current)
            setError(
              "Changes saved, but the visit could not refresh. Reload to open the saved version for review.",
            )
        }
      } else if (result.response?.status === 422) {
        setError(
          "Use unique headings and a nonblank note within 18,000 characters.",
        )
      } else {
        setClosed(true)
        setError(
          result.response?.status === 409 || result.response?.status === 404
            ? "This note or your access changed. Edits remain only in this open editor. Copy them before reloading to compare the current note."
            : "Save was not confirmed. Edits remain only in this open editor. Copy them before reloading to check before saving again.",
        )
      }
    } catch {
      if (!active.current) return
      setClosed(true)
      setError(
        "Save was not confirmed. Edits remain only in this open editor. Copy them before reloading to check before saving again.",
      )
    } finally {
      if (active.current) setPending(false)
    }
  }

  if (!editing) {
    return (
      <Button className="mt-4" variant="outline" size="sm" onClick={open}>
        Correct this note
      </Button>
    )
  }

  return (
    <form onSubmit={save} className="mt-4 space-y-4 rounded-lg border p-4">
      <h3 className="font-semibold">Correct this visit note</h3>
      <p className="text-muted-foreground text-sm">
        Check the recording and correct inaccurate text or dated headings.
        Saving keeps the earlier version and requires a new Session Prep review.
      </p>
      <fieldset disabled={pending} className="space-y-4">
        {draft.map(([heading, text], index) => (
          <div key={index} className="space-y-2">
            <label className="block text-sm font-medium">
              Section {index + 1} heading
              <Input
                value={heading}
                readOnly={closed}
                maxLength={200}
                onChange={(event) =>
                  setDraft((current) =>
                    current.map((row, i) =>
                      i === index ? [event.target.value, row[1]] : row,
                    ),
                  )
                }
              />
            </label>
            <label className="block text-sm font-medium">
              Section {index + 1} text
              <textarea
                className="mt-1 block min-h-24 w-full rounded-md border p-3 text-sm"
                value={text}
                readOnly={closed}
                rows={5}
                maxLength={18000}
                onChange={(event) =>
                  setDraft((current) =>
                    current.map((row, i) =>
                      i === index ? [row[0], event.target.value] : row,
                    ),
                  )
                }
              />
            </label>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={closed}
              onClick={() =>
                setDraft((current) => current.filter((_, i) => i !== index))
              }
            >
              Remove section {index + 1}
            </Button>
          </div>
        ))}
      </fieldset>
      {error && (
        <p role={confirmed ? "status" : "alert"} className="text-sm">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="submit"
          disabled={
            pending ||
            closed ||
            JSON.stringify(draft) === JSON.stringify(Object.entries(fields))
          }
        >
          {pending ? "Saving correction…" : "Save correction"}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={pending}
          onClick={() => {
            if (closed) window.location.reload()
            else {
              setEditing(false)
            }
          }}
        >
          {closed
            ? confirmed
              ? "Reload saved visit"
              : "Discard edits and reload visit"
            : "Discard edits"}
        </Button>
      </div>
    </form>
  )
}
