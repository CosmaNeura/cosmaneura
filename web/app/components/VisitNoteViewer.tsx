import { useRef, useState } from "react"
import Markdown from "react-markdown"

import { ChevronDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import { CopyButton } from "~/components/shared/CopyButton"
import type {
  AiVisitNote,
  AiVisitTranscript,
  SessionPrepNoteReview,
} from "~/configuration/client"

import { SessionPrepNoteReviewButton } from "./SessionPrepNoteReviewButton"

export default function VisitNoteViewer({
  note,
  defaultOpen = false,
  review,
  transcript,
}: {
  note: AiVisitNote
  defaultOpen: boolean
  review?: SessionPrepNoteReview
  transcript?: AiVisitTranscript | null
}) {
  const [isOpen, setOpen] = useState(defaultOpen)
  const contentRef = useRef<HTMLDivElement>(null)

  return (
    <Collapsible className="mb-5" open={isOpen} onOpenChange={setOpen}>
      <div className="flex items-start">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="h-auto p-2">
            <ChevronDown
              className={`h-4 w-4 transition-transform ${
                isOpen ? "rotate-180 transform" : ""
              }`}
            />
            <span className="text-xl font-semibold">{note.note_type}</span>
          </Button>
        </CollapsibleTrigger>
        {isOpen && <CopyButton htmlRef={contentRef} className="mt-1 ml-5" />}
      </div>

      <CollapsibleContent className="mt-3">
        <div ref={contentRef}>
          {Object.entries(note.note).map(([sectionTitle, noteContent]) => (
            <div className="mb-2" key={sectionTitle}>
              <h3 className="text-md font-semibold">{sectionTitle}</h3>
              <div className="prose-headings prose max-w-full">
                <Markdown>{noteContent as string}</Markdown>
              </div>
            </div>
          ))}
        </div>
        {review && (
          <SessionPrepNoteReviewButton
            key={`${note.id}:${review.content_sha256}`}
            noteId={note.id}
            review={review}
          />
        )}
        {transcript !== undefined && (
          <div className="mt-4 border-t pt-4">
            <VisitTranscriptViewer transcript={transcript} />
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}
