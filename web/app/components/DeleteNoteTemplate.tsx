import { useState } from "react"

import { useViewAsActive } from "~/components/ViewAsRouteGuard"
import { deleteNoteTemplate } from "~/configuration/client"

export function DeleteNoteTemplate({
  schemaId,
  name,
  onDeleted,
}: {
  schemaId: string
  name: string
  onDeleted: () => void
}) {
  const viewAsActive = useViewAsActive()
  const [phase, setPhase] = useState("idle")
  const remove = async () => {
    setPhase("deleting")
    try {
      const result = await deleteNoteTemplate({ path: { schema_id: schemaId } })
      if (result.response?.status !== 204) {
        setPhase("unknown")
        return
      }
      setPhase("done")
      onDeleted()
    } catch {
      setPhase("unknown")
    }
  }
  if (viewAsActive) return null
  if (phase === "done") return <p role="status">Template deleted.</p>
  if (phase === "idle")
    return (
      <button type="button" onClick={() => setPhase("confirm")}>
        Delete template
      </button>
    )
  return (
    <div>
      <p>Delete “{name}”? Saved notes remain available.</p>
      {phase === "unknown" && (
        <p role="alert">
          Deletion was not confirmed. Check the same deletion before taking
          another action.
        </p>
      )}
      <button
        type="button"
        disabled={phase === "deleting"}
        onClick={() => {
          void remove()
        }}
      >
        {phase === "deleting"
          ? "Deleting…"
          : phase === "unknown"
            ? "Check deletion"
            : "Confirm deletion"}
      </button>
      {phase === "confirm" && (
        <button type="button" onClick={() => setPhase("idle")}>
          Keep template
        </button>
      )}
    </div>
  )
}
