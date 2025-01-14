import { useState } from "react"
import Markdown from "react-markdown"

import { ChevronDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { AIVisitNote } from "~/configuration/client"

export default function VisitNoteViewer({
  note,
  defaultOpen = false,
}: {
  note: AIVisitNote
  defaultOpen: boolean
}) {
  const [isOpen, setOpen] = useState(defaultOpen)

  return (
    <Collapsible open={isOpen} onOpenChange={setOpen}>
      <div className="flex items-start">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="h-auto p-0">
            <ChevronDown
              className={`h-4 w-4 transition-transform ${
                isOpen ? "transform rotate-180" : ""
              }`}
            />
            {/* TODO once the note type is threaded through! */}
            {/* <span className="ml-2 text-xl font-semibold">{note.note_name}</span> */}
            <span className="ml-2 text-xl font-semibold">Example Type</span>
          </Button>
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent className="mt-3">
        {Object.entries(note.note).map(([sectionTitle, noteContent]) => {
          return (
            <div className="mb-2">
              <h3 className="text-md font-semibold">{sectionTitle}</h3>
              <Markdown>{noteContent}</Markdown>
            </div>
          )
        })}
      </CollapsibleContent>
    </Collapsible>
  )
}
