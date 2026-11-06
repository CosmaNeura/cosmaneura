import { useEffect, useRef, useState } from "react"

import { useViewAsActive } from "~/components/ViewAsRouteGuard"
import { initializeNoteTemplates } from "~/configuration/client"

export function DefaultTemplateSetup({ onCreated }: { onCreated: () => void }) {
  const viewAsActive = useViewAsActive()
  const notify = useRef(onCreated)
  notify.current = onCreated
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState("pending")
  useEffect(() => {
    if (viewAsActive) return
    let active = true
    setStatus("pending")
    void initializeNoteTemplates({ body: {} }).then(
      (result) => {
        if (!active) return
        if (!result.data) {
          setStatus("unknown")
          return
        }
        setStatus(result.data.status)
        if (result.data.status === "available") notify.current()
      },
      () => {
        if (active) setStatus("unknown")
      },
    )
    return () => {
      active = false
    }
  }, [attempt, viewAsActive])
  if (viewAsActive) return null
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
