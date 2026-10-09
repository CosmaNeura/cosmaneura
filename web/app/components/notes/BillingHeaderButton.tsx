import { useEffect, useId, useRef, useState } from "react"

import { FileCheck2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  type BillingHeaderSource,
  type PrepareBillingHeaderOperation,
  prepareBillingHeaderOperation,
} from "~/configuration/client"
import {
  type BillingVisitDraft,
  emptyBillingVisit,
  prepareBillingVisit,
  readPreparedBillingOperation,
} from "~/lib/cab-billing-header"
import { sameCabValue } from "~/lib/cab-provider-header"
import { withReadDeadline } from "~/lib/read-deadline"

import { CabBillingHeaderConnection } from "./CabBillingHeaderConnection"

const inputClass =
  "border-input bg-background h-10 w-full rounded-md border px-3 text-sm"

export function BillingHeaderButton({
  noteId,
  source,
  available = true,
}: {
  noteId: string
  source: BillingHeaderSource
  available?: boolean
}) {
  const prefix = useId()
  const [open, setOpen] = useState(false)
  const [visit, setVisit] = useState(emptyBillingVisit)
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [preparedId, setPreparedId] = useState(
    source.latestOperation?.operationId ?? null,
  )
  const [updatingVisit, setUpdatingVisit] = useState(false)
  const [headerLocked, setHeaderLocked] = useState(false)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const command = useRef<PrepareBillingHeaderOperation | null>(null)
  const inFlight = useRef(false)
  const [binding] = useState(() => ({
    noteId,
    source: structuredClone(source.source),
  }))
  const latestSeen = useRef(source.latestOperation?.operationId ?? null)
  const externalStale = useRef(false)
  const latest = source.latestOperation?.operationId ?? null
  if (
    !available ||
    noteId !== binding.noteId ||
    !sameCabValue(source.source, binding.source)
  )
    externalStale.current = true
  if (latest !== latestSeen.current) {
    if (latest !== preparedId && latest !== command.current?.operationId)
      externalStale.current = true
    latestSeen.current = latest
  }
  const blocked = stale || externalStale.current
  const current = useRef(true)
  current.current = !blocked
  const locked = pending || (uncertain && !blocked) || headerLocked
  useEffect(() => {
    if (externalStale.current) setStale(true)
  }, [available, source])

  useEffect(() => {
    if (!locked) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [locked])

  function field(name: keyof BillingVisitDraft, value: string) {
    if (locked || blocked) return
    setVisit((current) => ({ ...current, [name]: value }))
    setConfirmed(false)
    setError(null)
  }

  async function submit() {
    if (inFlight.current || !current.current) return
    if (!command.current) {
      try {
        if (!confirmed)
          throw new Error(
            "Confirm that you reviewed this note and the visit details.",
          )
        command.current = {
          operationId: crypto.randomUUID(),
          expectedContentSha256: binding.source.contentSha256,
          expectedTextSha256: binding.source.textSha256,
          confirmedComplete: true,
          visit: prepareBillingVisit(visit),
        }
      } catch (failure) {
        setError(
          failure instanceof Error
            ? failure.message
            : "Check the visit details.",
        )
        return
      }
    }
    const exactCommand = command.current
    inFlight.current = true
    setPending(true)
    setError(null)
    try {
      // A deadline may occur after persistence: preserve the command for exact retry.
      const result = await withReadDeadline((signal) =>
        prepareBillingHeaderOperation({
          path: { note_id: binding.noteId },
          body: exactCommand,
          signal,
          throwOnError: false,
        }),
      )
      if (!current.current) return
      if (
        [400, 401, 403, 404, 409, 422].includes(result.response?.status ?? 0)
      ) {
        setStale(true)
        setUncertain(false)
        setError(
          "This note, your access, or the submitted details changed. Reload the note before continuing.",
        )
        return
      }
      if (!result.response?.ok) throw new Error("Unconfirmed response")
      readPreparedBillingOperation(result.data, exactCommand, binding.source)
      setPreparedId(exactCommand.operationId)
      setUpdatingVisit(false)
      setUncertain(false)
      command.current = null
    } catch {
      if (!current.current) return
      setUncertain(true)
      setError(
        "We couldn't confirm the save. Retry this same confirmation to check its result.",
      )
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!locked) setOpen(next)
      }}
    >
      <Button
        variant="outline"
        size="sm"
        className="ml-auto gap-2"
        onClick={() => setOpen(true)}
      >
        <FileCheck2 className="h-4 w-4" aria-hidden="true" />
        Billing header
      </Button>
      <DialogContent
        className={
          "max-h-[90dvh] w-[calc(100%-2rem)] overflow-y-auto " +
          (preparedId && !updatingVisit ? "max-w-3xl" : "max-w-xl")
        }
      >
        <DialogHeader>
          <DialogTitle>Billing header</DialogTitle>
          <DialogDescription>
            Confirm this note and its visit details for coding review. Your
            clinical note stays unchanged.
          </DialogDescription>
        </DialogHeader>

        {externalStale.current && (!preparedId || updatingVisit) && (
          <p
            role="alert"
            className="rounded-md border border-amber-300 p-3 text-sm"
          >
            This note, its billing confirmation, or your access changed. Your
            inputs are retained. Reload the note before continuing.
          </p>
        )}
        {preparedId && !updatingVisit ? (
          <CabBillingHeaderConnection
            key={preparedId}
            operationId={preparedId}
            source={binding.source}
            sourceStale={blocked}
            onLocked={setHeaderLocked}
            onUpdateVisit={(context) => {
              if (locked || blocked) return
              setVisit({
                patientAge: String(context.patientAge),
                patientSex: context.patientSex,
                encounterType: context.encounterType,
                placeOfService: context.placeOfService,
                providerSpecialty: context.providerSpecialty,
                serviceDate: context.serviceDate ?? "",
              })
              setConfirmed(false)
              setError(null)
              setUpdatingVisit(true)
            }}
          />
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            {updatingVisit && (
              <p className="bg-muted mb-4 rounded-md p-3 text-sm">
                Confirm the corrected details, then generate a new header. The
                previous header is retained in the record.
              </p>
            )}
            {source.source.excludedRecordingTime && (
              <p className="bg-muted mb-4 rounded-md p-3 text-sm">
                Recording-based care time will be excluded from the billing
                input. Review any clinician-documented time in the note.
              </p>
            )}
            <fieldset
              disabled={locked || blocked}
              className="grid grid-cols-1 gap-4 sm:grid-cols-2"
            >
              <legend className="sr-only">Visit details</legend>
              <label htmlFor={prefix + "-age"} className="space-y-1 text-sm">
                <span>Age at visit</span>
                <Input
                  id={prefix + "-age"}
                  inputMode="numeric"
                  value={visit.patientAge}
                  onChange={(event) => field("patientAge", event.target.value)}
                />
              </label>
              <label htmlFor={prefix + "-sex"} className="space-y-1 text-sm">
                <span>Documented sex</span>
                <select
                  id={prefix + "-sex"}
                  className={inputClass}
                  value={visit.patientSex}
                  onChange={(event) => field("patientSex", event.target.value)}
                >
                  <option value="">Select…</option>
                  <option value="F">Female</option>
                  <option value="M">Male</option>
                  <option value="other">Other</option>
                  <option value="unknown">Unknown</option>
                </select>
              </label>
              <label htmlFor={prefix + "-type"} className="space-y-1 text-sm">
                <span>Visit setting and patient type</span>
                <select
                  id={prefix + "-type"}
                  className={inputClass}
                  value={visit.encounterType}
                  onChange={(event) =>
                    field("encounterType", event.target.value)
                  }
                >
                  <option value="">Select…</option>
                  <option value="office_new">Office · New patient</option>
                  <option value="office_established">
                    Office · Established patient
                  </option>
                  <option value="telehealth_new">
                    Telehealth · New patient
                  </option>
                  <option value="telehealth_established">
                    Telehealth · Established patient
                  </option>
                </select>
              </label>
              <label htmlFor={prefix + "-pos"} className="space-y-1 text-sm">
                <span>Place of service</span>
                <select
                  id={prefix + "-pos"}
                  className={inputClass}
                  value={visit.placeOfService}
                  onChange={(event) =>
                    field("placeOfService", event.target.value)
                  }
                >
                  <option value="">Select…</option>
                  <option value="11">Office (11)</option>
                  <option value="10">Telehealth · Patient at home (10)</option>
                  <option value="02">
                    Telehealth · Patient elsewhere (02)
                  </option>
                </select>
              </label>
              <label
                htmlFor={prefix + "-specialty"}
                className="space-y-1 text-sm"
              >
                <span>Provider specialty</span>
                <Input
                  id={prefix + "-specialty"}
                  maxLength={100}
                  value={visit.providerSpecialty}
                  onChange={(event) =>
                    field("providerSpecialty", event.target.value)
                  }
                />
              </label>
              <label htmlFor={prefix + "-date"} className="space-y-1 text-sm">
                <span>
                  Service date{" "}
                  <span className="text-muted-foreground">(optional)</span>
                </span>
                <Input
                  id={prefix + "-date"}
                  type="date"
                  value={visit.serviceDate}
                  onChange={(event) => field("serviceDate", event.target.value)}
                />
              </label>
              <label className="flex items-start gap-2 border-t pt-4 text-sm sm:col-span-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                <span>
                  I reviewed this note and confirm these visit details for
                  billing analysis.
                </span>
              </label>
            </fieldset>
            {error && (
              <p role="alert" className="text-destructive mt-4 text-sm">
                {error}
              </p>
            )}
            <DialogFooter className="mt-5 gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={locked}
                onClick={() =>
                  updatingVisit ? setUpdatingVisit(false) : setOpen(false)
                }
              >
                {updatingVisit ? "Cancel visit changes" : "Close"}
              </Button>
              <Button
                type="submit"
                disabled={pending || blocked || (!confirmed && !uncertain)}
              >
                {pending
                  ? "Saving confirmation…"
                  : uncertain
                    ? "Retry same confirmation"
                    : "Confirm for billing"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
