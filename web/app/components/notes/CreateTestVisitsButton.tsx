import { useRef, useState } from "react"
import { Link, useRevalidator } from "react-router"

import { Button } from "~/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "~/components/ui/dialog"
import {
  newTestVisitRequest,
  retainedTestVisitRequest,
} from "~/lib/retained-test-visit-request"
import {
  type TestVisitReceipt,
  type TestVisitRequest,
  createTestVisits,
} from "~/lib/staging-test-visits"

export function CreateTestVisitsButton() {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [result, setResult] = useState<TestVisitReceipt | null>(null)
  const retained = useRef<TestVisitRequest | null>(null)
  const revalidator = useRevalidator()

  async function create() {
    if (busy || result) return
    setBusy(true)
    setFailed(false)
    try {
      retained.current ??= retainedTestVisitRequest()
      setResult(await createTestVisits(retained.current))
      void revalidator.revalidate()
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          Create test visits
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invented recording test visits</DialogTitle>
          <DialogDescription>
            Two invented visits for testing recording and note saving in
            staging.
          </DialogDescription>
        </DialogHeader>
        <p>
          Create two empty visits for this account’s clearly labelled synthetic
          patient. They contain fixed invented data. Recording starts only when
          you open a visit in CosmaBot or Scribe and give consent.
        </p>
        {result ? (
          <div role="status">
            <p>
              {result.created
                ? "Two test visits created."
                : "The same two test visits were recovered."}
            </p>
            <ul className="mt-3 space-y-2">
              {result.visits.map((visit) => (
                <li key={visit.appointment_id}>
                  <Link
                    className="underline"
                    to={`/notes/${visit.appointment_id}`}
                    onClick={() => setOpen(false)}
                  >
                    {visit.appointment_name}
                  </Link>
                </li>
              ))}
            </ul>
            <Button
              className="mt-4"
              variant="outline"
              onClick={() => {
                newTestVisitRequest()
                retained.current = null
                setResult(null)
              }}
            >
              Start another test pair
            </Button>
          </div>
        ) : (
          <>
            {failed && (
              <p role="alert">
                Creation could not be confirmed. Retry checks the same request
                and retains any saved visits.
              </p>
            )}
            <Button disabled={busy} onClick={() => void create()}>
              {busy
                ? "Creating…"
                : failed
                  ? "Retry same request"
                  : "Create two visits"}
            </Button>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
