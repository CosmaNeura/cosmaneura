import VisitNoteViewer from "~/components/VisitNoteViewer"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import type { AIVisitNote, AIVisitTranscript } from "~/configuration/client"

import { FeedbackWidget } from "./FeedbackWidget"

export function EmptyNotes() {
  return (
    <div className="mb-4">
      <div className="mb-2 text-xl font-semibold">Notes</div>
      <div className="text-sm italic text-muted-foreground">
        No Notes Available. Upload some!
      </div>
    </div>
  )
}

export function MessageContent({
  notes,
  transcript,
  // showTemplates = true,
  // showStatus = false,
}: {
  notes: AIVisitNote[]
  transcript: AIVisitTranscript
}) {
  return (
    <div className="space-y-6">
      {/* TODO pretty sure I should remove all of this transition stuff... */}
      <div className={"transform transition-all duration-150 ease-in-out"}>
        <div className="rounded-lg border bg-card p-6 shadow-sm">
          {/* <MessageHeader
            mail={mail}
            // showStatus={showStatus}
          /> */}

          {notes.length ? (
            notes.map((note, index) => (
              <VisitNoteViewer note={note} defaultOpen={index === 0} />
            ))
          ) : (
            <EmptyNotes />
          )}

          <VisitTranscriptViewer transcript={transcript} />

          {/* <PatientDataSections className="mb-6" mail={currentMail} /> */}

          {/* {!showTemplates && (
            <MessageIntake
              mail={currentMail}
              isAccuityIntakeOpen={isAccuityIntakeOpen}
              setIsAccuityIntakeOpen={setIsAccuityIntakeOpen}
              isIntakeTranscriptOpen={isIntakeTranscriptOpen}
              setIsIntakeTranscriptOpen={setIsIntakeTranscriptOpen}
            />
          )} */}
        </div>
      </div>

      <div className="flex flex-col gap-4 sm:flex-row">
        <FeedbackWidget />
      </div>
    </div>
  )
}
