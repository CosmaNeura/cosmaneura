import { useLayoutEffect, useState } from "react"
import { useParams } from "react-router"

import { retainedSessionPrepCitation } from "~/configuration/client"
import { withReadDeadline } from "~/lib/read-deadline"
import { getViewAsStatus } from "~/lib/view-as-api"
import { subscribeToViewAsStateChanges } from "~/lib/view-as-binding"
import {
  type RetainedCitation,
  citationCoordinates,
  parseRetainedCitation,
} from "~/sovereign/RetainedCitation"

export default function ReviewedCitationPage() {
  const { citation_ref: reference } = useParams()
  const [display, setDisplay] = useState<{
    reference: string
    data: RetainedCitation
  } | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useLayoutEffect(() => {
    let current = true
    const controller = new AbortController()
    setDisplay(null)
    setUnavailable(false)
    const invalidate = () => {
      current = false
      controller.abort()
      setDisplay(null)
      setUnavailable(true)
    }
    const unsubscribe = subscribeToViewAsStateChanges(invalidate)
    const onFocus = () => {
      invalidate()
      setRefresh((value) => value + 1)
    }
    const onVisibility = () => {
      if (document.hidden) invalidate()
      else onFocus()
    }
    window.addEventListener("focus", onFocus)
    document.addEventListener("visibilitychange", onVisibility)
    void withReadDeadline(
      async (signal) => {
        if (!reference) throw new Error()
        const path = citationCoordinates(reference)
        const status = await getViewAsStatus(signal)
        signal.throwIfAborted()
        if (status.active || !current) throw new Error()
        const result = await retainedSessionPrepCitation({
          path,
          signal,
          throwOnError: false,
          cache: "no-store",
        })
        signal.throwIfAborted()
        if (!result.data) throw new Error()
        const currentStatus = await getViewAsStatus(signal)
        signal.throwIfAborted()
        if (currentStatus.active || !current) throw new Error()
        const data = await parseRetainedCitation(result.data, reference)
        signal.throwIfAborted()
        if (current) setDisplay({ reference, data })
      },
      { signal: controller.signal },
    ).catch(() => {
      if (current) setUnavailable(true)
    })
    return () => {
      current = false
      controller.abort()
      unsubscribe()
      window.removeEventListener("focus", onFocus)
      document.removeEventListener("visibilitychange", onVisibility)
    }
  }, [reference, refresh])
  const data = display && display.reference === reference ? display.data : null
  return (
    <main className="mx-auto max-w-4xl p-6">
      <h1 className="text-xl font-semibold">
        Reviewed source retained with this answer
      </h1>
      <p className="mt-2">
        This is the exact reviewed version cited in the accepted answer. Later
        corrections appear in current visit notes.
      </p>
      {unavailable ? (
        <p role="status" className="mt-4">
          This reviewed source is unavailable for your current account or has
          expired.
        </p>
      ) : !data ? (
        <p role="status" className="mt-4">
          Loading reviewed source…
        </p>
      ) : (
        <>
          <p className="mt-4">Visit · {data.citation.source_at}</p>
          <p>Reviewed · {data.citation.reviewed_at}</p>
          {data.sections.map((section) =>
            section.title === data.citation.section ? (
              <section key={section.title} className="mt-6">
                <h2 className="font-semibold">{section.title}</h2>
                <pre className="mt-2 font-sans whitespace-pre-wrap">
                  {section.text}
                </pre>
              </section>
            ) : (
              <details key={section.title} className="mt-4">
                <summary>{section.title}</summary>
                <pre className="mt-2 font-sans whitespace-pre-wrap">
                  {section.text}
                </pre>
              </details>
            ),
          )}
        </>
      )}
    </main>
  )
}
