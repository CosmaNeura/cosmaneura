import { useEffect, useRef, useState } from "react"
import { useBlocker } from "react-router"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { BillingHeaderSource } from "~/configuration/client"
import {
  type CabAction,
  type CabCommand,
  type CabDraft,
  type CabRequest,
  type CabSource,
  type CabView,
  cabProviderExport,
  cabReviewSections,
  changeCabCodes,
  editableCabHeader,
  readCabResponse,
} from "~/lib/cab-provider-header"

import { CabVisitContext } from "./CabVisitContext"

const input = "border-input bg-background w-full rounded-md border p-2 text-sm"
type Command = { action: CabAction; body: CabCommand }

export function CabBillingHeaderPanel({
  operationId,
  expectedSource,
  request,
  onLocked,
  sourceStale = false,
  onUpdateVisit,
  asyncEnabled = false,
}: {
  operationId: string
  expectedSource: BillingHeaderSource["source"]
  request: CabRequest
  onLocked: (locked: boolean) => void
  sourceStale?: boolean
  asyncEnabled?: boolean
  onUpdateVisit?: (visit: CabSource["visitContext"]) => void
}) {
  const [source, setSource] = useState<CabSource | null>(null)
  const [view, setView] = useState<CabView | null>(null)
  const [draft, setDraft] = useState<CabDraft | null>(null)
  const [reason, setReason] = useState("")
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [generation, setGeneration] = useState<Command | null>(null)
  const [retry, setRetry] = useState<Command | null>(null)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const inFlight = useRef(false),
    mounted = useRef(true)
  const exactCommand = useRef<Command | null>(null)
  const current = useRef(true)
  current.current = !sourceStale
  const locked = busy || Boolean(retry) || Boolean(draft)
  const blocker = useBlocker(locked)

  useEffect(() => {
    mounted.current = true
    void run({ action: "accept", body: { expectedSource: null } })
    return () => {
      mounted.current = false
    }
    // The parent keys this component by immutable source operation.
  }, [])
  useEffect(() => {
    onLocked(locked)
    if (!locked) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [locked, onLocked])
  useEffect(() => () => onLocked(false), [onLocked])

  useEffect(() => {
    if (!sourceStale) return
    setStale(true)
    setRetry(null)
    setGeneration(null)
    setView(null)
    setError(
      "The source or billing confirmation changed. Your unsaved inputs are retained. Cancel edits and reload the note before continuing.",
    )
  }, [sourceStale])

  async function run(command: Command) {
    if (
      inFlight.current ||
      !current.current ||
      stale ||
      (exactCommand.current && exactCommand.current !== command)
    )
      return
    inFlight.current = true
    setBusy(true)
    setError("")
    setNotice("")
    let observingStatus = command.action === "generation-status"
    try {
      const raw = await request(command.action, command.body)
      if (!mounted.current || !current.current) return
      const received = await readCabResponse(
        raw,
        command.action,
        operationId,
        expectedSource,
        source?.headerSource,
        command.body.operationId,
      )
      if (!mounted.current || !current.current) return
      exactCommand.current = null
      setRetry(null)
      setSource(received.source)
      if (received.generation) {
        if (received.generation.status === "running") {
          const status = {
            action: "generation-status" as const,
            body: {
              encounterId: received.source.encounterId,
              expectedSource: received.source.headerSource,
              operationId: received.generation.operationId,
            },
          }
          setGeneration(status)
          observingStatus = true
          // Polling only observes the same job; every HTTP call keeps its short deadline.
          const until = Date.now() + 300_000
          while (Date.now() < until && mounted.current && current.current) {
            await new Promise((resolve) => setTimeout(resolve, 2_000))
            if (!mounted.current || !current.current) return
            const polled = await readCabResponse(
              await request(status.action, status.body),
              status.action,
              operationId,
              expectedSource,
              received.source.headerSource,
              status.body.operationId,
            )
            if (!mounted.current || !current.current) return
            setSource(polled.source)
            if (polled.generation?.status === "running") continue
            if (polled.view) setView(polled.view)
            if (polled.generation?.status === "succeeded") {
              setGeneration(null)
              setNotice("Header generated. Code proposals await coder review.")
            } else
              setError(
                polled.generation?.status === "failed"
                  ? "Generation failed. Resume status to check the same operation."
                  : "Generation was interrupted or its outcome is unknown. Resume status for this operation.",
              )
            setReady(true)
            return
          }
          if (mounted.current && current.current)
            setError(
              "Generation is still pending. Resume status to check this operation.",
            )
          return
        }
        if (received.generation.status !== "succeeded") {
          setGeneration({
            action: "generation-status",
            body: {
              encounterId: received.source.encounterId,
              expectedSource: received.source.headerSource,
              operationId: received.generation.operationId,
            },
          })
          setError(
            received.generation.status === "failed"
              ? "Generation failed. Resume status to check the same operation."
              : "Generation was interrupted or its outcome is unknown. Resume status for this operation.",
          )
          setReady(true)
          return
        }
        setGeneration(null)
      }
      if (command.action === "accept") {
        // Acceptance is acknowledged before a separate read. No model call on opening.
        const reading = {
          encounterId: received.source.encounterId,
          expectedSource: received.source.headerSource,
        }
        const result = await request("read", reading)
        if (!mounted.current || !current.current) return
        const loaded = await readCabResponse(
          result,
          "read",
          operationId,
          expectedSource,
          received.source.headerSource,
        )
        if (!mounted.current || !current.current) return
        setSource(loaded.source)
        setView(loaded.view)
      } else {
        setView(received.view)
        if (received.view && command.action === "read") setGeneration(null)
      }
      setReady(true)
      if (command.action === "save") {
        setDraft(null)
        setReason("")
        setNotice("Header edits saved for coder review.")
      }
      if (command.action === "generate")
        setNotice("Header generated. Code proposals await coder review.")
    } catch (failure) {
      if (!mounted.current || !current.current) return
      const status = (failure as { status?: number }).status
      const mutation =
        command.action !== "read" && command.action !== "generation-status"
      const statusFailure = observingStatus
      // Retain the exact operation through network loss or an unconfirmed receipt.
      const ambiguous = mutation && (!status || status >= 500 || status === 429)
      exactCommand.current = ambiguous && !statusFailure ? command : null
      setRetry(exactCommand.current)
      if ([401, 403, 404, 409].includes(status ?? 0)) {
        setStale(true)
        setGeneration(null)
        setView(null)
        setError(
          "The source, header, or your access changed. Your unsaved inputs are retained. Reload the note before continuing.",
        )
      } else if (statusFailure) {
        setError(
          "Status could not be confirmed. Resume status for the same operation.",
        )
      } else if (status === 422) {
        setError(
          "These edits could not be saved. Check codes, evidence and the required reason.",
        )
      } else
        setError(
          ambiguous
            ? "We couldn't confirm the result. Retry the same request before continuing."
            : "The header could not be loaded. Retry when the connection is available.",
        )
    } finally {
      inFlight.current = false
      if (mounted.current) setBusy(false)
    }
  }
  function act(
    action: "read" | "generate" | "save",
    extra: Partial<CabCommand> = {},
  ) {
    if (!source) return
    void run({
      action,
      body: {
        encounterId: source.encounterId,
        expectedSource: source.headerSource,
        ...(action === "read" ? {} : { operationId: crypto.randomUUID() }),
        ...(action === "generate" && asyncEnabled
          ? { responseMode: "async" as const }
          : {}),
        ...extra,
      },
    })
  }
  function editCode(index: number, patch: Partial<CabDraft["codes"][number]>) {
    setDraft(
      (current) =>
        current &&
        changeCabCodes(
          current,
          current.codes.map((row, i) =>
            i === index ? { ...row, ...patch } : row,
          ),
        ),
    )
  }
  function moveDiagnosis(id: string, target: number) {
    if (!draft) return
    const indices = draft.codes.flatMap((row, i) =>
      row.kind === "ICD10" ? [i] : [],
    )
    const rows = indices.map((i) => draft.codes[i])
    const from = rows.findIndex((row) => row.id === id)
    if (from < 0 || target < 0 || target >= rows.length) return
    rows.splice(target, 0, rows.splice(from, 1)[0])
    const codes = [...draft.codes]
    indices.forEach((index, i) => {
      codes[index] = rows[i]
    })
    setDraft(changeCabCodes(draft, codes))
  }
  const disabled = busy || Boolean(retry) || stale || sourceStale
  const display = view ? editableCabHeader(view) : null
  const h = view?.header
  const diagnoses = draft?.codes.filter((row) => row.kind === "ICD10") ?? []
  async function copy() {
    if (!view || !source) return
    try {
      await navigator.clipboard.writeText(cabProviderExport(view, source))
      setNotice("Header and source citations copied.")
    } catch {
      setError("Copy is unavailable. You can download the header instead.")
    }
  }
  function download() {
    if (!view || !source) return
    const url = URL.createObjectURL(
      new Blob([cabProviderExport(view, source)], {
        type: "text/plain;charset=utf-8",
      }),
    )
    const link = document.createElement("a")
    link.href = url
    link.download = "billing-header-revision-" + view.revisionNumber + ".txt"
    link.click()
    URL.revokeObjectURL(url)
    setNotice("Header and source citations downloaded.")
  }

  return (
    <section
      aria-label="Provider billing header"
      aria-busy={busy}
      className="space-y-4"
    >
      {blocker.state === "blocked" && (
        <div role="alert" className="bg-muted rounded-md p-3 text-sm">
          <p>
            {busy || retry
              ? "Resolve the pending request before leaving this note."
              : "You have unsaved header edits."}
          </p>
          <Button
            type="button"
            variant="outline"
            onClick={() => blocker.reset()}
          >
            Stay on this note
          </Button>
          {!busy && !retry && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => blocker.proceed()}
            >
              Discard edits and leave
            </Button>
          )}
        </div>
      )}
      {busy && (
        <p role="status" className="text-muted-foreground text-sm">
          Working on the billing header…
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
        >
          <p>{error}</p>
          {retry && (
            <Button
              type="button"
              variant="outline"
              className="mt-2"
              disabled={busy}
              onClick={() => void run(retry)}
            >
              Retry same request
            </Button>
          )}
          {generation && !stale && (
            <Button
              type="button"
              variant="outline"
              className="mt-2"
              disabled={busy}
              onClick={() => void run(generation)}
            >
              Resume status
            </Button>
          )}
          {!retry && !stale && (
            <Button
              type="button"
              variant="outline"
              className="mt-2"
              disabled={busy || Boolean(draft)}
              onClick={() =>
                source
                  ? act("read")
                  : void run({
                      action: "accept",
                      body: { expectedSource: null },
                    })
              }
            >
              Reload header
            </Button>
          )}
        </div>
      )}
      {notice && (
        <p role="status" className="text-sm text-emerald-800">
          {notice}
        </p>
      )}
      {source && (
        <div className="text-muted-foreground text-xs">
          Provider-confirmed completed note ·{" "}
          {source.projection.excludedRecordingTime
            ? "Recording-derived care time excluded"
            : "Original note preserved"}
        </div>
      )}
      {source && onUpdateVisit && (
        <CabVisitContext
          source={source}
          disabled={disabled || locked || !ready}
          onUpdate={onUpdateVisit}
        />
      )}
      {ready && !h && !stale && (
        <div className="space-y-2">
          <p className="text-sm">
            Generate code proposals and visit facts for a medical coder to
            review.
          </p>
          <Button
            type="button"
            disabled={
              disabled ||
              Boolean(generation) ||
              !source?.inferencePolicy.allowed
            }
            onClick={() => act("generate")}
          >
            Generate billing header
          </Button>
        </div>
      )}
      {source && !source.inferencePolicy.allowed && (
        <p role="status" className="bg-muted rounded-md p-3 text-sm">
          New generation is unavailable for this source. Any saved header
          remains available for review and editing.
        </p>
      )}
      {h && display && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-muted-foreground text-xs">
              Revision {view!.revisionNumber} ·{" "}
              {view!.signoff ? "Coder review recorded" : "Coder review pending"}
            </p>
            {!draft && (
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => {
                    setDraft(editableCabHeader(view!))
                    setNotice("")
                  }}
                >
                  Edit header
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => void copy()}
                >
                  Copy header
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={disabled}
                  onClick={download}
                >
                  Download
                </Button>
              </div>
            )}
          </div>
          {view!.previousSignoff && (
            <p className="text-sm text-amber-900">
              This revision needs a fresh coder review after the header edits.
            </p>
          )}
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {h.facts.map((fact) => (
              <div key={fact.key} className="bg-muted rounded-md p-3">
                <dt className="text-muted-foreground text-xs">{fact.label}</dt>
                <dd className="mt-1 text-sm whitespace-pre-wrap">
                  {display.facts[fact.key] || "Not documented / unspecified"}
                </dd>
                {display.facts[fact.key] !== (fact.value ?? "") && (
                  <p className="mt-1 text-xs text-violet-800">
                    Human edit · Original: {fact.value || "Not documented"}
                  </p>
                )}
                <Citations ids={fact.evidenceIds} view={view!} />
              </div>
            ))}
          </dl>
          <div>
            <h3 className="text-sm font-semibold">Code proposals</h3>
            <p className="text-muted-foreground text-xs">
              Saving a proposal does not accept a code or submit a claim.
            </p>
            {display.codes.length === 0 && (
              <p className="mt-2 text-sm">No code proposals.</p>
            )}
            <ul className="mt-2 space-y-2">
              {display.codes.map((row) => (
                <li key={row.id} className="rounded-md border p-3 text-sm">
                  <strong className="font-mono">{row.code}</strong> ·{" "}
                  {row.label}
                  {row.mapping && (
                    <p className="text-muted-foreground text-xs">
                      Human catalog mapping · requires coder review
                    </p>
                  )}
                  <Citations ids={row.evidenceIds} view={view!} />
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs">
              {display.diagnosisOrderConfirmed
                ? "Human proposed primary/order: " +
                  display.codes
                    .filter((row) => row.kind === "ICD10")
                    .map((row) => row.code)
                    .join(" → ") +
                  ". Separate coder acceptance required."
                : "Human primary diagnosis/order: unconfirmed."}
            </p>
          </div>
          {display.note && (
            <p className="rounded-md border p-3 text-sm whitespace-pre-wrap">
              {display.note}
            </p>
          )}
          {!!h.flags.length && (
            <ul className="list-disc space-y-2 rounded-md bg-amber-50 py-3 pr-3 pl-7 text-sm text-amber-950">
              {h.flags.map((flag) => (
                <li key={flag.id}>
                  {flag.message}
                  <Citations ids={flag.evidenceIds ?? []} view={view!} />
                </li>
              ))}
            </ul>
          )}
          <details className="text-sm">
            <summary className="cursor-pointer font-medium">
              Original engine suggestions
            </summary>
            <ul className="mt-2 space-y-2">
              {h.suggestions.map((row) => (
                <li key={row.id} className="rounded-md border p-3">
                  <strong>{row.code}</strong> · {row.label}
                  <p className="text-muted-foreground mt-1 text-xs">
                    {row.basis}
                  </p>
                  <Citations ids={row.evidenceIds} view={view!} />
                </li>
              ))}
            </ul>
          </details>
          <details className="text-sm">
            <summary className="cursor-pointer font-medium">
              Original coding evidence and held items
            </summary>
            <p className="text-muted-foreground mt-2 text-xs">
              These engine findings stay unchanged by header edits. An element
              level does not establish the entire E/M level.
            </p>
            {cabReviewSections(view!).map(({ label, review }) => (
              <section key={label} className="mt-3">
                <h4 className="font-medium">{label}</h4>
                <p className="text-muted-foreground text-xs">
                  {review.evidence_complete
                    ? "Element level: " + review.level
                    : "Evidence incomplete; summary unavailable."}
                </p>
                <ul className="mt-2 space-y-2">
                  {review.events.map((event) => (
                    <li
                      key={event.id}
                      className="rounded-md border p-3 text-sm"
                    >
                      <p className="text-xs font-semibold">
                        {event.status === "counted"
                          ? "Counted in this element"
                          : event.status === "held"
                            ? "Held · not counted"
                            : "Duplicate · not counted again"}
                      </p>
                      <p>{event.subject}</p>
                      <p className="mt-1">{event.reason}</p>
                      {!event.evidence_complete && (
                        <p className="text-xs text-amber-900">
                          Complete source evidence was not located.
                        </p>
                      )}
                      {event.evidence_spans.map((span, i) => (
                        <details key={i} className="mt-2 text-xs">
                          <summary className="cursor-pointer">
                            Source evidence
                          </summary>
                          <blockquote className="mt-1 border-l-2 pl-3 whitespace-pre-wrap">
                            {span.text}
                          </blockquote>
                        </details>
                      ))}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </details>
          <p className="text-muted-foreground text-xs">{h.policy.message}</p>
        </div>
      )}
      {draft && (
        <form
          className="space-y-4 border-t pt-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!disabled && view && reason.trim())
              act("save", {
                headerId: view.header.headerId,
                expectedRevision: view.revisionNumber,
                draft,
                reason,
              })
          }}
        >
          <h3 className="font-semibold">Edit billing header</h3>
          <p className="text-muted-foreground text-xs">
            Your changes remain separate from the clinical note and original
            engine suggestions.
          </p>
          <fieldset disabled={disabled} className="grid gap-3 sm:grid-cols-2">
            {Object.entries(draft.facts).map(([key, value]) => (
              <label key={key} className="space-y-1 text-xs">
                <span>
                  {h?.facts.find((fact) => fact.key === key)?.label ??
                    key.replaceAll("_", " ")}
                </span>
                <Input
                  maxLength={600}
                  value={value}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      facts: { ...draft.facts, [key]: event.target.value },
                    })
                  }
                />
              </label>
            ))}
          </fieldset>
          <fieldset disabled={disabled} className="space-y-3">
            <legend className="mb-2 text-sm font-medium">
              Code proposals for review
            </legend>
            {draft.codes.map((row, index) => (
              <div key={row.id} className="space-y-2 rounded-md border p-3">
                <div className="grid gap-2 sm:grid-cols-3">
                  <label className="text-xs">
                    Kind
                    <select
                      className={input}
                      value={row.kind}
                      disabled={!!row.mapping}
                      onChange={(event) =>
                        editCode(index, {
                          kind: event.target.value as "EM" | "ICD10",
                        })
                      }
                    >
                      <option>EM</option>
                      <option>ICD10</option>
                    </select>
                  </label>
                  <label className="text-xs">
                    Code
                    <Input
                      maxLength={15}
                      required
                      value={row.code}
                      readOnly={!!row.mapping}
                      onChange={(event) =>
                        editCode(index, {
                          code: event.target.value.toUpperCase(),
                        })
                      }
                    />
                  </label>
                  <label className="text-xs">
                    Short label
                    <Input
                      maxLength={150}
                      required
                      value={row.label}
                      readOnly={!!row.mapping}
                      onChange={(event) =>
                        editCode(index, { label: event.target.value })
                      }
                    />
                  </label>
                </div>
                {row.mapping && (
                  <p className="text-muted-foreground text-xs">
                    Catalog-linked proposal. A coder can change its mapping
                    during review.
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setDraft(
                        changeCabCodes(
                          draft,
                          draft.codes.filter((_, i) => i !== index),
                        ),
                      )
                    }
                  >
                    Remove proposal
                  </Button>
                  {row.kind === "ICD10" && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={diagnoses[0]?.id === row.id}
                      onClick={() => moveDiagnosis(row.id, 0)}
                    >
                      Make primary
                    </Button>
                  )}
                  {row.kind === "ICD10" && (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={diagnoses[0]?.id === row.id}
                      onClick={() =>
                        moveDiagnosis(
                          row.id,
                          diagnoses.findIndex((item) => item.id === row.id) - 1,
                        )
                      }
                    >
                      Move up
                    </Button>
                  )}
                </div>
                <details className="text-xs">
                  <summary className="cursor-pointer">
                    Source evidence ({row.evidenceIds.length} selected;
                    required)
                  </summary>
                  {h?.evidence.map((item) => (
                    <label
                      key={item.id}
                      className="mt-2 flex items-start gap-2"
                    >
                      <input
                        type="checkbox"
                        checked={row.evidenceIds.includes(item.id)}
                        disabled={!!row.mapping}
                        onChange={(event) =>
                          editCode(index, {
                            evidenceIds: event.target.checked
                              ? [...row.evidenceIds, item.id]
                              : row.evidenceIds.filter((id) => id !== item.id),
                          })
                        }
                      />
                      <span className="whitespace-pre-wrap">{item.text}</span>
                    </label>
                  ))}
                </details>
              </div>
            ))}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={draft.codes.length >= 20}
              onClick={() =>
                setDraft(
                  changeCabCodes(draft, [
                    ...draft.codes,
                    {
                      id: "human-" + crypto.randomUUID(),
                      kind: "ICD10",
                      code: "",
                      label: "",
                      evidenceIds: [],
                    },
                  ]),
                )
              }
            >
              Add code proposal
            </Button>
          </fieldset>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              disabled={disabled || !diagnoses.length}
              checked={draft.diagnosisOrderConfirmed === true}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  diagnosisOrderConfirmed: event.target.checked,
                })
              }
            />
            <span>
              I confirm {diagnoses[0]?.code || "the first diagnosis"} as my
              proposed primary and the displayed order for coder review.
            </span>
          </label>
          <label className="block space-y-1 text-xs">
            Provider/reviewer note (optional)
            <textarea
              className={input}
              disabled={disabled}
              maxLength={2000}
              value={draft.note}
              onChange={(event) =>
                setDraft({ ...draft, note: event.target.value })
              }
            />
          </label>
          <label className="block space-y-1 text-xs">
            Reason for edit
            <Input
              required
              disabled={disabled}
              maxLength={600}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={disabled || !reason.trim()}>
              Save header edits
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy || !!retry}
              onClick={() => {
                setDraft(null)
                setReason("")
              }}
            >
              Cancel edits
            </Button>
          </div>
        </form>
      )}
      {view?.revision && (
        <details className="border-t pt-3 text-xs">
          <summary className="cursor-pointer font-medium">
            Edit history ({view.revisionNumber})
          </summary>
          <ol className="mt-2 space-y-2">
            {[...view.revision.history].reverse().map((row) => (
              <li key={row.id} className="bg-muted rounded-md p-3">
                <p>
                  Revision {row.number} · {row.actor.name} ({row.actor.role}) ·{" "}
                  {new Date(row.createdAt).toLocaleString()}
                </p>
                <p className="mt-1">Reason: {row.reason}</p>
                <p className="text-muted-foreground mt-1">
                  Changed:{" "}
                  {row.changes.map((change) => change.label).join(", ")}
                </p>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  )
}

function Citations({ ids, view }: { ids: string[]; view: CabView }) {
  if (!ids.length) return null
  return (
    <details className="mt-2 text-xs">
      <summary className="text-muted-foreground cursor-pointer">
        Source evidence
      </summary>
      {ids.map((id) => {
        const row = view.header.evidence.find((item) => item.id === id)!
        return (
          <blockquote key={id} className="mt-2 border-l-2 pl-3">
            <p className="whitespace-pre-wrap">{row.text}</p>
            <p className="text-muted-foreground mt-1">
              Note version {row.sourceVersion} · characters {row.start}–
              {row.end}
            </p>
          </blockquote>
        )
      })}
    </details>
  )
}
