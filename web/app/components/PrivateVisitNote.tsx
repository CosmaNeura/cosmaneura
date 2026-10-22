import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import { useBlocker } from "react-router"

import { Button } from "~/components/ui/button"
import {
  scribePrivateNoteRead,
  scribePrivateNoteSave,
} from "~/configuration/client"
import { withReadDeadline } from "~/lib/read-deadline"

type Status =
  | "loading"
  | "saved"
  | "unsaved"
  | "saving"
  | "unavailable"
  | "sign-in-required"
  | "access-unavailable"
  | "request-rejected"
  | "conflict"

function failureStatus(code?: number): Status {
  if (code === 401) return "sign-in-required"
  if (code === 403 || code === 404) return "access-unavailable"
  if (code === 400 || code === 422) return "request-rejected"
  return "unavailable"
}

/** Private reminders never enter clinical note fields, export, or browser storage. */
export function PrivateVisitNote({ appointmentId }: { appointmentId: string }) {
  const [text, setText] = useState("")
  const [saved, setSaved] = useState("")
  const [status, setStatus] = useState<Status>("loading")
  const active = useRef(false)
  const generation = useRef(0)
  const loaded = useRef(false)
  const current = useRef("")
  const version = useRef(0)
  const inFlight = useRef(false)
  const retained = useRef<{
    change_key: string
    expected_version: number
    text: string
  } | null>(null)
  const blocker = useBlocker(text !== saved || retained.current !== null)

  const load = useCallback(async () => {
    const reading = generation.current
    setStatus("loading")
    try {
      const result = await withReadDeadline((signal) =>
        scribePrivateNoteRead({
          path: { appointment_id: appointmentId },
          signal,
          throwOnError: false,
        }),
      )
      if (!active.current || generation.current !== reading) return
      if (!result.data) {
        setStatus(failureStatus(result.response?.status))
        return
      }
      current.current = result.data.text
      loaded.current = true
      version.current = result.data.version
      retained.current = null
      setText(result.data.text)
      setSaved(result.data.text)
      setStatus("saved")
    } catch {
      if (active.current && generation.current === reading)
        setStatus("unavailable")
    }
  }, [appointmentId])

  useLayoutEffect(() => {
    generation.current += 1
    active.current = true
    loaded.current = false
    current.current = ""
    version.current = 0
    retained.current = null
    inFlight.current = false
    setText("")
    setSaved("")
    void load()
    return () => {
      active.current = false
      generation.current += 1
    }
  }, [load])

  const save = useCallback(async () => {
    if (inFlight.current || !active.current || !loaded.current) return false
    const writing = generation.current
    const pending = retained.current ?? {
      change_key: crypto.randomUUID(),
      expected_version: version.current,
      text: current.current,
    }
    retained.current = pending
    inFlight.current = true
    setStatus("saving")
    try {
      // A transport timeout leaves the retained exact key/body unresolved.
      // Retrying is safe even if the server committed before the response.
      const result = await withReadDeadline((signal) =>
        scribePrivateNoteSave({
          path: { appointment_id: appointmentId },
          body: pending,
          signal,
          throwOnError: false,
        }),
      )
      if (!active.current || generation.current !== writing) return false
      if (!result.data) {
        setStatus(
          result.response?.status === 409
            ? "conflict"
            : failureStatus(result.response?.status),
        )
        return false
      }
      version.current = result.data.version
      retained.current = null
      setSaved(result.data.text)
      // A later cross-device edit may be returned by an old exact-key retry.
      // Keep the local input and require review instead of overwriting it.
      if (result.data.text !== pending.text) {
        setStatus("conflict")
        return false
      }
      const clean = current.current === pending.text
      setStatus(clean ? "saved" : "unsaved")
      return clean
    } catch {
      if (active.current && generation.current === writing)
        setStatus("unavailable")
      return false
    } finally {
      if (generation.current === writing) inFlight.current = false
    }
  }, [appointmentId])

  useEffect(() => {
    if (status !== "unsaved") return
    const timer = setTimeout(() => void save(), 750)
    return () => clearTimeout(timer)
  }, [text, status, save])

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (current.current !== saved || retained.current !== null) {
        event.preventDefault()
        event.returnValue = ""
      }
    }
    window.addEventListener("beforeunload", guard)
    return () => window.removeEventListener("beforeunload", guard)
  }, [saved])

  return (
    <section
      className="bg-muted/20 my-6 rounded-lg border p-4"
      data-ph-no-capture=""
      data-sentry-block=""
      aria-labelledby="private-note-title"
    >
      <h2 id="private-note-title" className="text-lg font-semibold">
        Jot a note to yourself
      </h2>
      <p id="private-note-help" className="text-muted-foreground mb-3 text-sm">
        Only you can see this. It syncs with Scribe and stays out of the
        clinical note and exports.
      </p>
      <textarea
        aria-label="Private note"
        aria-describedby="private-note-help"
        maxLength={2000}
        rows={3}
        className="bg-background w-full resize-y rounded-md border p-3 text-base"
        value={text}
        disabled={
          status === "loading" ||
          status === "sign-in-required" ||
          status === "access-unavailable" ||
          status === "request-rejected" ||
          !loaded.current
        }
        onChange={(event) => {
          current.current = event.target.value
          setText(event.target.value)
          if (
            status !== "saving" &&
            status !== "conflict" &&
            retained.current === null
          )
            setStatus(event.target.value === saved ? "saved" : "unsaved")
        }}
      />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <p role="status" className="text-sm">
          {
            {
              loading: "Loading…",
              saved: "Saved",
              unsaved: "Unsaved changes",
              saving: "Saving…",
              unavailable:
                "We couldn’t load or save your private note. Try again. Any unsaved changes are kept on this page.",
              "sign-in-required":
                "Your sign-in session could not be verified. Sign in again, then check access. Any unsaved changes are kept on this page.",
              "access-unavailable":
                "Private notes are unavailable for this visit in the current account. Check the account and visit before checking access again. Any unsaved changes are kept on this page.",
              "request-rejected":
                "This private-note request could not be accepted. Keep this page open to retain any unsaved changes.",
              conflict:
                "Your private note changed on another device. Review the saved version before saving again.",
            }[status]
          }
        </p>
        {(status === "unavailable" ||
          status === "sign-in-required" ||
          status === "access-unavailable") && (
          <Button
            variant="outline"
            onClick={() =>
              void (text === saved && !retained.current ? load() : save())
            }
          >
            {status === "unavailable" ? "Try again" : "Check access again"}
          </Button>
        )}
        {(status === "conflict" || status === "request-rejected") && (
          <Button
            variant="outline"
            onClick={() => {
              if (
                window.confirm(
                  "Reload the saved private note? Your unsaved changes here will be replaced.",
                )
              )
                void load()
            }}
          >
            Reload saved note
          </Button>
        )}
      </div>
      {blocker.state === "blocked" && (
        <div
          role="alertdialog"
          aria-label="Save your private note"
          className="mt-3 rounded-md border p-3"
        >
          <p>Your private note has unsaved changes.</p>
          <div className="mt-2 flex gap-2">
            <Button
              disabled={
                status === "loading" ||
                status === "saving" ||
                status === "conflict" ||
                status === "sign-in-required" ||
                status === "access-unavailable" ||
                status === "request-rejected"
              }
              onClick={() =>
                void save().then((clean) => {
                  if (clean) blocker.proceed()
                })
              }
            >
              Save and continue
            </Button>
            <Button variant="outline" onClick={() => blocker.reset()}>
              Keep editing
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}
