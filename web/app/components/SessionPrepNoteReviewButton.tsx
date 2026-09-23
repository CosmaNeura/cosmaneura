import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  type SessionPrepNoteReview,
  reviewNoteForSessionPrep,
} from "~/configuration/client"

export function SessionPrepNoteReviewButton({
  noteId,
  review,
}: {
  noteId: string
  review: SessionPrepNoteReview
}) {
  const [reviewed, setReviewed] = useState(Boolean(review.reviewed_at))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function markReviewed() {
    if (pending || reviewed) return
    setPending(true)
    setError(null)
    try {
      const result = await reviewNoteForSessionPrep({
        path: { note_id: noteId },
        body: { expected_content_sha256: review.content_sha256 },
        throwOnError: false,
      })
      if (
        result.data?.reviewed_at &&
        result.data.content_sha256 === review.content_sha256
      ) {
        setReviewed(true)
      } else {
        setError(
          result.response?.status === 409 || result.response?.status === 404
            ? "This note or your account access changed. Reload the page before reviewing it again."
            : "The review could not be saved. Try again.",
        )
      }
    } catch {
      setError("The review could not be saved. Try again.")
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="mt-4 border-t pt-3">
      {reviewed ? (
        <p role="status" className="text-muted-foreground text-sm">
          Reviewed for Session Prep
        </p>
      ) : (
        <>
          <p className="text-muted-foreground mb-2 text-sm">
            After reviewing this note, make it available for future visit
            preparation.
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={pending}
            onClick={markReviewed}
          >
            {pending ? "Saving review…" : "Mark reviewed for Session Prep"}
          </Button>
        </>
      )}
      {error && (
        <p role="alert" className="text-destructive mt-2 text-sm">
          {error}
        </p>
      )}
    </div>
  )
}
