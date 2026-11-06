import { useEffect, useRef, useState } from "react"

import { deleteNoteTemplate } from "~/configuration/client"
import {
  type TemplateOperationContext,
  useTemplateOperationContext,
} from "~/hooks/use-template-operation-context"
import { templateOperation } from "~/lib/template-operation"

export function DeleteNoteTemplate({
  doctorId,
  schemaId,
  name,
  onDeleted,
}: {
  doctorId: string
  schemaId: string
  name: string
  onDeleted: () => void
}) {
  const context = useTemplateOperationContext(
    doctorId,
    JSON.stringify([schemaId, name]),
  )
  if (!context.available) return null
  return (
    <TemplateDeletion
      key={context.key}
      context={context}
      schemaId={schemaId}
      name={name}
      onDeleted={onDeleted}
    />
  )
}

function TemplateDeletion({
  context,
  schemaId,
  name,
  onDeleted,
}: {
  context: TemplateOperationContext
  schemaId: string
  name: string
  onDeleted: () => void
}) {
  const [phase, setPhase] = useState("idle")
  const mounted = useRef(false)
  const pending = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const remove = async () => {
    const isCurrent = () => mounted.current && context.isCurrent()
    if (pending.current || !isCurrent()) return
    pending.current = true
    setPhase("deleting")
    try {
      const result = await templateOperation(
        context.principal,
        isCurrent,
        (options) =>
          deleteNoteTemplate({ path: { schema_id: schemaId }, ...options }),
      )
      if (!isCurrent()) return
      if (result.response?.status !== 204) {
        setPhase("unknown")
        return
      }
      setPhase("done")
      onDeleted()
    } catch {
      if (isCurrent()) setPhase("unknown")
    } finally {
      if (isCurrent()) pending.current = false
    }
  }
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
