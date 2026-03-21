import { useRef, useState } from "react"
import Markdown from "react-markdown"

import { ChevronDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { CopyButton } from "~/components/shared/CopyButton"
import type { AiVisitNote } from "~/configuration/client"

export default function VisitNoteViewer({
  note,
  defaultOpen = false,
}: {
  note: AiVisitNote
  defaultOpen: boolean
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
        {isOpen && (
          <CopyButton
            htmlRef={contentRef}
            className="mt-1 ml-5"
            text={note.note.content}
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
      </CollapsibleContent>
    </Collapsible>
  )
}
