import { useEffect, useRef, useState } from "react"
import Markdown from "react-markdown"

import { ChevronDown } from "lucide-react"

import { BillingHeaderButton } from "@/components/notes/BillingHeaderButton"
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
  BillingHeaderSource,
  SessionPrepNoteReview,
} from "~/configuration/client"
import { usableBillingSource } from "~/lib/cab-billing-header"

import { SessionPrepNoteReviewButton } from "./SessionPrepNoteReviewButton"

export default function VisitNoteViewer({
  note,
  defaultOpen = false,
  review,
  transcript,
  focusOnMount = false,
  billingSource,
}: {
  note: AiVisitNote
  defaultOpen: boolean
  review?: SessionPrepNoteReview
  transcript?: AiVisitTranscript | null
  focusOnMount?: boolean
  billingSource?: BillingHeaderSource
}) {
  const [isOpen, setOpen] = useState(defaultOpen)
  const contentRef = useRef<HTMLDivElement>(null)
  const noteRef = useRef<HTMLDivElement>(null)
  const retainedBillingSource = useRef<BillingHeaderSource | null>(null)
  const billingAvailable = usableBillingSource(billingSource, note)
  if (billingAvailable && !retainedBillingSource.current)
    retainedBillingSource.current = billingSource
  const visibleBillingSource = billingAvailable
    ? billingSource
    : retainedBillingSource.current

  useEffect(() => {
    if (focusOnMount) noteRef.current?.scrollIntoView({ block: "start" })
  }, [focusOnMount, note.id])

  return (
    <Collapsible
      ref={noteRef}
      id={`note-${note.id}`}
      className="mb-5 scroll-mt-24"
      open={isOpen}
      onOpenChange={setOpen}
    >
      <div className="flex flex-wrap items-start gap-2">
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
        {isOpen && (
          <CopyButton
            htmlRef={contentRef}
            className="mt-1 ml-5"
            data-shortcut="copy-note"
            aria-keyshortcuts="c"
          />
        )}
        {visibleBillingSource && (
          <BillingHeaderButton
            noteId={note.id}
            source={visibleBillingSource}
            available={billingAvailable}
          />
        )}
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
