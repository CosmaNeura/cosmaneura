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
import { AIVisitNote } from "~/configuration/client"

import ReactDOMServer from "react-dom/server"

export default function VisitNoteViewer({
  note,
  defaultOpen = false,
}: {
  note: AIVisitNote
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
                isOpen ? "transform rotate-180" : ""
              }`}
            />
            <span className="text-xl font-semibold">{note.note_type}</span>
          </Button>
        </CollapsibleTrigger>
        {isOpen && (
          <CopyButton
            htmlRef={contentRef}
            className="ml-5 mt-1"
            text={note.note.content}
          />
        )}
      </div>

      <CollapsibleContent className="mt-3">
        <div ref={contentRef}>
          {Object.entries(note.note).map(([sectionTitle, noteContent]) => (
            <div className="mb-2">
              <h3 className="text-md font-semibold">{sectionTitle}</h3>
              <Markdown>{noteContent as string}</Markdown>
            </div>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
