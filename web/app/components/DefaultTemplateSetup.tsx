import { useEffect, useRef, useState } from "react"

import { initializeNoteTemplates } from "~/configuration/client"
import {
  type TemplateOperationContext,
  useTemplateOperationContext,
} from "~/hooks/use-template-operation-context"
import { templateOperation } from "~/lib/template-operation"

export function DefaultTemplateSetup({
  doctorId,
  onCreated,
}: {
  doctorId: string
  onCreated: () => void
}) {
  const context = useTemplateOperationContext(doctorId)
  if (!context.available) return null
  return (
    <TemplateSetup key={context.key} context={context} onCreated={onCreated} />
  )
}

function TemplateSetup({
  context,
  onCreated,
}: {
  context: TemplateOperationContext
  onCreated: () => void
}) {
  const notify = useRef(onCreated)
  notify.current = onCreated
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState("pending")
  useEffect(() => {
    let active = true
    const isCurrent = () => active && context.isCurrent()
    setStatus("pending")
    void templateOperation(context.principal, isCurrent, (options) =>
      initializeNoteTemplates({ body: {}, ...options }),
    ).then(
      (result) => {
        if (!isCurrent()) return
        if (!result.data) {
          setStatus("unknown")
          return
        }
        setStatus(result.data.status)
        if (result.data.status === "available") notify.current()
      },
      () => {
        if (isCurrent()) setStatus("unknown")
      },
    )
    return () => {
      active = false
    }
  }, [attempt, context.key])
  if (status === "available") return null
  if (status === "pending")
    return <p role="status">Setting up your SOAP template…</p>
  if (status === "deleted")
    return (
      <p>
        Your supplied SOAP template was deleted. Create a template or upload
        your own when you need one.
      </p>
    )
  if (status === "capacity")
    return (
      <p>
        Your template limit has been reached. Your existing templates remain
        available.
      </p>
    )
  return (
    <div role="alert">
      <p>Template setup was not confirmed.</p>
      <button type="button" onClick={() => setAttempt((value) => value + 1)}>
        Check template setup
      </button>
    </div>
  )
}
