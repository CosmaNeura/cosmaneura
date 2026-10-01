import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type VisitPrepCommandStateResponse,
  type VisitPrepPackageResponse,
  type VisitPrepPackageTopicResponse,
  type VisitPrepSettingsData,
  visitPrepList,
  visitPrepPackage,
} from "~/configuration/client"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"

function appointment(prep: VisitPrepCommandStateResponse): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: prep.timezone_name,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(prep.scheduled_at))
  } catch {
    return "Appointment time unavailable"
  }
}

function words(topic: VisitPrepPackageTopicResponse, initial = false): string {
  const kind = initial ? topic.initial_response_kind : topic.response_kind
  const answer = initial ? topic.initial_answer : topic.answer
  if (kind === "skipped") return "Skipped — not supplied"
  if (kind === null) return "Not answered"
  if (topic.kind === "choice") {
    return (
      topic.choices.find((choice) => choice.value === answer)?.label ??
      "Answer unavailable"
    )
  }
  return answer ?? "Answer unavailable"
}

function PackageView({ value }: { value: VisitPrepPackageResponse }) {
  return (
    <article
      className="min-w-0 space-y-4"
      aria-labelledby="visit-prep-package-title"
    >
      <div>
        <h3 id="visit-prep-package-title" className="text-lg font-semibold">
          Patient contribution
        </h3>
        <p className="text-sm">
          {appointment(value.preparation)} · {value.preparation.timezone_name}
        </p>
        <p className="text-sm font-medium">
          {value.actor_role === "caregiver"
            ? "Caregiver report"
            : "Patient report"}{" "}
          ·{" "}
          {value.current_answers_complete
            ? "All applicable topics answered or skipped"
            : "Incomplete contribution"}
        </p>
        <p className="text-muted-foreground text-sm">
          {value.current_answers_reviewed
            ? "The recipient confirmed this version of their answers."
            : "The recipient has not confirmed this version of their answers."}{" "}
          This is not clinical verification or evidence that a provider reviewed
          it.
        </p>
      </div>
      <div className="rounded-md border p-3 text-sm">
        AI synthesis has not been generated. These are the recipient’s own
        responses, not chart facts. Skipped or missing information has not been
        filled in. This snapshot is from your last load; reload to check for
        changes.
      </div>
      {value.preparation.patient_id === null && (
        <p className="text-sm">
          Not linked to a patient chart. No chart was created for this
          preparation.
        </p>
      )}
      {value.original_interview_completed &&
        !value.current_answers_complete && (
          <p className="text-sm">
            The original interview was submitted, but revised answers now
            require another topic.
          </p>
        )}
      {value.topics.map((topic) => (
        <section
          key={topic.question_key}
          className="min-w-0 rounded-md border p-4"
        >
          <h4 className="font-medium break-words">{topic.prompt}</h4>
          <p className="mt-2 [overflow-wrap:anywhere] break-words whitespace-pre-wrap">
            {words(topic)}
          </p>
          {topic.changed && (
            <details className="mt-3 text-sm">
              <summary>
                Recipient revised this response — original wording
              </summary>
              <p className="mt-2 [overflow-wrap:anywhere] break-words whitespace-pre-wrap">
                {words(topic, true)}
              </p>
            </details>
          )}
          <details className="text-muted-foreground mt-3 text-xs">
            <summary>Response provenance</summary>
            <dl className="mt-2 space-y-1 break-all">
              <dt>Question</dt>
              <dd>{topic.question_key}</dd>
              <dt>Original response</dt>
              <dd>
                {topic.original_response_id ?? "No original interview response"}
              </dd>
              {topic.additional_answer_source_id && (
                <>
                  <dt>Newly relevant topic source</dt>
                  <dd>{topic.additional_answer_source_id}</dd>
                </>
              )}
              {topic.current_revision_id && (
                <>
                  <dt>Current revision</dt>
                  <dd>{topic.current_revision_id}</dd>
                </>
              )}
              <dt>Current response recorded</dt>
              <dd>{topic.recorded_at ?? "Not recorded"}</dd>
            </dl>
          </details>
        </section>
      ))}
      <details className="text-muted-foreground text-xs">
        <summary>Package version and provenance</summary>
        <dl className="mt-2 space-y-1 break-all">
          <dt>Preparation</dt>
          <dd>{value.package_id}</dd>
          <dt>Source version</dt>
          <dd>{value.source_version}</dd>
          <dt>Question version</dt>
          <dd>{value.preparation.question_set_version_id}</dd>
          <dt>Original responses / revision events</dt>
          <dd>
            {value.basis.original_response_count} / {value.basis.event_sequence}
          </dd>
        </dl>
      </details>
    </article>
  )
}

export function VisitPrepPackages({
  access,
  scopeKey,
}: {
  access: VisitPrepSettingsData
  scopeKey: string
}) {
  const allowed = access.eligible && access.can_manage && Boolean(scopeKey)
  const generation = useRef(0)
  const [scope, setScope] = useState(scopeKey)
  const [preps, setPreps] = useState<VisitPrepCommandStateResponse[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [value, setValue] = useState<VisitPrepPackageResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [blocked, setBlocked] = useState(false)

  useEffect(() => {
    function clear() {
      generation.current += 1
      setScope(scopeKey)
      setPreps([])
      setNext(null)
      setLoaded(false)
      setSelected(null)
      setValue(null)
      setBusy(false)
      setError(null)
    }
    clear()
    function block() {
      clear()
      setBlocked(true)
    }
    function conceal() {
      if (document.visibilityState === "hidden") clear()
    }
    window.addEventListener("blur", clear)
    window.addEventListener("pagehide", clear)
    document.addEventListener("visibilitychange", conceal)
    window.addEventListener(VIEW_AS_HARD_BLOCK_EVENT, block)
    const unsubscribe = subscribeToViewAsStateChanges(block)
    return () => {
      generation.current += 1
      window.removeEventListener("blur", clear)
      window.removeEventListener("pagehide", clear)
      document.removeEventListener("visibilitychange", conceal)
      window.removeEventListener(VIEW_AS_HARD_BLOCK_EVENT, block)
      unsubscribe()
    }
  }, [allowed, scopeKey])

  async function loadList(before?: string) {
    if (!allowed || blocked || busy) return
    const requestGeneration = ++generation.current
    setBusy(true)
    setValue(null)
    setSelected(null)
    setPreps([])
    setError(null)
    setLoaded(false)
    try {
      const result = await visitPrepList({
        query: { limit: 20, ...(before ? { before_id: before } : {}) },
        cache: "no-store",
      })
      if (generation.current !== requestGeneration) return
      if (!result.data || result.error) throw new Error("unavailable")
      setScope(scopeKey)
      setPreps(result.data.preparations)
      setNext(result.data.next_before_id)
      setLoaded(true)
    } catch {
      if (generation.current === requestGeneration)
        setError("Preparations could not be loaded. Reload to retry.")
    } finally {
      if (generation.current === requestGeneration) setBusy(false)
    }
  }

  async function loadPackage(id: string) {
    if (!allowed || blocked || busy) return
    const requestGeneration = ++generation.current
    setBusy(true)
    setValue(null)
    setSelected(id)
    setError(null)
    try {
      const result = await visitPrepPackage({
        path: { prep_id: id },
        cache: "no-store",
      })
      if (generation.current !== requestGeneration) return
      if (
        !result.data ||
        result.error ||
        result.data.package_id !== id ||
        result.data.preparation.id !== id ||
        result.data.contract !== "visit_prep.patient_sources.v1" ||
        !/^[0-9a-f]{64}$/.test(result.data.source_version)
      )
        throw new Error("unavailable")
      setScope(scopeKey)
      setValue(result.data)
    } catch {
      if (generation.current === requestGeneration)
        setError(
          "This contribution is unavailable or has changed. Reload to check its current state.",
        )
    } finally {
      if (generation.current === requestGeneration) setBusy(false)
    }
  }

  if (!allowed || blocked)
    return (
      <section className="mt-8">
        <h2 className="text-lg font-semibold">Review Visit Prep</h2>
        <p className="text-sm">
          Patient contributions are unavailable in this session. Exit support
          view or reload to recheck access.
        </p>
      </section>
    )
  const currentScope = scope === scopeKey
  return (
    <section
      className="mt-8 min-w-0 space-y-4"
      aria-labelledby="visit-prep-review-title"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="visit-prep-review-title" className="text-lg font-semibold">
          Review Visit Prep
        </h2>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void loadList()}
        >
          Load preparations
        </Button>
      </div>
      <p className="text-muted-foreground text-sm">
        Open a preparation to review patient-reported information before the
        visit. Answers are loaded only when you select one.
      </p>
      {busy && <p role="status">Loading Visit Prep…</p>}
      {error && <p role="alert">{error}</p>}
      {currentScope && loaded && preps.length === 0 && (
        <p>No preparations found.</p>
      )}
      {currentScope &&
        preps.map((prep) => (
          <div
            key={prep.id}
            className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-md border p-3"
          >
            <div className="min-w-0 text-sm">
              <p>
                {appointment(prep)} · {prep.timezone_name}
              </p>
              <p>
                {prep.revoked_at || prep.lifecycle_state === "misdirected"
                  ? "Revoked or quarantined"
                  : prep.lifecycle_state}
              </p>
              <p className="text-muted-foreground text-xs break-all">
                {prep.id}
              </p>
            </div>
            <Button
              variant="outline"
              disabled={
                busy ||
                prep.revoked_at !== null ||
                prep.lifecycle_state === "misdirected" ||
                prep.lifecycle_state === "invited"
              }
              onClick={() => void loadPackage(prep.id)}
              aria-label={`Open contribution ${prep.id}`}
            >
              Open contribution
            </Button>
          </div>
        ))}
      {currentScope && loaded && next && (
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void loadList(next)}
        >
          Next preparations
        </Button>
      )}
      {currentScope && selected && (
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void loadPackage(selected)}
        >
          Reload contribution
        </Button>
      )}
      {currentScope && value && <PackageView value={value} />}
    </section>
  )
}
