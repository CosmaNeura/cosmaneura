import { useEffect, useRef, useState } from "react"
import { useRevalidator } from "react-router"

import { Button } from "@/components/ui/button"

const CHECK_INTERVAL_MS = 3_000
// Covers the bounded note request plus recovery without requiring a page refresh.
const CHECK_WINDOW_MS = 300_000

/** Mounted only for a capture transcript with no current note or note editor. */
export function CaptureNoteReadiness() {
  const revalidator = useRevalidator()
  const current = useRef(revalidator)
  current.current = revalidator
  const checkNow = useRef<() => void>(() => {})
  const [deadline] = useState(() => Date.now() + CHECK_WINDOW_MS)
  const [expired, setExpired] = useState(false)

  useEffect(() => {
    let disposed = false
    let inFlight = false
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const clear = () => clearTimeout(timer)
    const schedule = () => {
      clear()
      if (disposed || stopped) return
      if (Date.now() >= deadline) {
        stopped = true
        setExpired(true)
      } else if (document.visibilityState !== "hidden") {
        timer = setTimeout(() => void check(), CHECK_INTERVAL_MS)
      }
    }
    const check = async (manual = false) => {
      clear()
      if (disposed || document.visibilityState === "hidden") return
      if (!manual && (stopped || Date.now() >= deadline)) {
        schedule()
        return
      }
      if (inFlight || current.current.state !== "idle") {
        schedule()
        return
      }
      inFlight = true
      try {
        // Reuse the fresh, bounded route reads and their existing access/error
        // boundaries. Revalidation never prepares or saves a note.
        await current.current.revalidate()
      } catch {
        // Router errors normally render the existing recovery boundary. If the
        // router itself cannot revalidate, leave one explicit read retry.
        stopped = true
        if (!disposed) setExpired(true)
      } finally {
        inFlight = false
        schedule()
      }
    }
    const onVisibility = () => {
      clear()
      if (document.visibilityState !== "hidden") void check()
    }
    const onFocus = () => void check()
    checkNow.current = () => void check(true)
    document.addEventListener("visibilitychange", onVisibility)
    window.addEventListener("focus", onFocus)
    schedule()
    return () => {
      disposed = true
      clear()
      checkNow.current = () => {}
      document.removeEventListener("visibilitychange", onVisibility)
      window.removeEventListener("focus", onFocus)
    }
  }, [deadline])

  const pending = revalidator.state !== "idle"
  return (
    <div className="mb-4">
      <h2 className="mb-2 text-xl font-semibold">Notes</h2>
      <p className="text-muted-foreground text-sm">
        Your transcript is ready. If you haven’t started the note, select
        Prepare draft note in Scribe.
      </p>
      <p className="mt-2 text-sm" role="status">
        {expired
          ? "A prepared note hasn’t appeared yet. You can check again."
          : "Checking for a prepared note…"}
      </p>
      <Button
        type="button"
        variant="outline"
        className="mt-3"
        disabled={pending}
        onClick={() => checkNow.current()}
      >
        {pending ? "Checking…" : "Check for note"}
      </Button>
    </div>
  )
}
