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
import { cn } from "@/lib/utils"
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

// Build clipboard HTML from the rendered section rather than the surrounding
// markup, so per-section copy controls never end up in a pasted note.
function sectionHtml(title: string, content: HTMLElement | null | undefined) {
  const heading = document.createElement("h3")
  heading.textContent = title
  return heading.outerHTML + (content?.innerHTML ?? "")
}

// Hover-capable pointers reveal the control on hover or focus; touch devices
// have no hover, so the control stays visible there.
const REVEAL_ON_HOVER =
  "[@media(hover:hover)]:opacity-0 group-hover:opacity-100 focus-visible:opacity-100"

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
  const sectionRefs = useRef(new Map<string, HTMLDivElement | null>())
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
            getHtml={() =>
              Object.keys(note.note)
                .map((title) =>
                  sectionHtml(title, sectionRefs.current.get(title)),
                )
                .join("")
            }
            className="mt-1 ml-5"
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
        <div>
          {Object.entries(note.note).map(([sectionTitle, noteContent]) => (
            <div className="group mb-2" key={sectionTitle}>
              <div className="flex items-center gap-1">
                <h3 className="text-md font-semibold">{sectionTitle}</h3>
                <CopyButton
                  iconOnly
                  variant="ghost"
                  label={`Copy ${sectionTitle}`}
                  getHtml={() =>
                    sectionHtml(
                      sectionTitle,
                      sectionRefs.current.get(sectionTitle),
                    )
                  }
                  className={cn("-my-1 transition-opacity", REVEAL_ON_HOVER)}
                />
              </div>
              <div
                className="prose-headings prose max-w-full"
                ref={(element) => {
                  sectionRefs.current.set(sectionTitle, element)
                }}
              >
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
