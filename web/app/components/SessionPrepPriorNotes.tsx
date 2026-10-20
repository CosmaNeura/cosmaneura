import { useRevalidator } from "react-router"

import { Button } from "@/components/ui/button"
import type {
  PriorNoteOverview,
  PriorNotePreview,
} from "~/configuration/client"
import { appointmentDateFormat } from "~/lib/date"

const labels: Record<PriorNotePreview["status"], string> = {
  included: "Included in the next preparation",
  reviewed: "Reviewed; not in the current selection",
  needs_review: "Needs your review",
  unavailable: "Unavailable for Session Prep",
}

export function SessionPrepPriorNotes({
  overview,
  onOpenSessionPrep,
  preparationState,
}: {
  overview: PriorNoteOverview
  onOpenSessionPrep?: () => void
  preparationState?: string | null
}) {
  const revalidator = useRevalidator()
  return (
    <section
      aria-labelledby="prior-notes-title"
      className="mb-6 rounded-lg border p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="prior-notes-title" className="text-lg font-semibold">
          Prior visits for Session Prep
        </h2>
        <div className="flex flex-wrap gap-2">
          {onOpenSessionPrep && (
            <Button
              size="sm"
              onClick={onOpenSessionPrep}
              disabled={revalidator.state !== "idle"}
            >
              {preparationState === "ready" ||
              preparationState === "update_needed"
                ? "Update Session Prep"
                : "Open Session Prep"}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={revalidator.state !== "idle"}
            onClick={() => void revalidator.revalidate()}
          >
            {revalidator.state === "idle"
              ? "Refresh prior notes"
              : "Refreshing…"}
          </Button>
        </div>
      </div>
      {preparationState === "update_needed" && (
        <p className="mt-2 text-sm font-medium" role="status">
          Reviewed prior notes changed. Update Session Prep to use the latest
          selection.
        </p>
      )}
      <p className="mt-2 text-sm" role="status">
        {overview.included_count === 0
          ? "No reviewed prior notes are available for this visit yet."
          : `${overview.included_count} reviewed ${overview.included_count === 1 ? "note is" : "notes are"} available for this visit.`}
      </p>
      <p className="text-muted-foreground mt-1 text-sm">
        Session Prep uses up to five reviewed notes from prior visits in the
        last 18 months. Open a note, review it, then return here and refresh
        before opening or updating Session Prep.
      </p>
      {overview.notes.length ? (
        <ul className="mt-3 divide-y">
          {overview.notes.map((note) => (
            <li
              key={note.note_id}
              className="flex flex-wrap items-center justify-between gap-2 py-3"
            >
              <div>
                <p className="text-sm font-medium">
                  {note.note_type} ·{" "}
                  {appointmentDateFormat(note.appointment_date)}
                </p>
                <p className="text-muted-foreground text-sm">
                  {labels[note.status]}
                </p>
              </div>
              <a
                className="text-primary text-sm underline underline-offset-4"
                href={`/notes/${encodeURIComponent(note.appointment_id)}?reviewNote=${encodeURIComponent(note.note_id)}#note-${encodeURIComponent(note.note_id)}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open note{" "}
                <span className="sr-only">
                  {note.note_type} ·{" "}
                  {appointmentDateFormat(note.appointment_date)} in a new tab
                </span>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground mt-3 text-sm">
          No prior visit notes were found in this period.
        </p>
      )}
      {overview.more_notes && (
        <p className="text-muted-foreground mt-2 text-sm">
          Showing the 20 most recent notes plus any older notes included in
          preparation.
        </p>
      )}
      {overview.selection_limited && (
        <p className="text-muted-foreground mt-2 text-sm">
          Additional reviewed notes are outside the current preparation
          selection.
        </p>
      )}
    </section>
  )
}
