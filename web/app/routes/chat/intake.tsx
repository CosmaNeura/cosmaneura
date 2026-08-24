import { useEffect, useState } from "react"
import { MetaFunction, useParams } from "react-router"

import type { Route } from "./+types/intake"

import { InterviewFrame } from "~/components/visit-prep/InterviewFrame"
import {
  type InterviewProgress,
  type InterviewQuestion,
  QuestionForm,
} from "~/components/visit-prep/QuestionForm"
import { requireEnv } from "~/utils/environment"

import { invariant } from "@epic-web/invariant"

const VITE_PYTHON_URL = requireEnv("VITE_PYTHON_URL")

/**
 * Visit Prep, the patient side (CosmaNeura#135).
 *
 * Unauthenticated: the opaque id in the path is the only credential, and the
 * API deliberately returns no identity -- no name, no physician, no visit date
 * -- so a forwarded link exposes the questions and nothing more.
 *
 * This route talks to the backend with plain `fetch` rather than the generated
 * client, which only covers the authenticated internal API. That was a
 * deliberate call before this rewrite and it still holds: one endpoint pair is
 * not worth a second OpenAPI pipeline.
 */
type InterviewResponse = {
  question: InterviewQuestion | null
  status: "processing" | "completed" | "in_progress"
  progress?: InterviewProgress | null
}

function questionEndpoint(intakeId: string) {
  return `${VITE_PYTHON_URL}/external/chat/intake/${intakeId}/question`
}

export const meta: MetaFunction = () => {
  return [
    { title: "Before your visit" },
    {
      name: "description",
      content: "A few questions from your care team before your visit.",
    },
    // A link that lands in an inbox should not end up in a search index.
    { name: "robots", content: "noindex, nofollow" },
  ]
}

export async function clientLoader(loaderArgs: Route.ClientLoaderArgs) {
  const intakeId = loaderArgs.params.id

  if (!intakeId) {
    throw new Response("This link is not valid", { status: 400 })
  }

  const response = await fetch(questionEndpoint(intakeId))

  if (!response.ok) {
    throw new Response("This link has expired", { status: response.status })
  }

  return (await response.json()) as InterviewResponse
}

function Done() {
  return (
    <InterviewFrame>
      <p className="text-xs font-semibold tracking-[0.22em] text-[var(--brand-gold)] uppercase">
        All set
      </p>
      <h1 className="mt-3 text-3xl leading-tight font-semibold md:text-4xl">
        Thank you — your answers are with your care team.
      </h1>
      <p className="mt-4 text-[var(--brand-muted-text)]">
        They will read this before you meet, so the visit can start on what you
        actually came for. There is nothing else you need to do.
      </p>
    </InterviewFrame>
  )
}

/**
 * Only reachable for legacy model-generated intakes, whose questions are
 * written by a background job shortly after the intake is created. Visit Prep
 * seeds its questions up front and never shows this.
 */
function Preparing() {
  const [waited, setWaited] = useState(0)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setWaited((count) => count + 1)
      window.location.reload()
    }, 10_000)
    return () => window.clearTimeout(timer)
  }, [waited])

  return (
    <InterviewFrame>
      <h1 className="text-2xl font-semibold">Getting your questions ready…</h1>
      <p className="mt-3 text-[var(--brand-muted-text)]">
        This page will refresh on its own in a moment.
      </p>
    </InterviewFrame>
  )
}

export default function VisitPrepInterview({
  loaderData,
}: Route.ComponentProps) {
  const { id } = useParams()
  invariant(id, "intake id is required")
  // Resolved once, outside `submit`, so the narrowed id does not have to
  // survive into a closure.
  const endpoint = questionEndpoint(id)

  const [state, setState] = useState<InterviewResponse>(loaderData)
  const [draft, setDraft] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(answer: string) {
    setSubmitting(true)
    setError(null)

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer }),
      })

      if (!response.ok) {
        // The draft stays in state on purpose. Losing a paragraph someone typed
        // on a phone with bad signal is the one failure this page must not have.
        setError("That did not save. Check your connection and try again.")
        return
      }

      const next = (await response.json()) as InterviewResponse
      setState(next)
      setDraft("")
    } catch {
      setError("That did not save. Check your connection and try again.")
    } finally {
      setSubmitting(false)
    }
  }

  if (state.status === "completed" || !state.question) return <Done />
  if (state.status === "processing") return <Preparing />

  return (
    <InterviewFrame>
      <QuestionForm
        question={state.question}
        progress={state.progress}
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={submit}
        submitting={submitting}
        error={error}
      />
    </InterviewFrame>
  )
}
