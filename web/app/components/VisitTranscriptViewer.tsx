import { useState } from "react"
import Markdown from "react-markdown"

import { ChevronDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { AIVisitTranscript } from "~/configuration/client"

export default function VisitTranscriptViewer({
  transcript,
}: {
  transcript: AIVisitTranscript
}) {
  if (!transcript) {
    return (
      <>
        <div className="mb-2 text-xl font-semibold">Visit Transcript</div>
        <div className="text-sm italic text-muted-foreground">
          No Transcript Available
        </div>
      </>
    )
  }

  const [isOpen, setOpen] = useState(false)

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
            <span className="ml-2 text-xl font-semibold">Visit Transcript</span>
          </Button>
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent className="mt-3">
        {transcript.transcript_data.map((transcriptEntry) => {
          return (
            <div className="mb-2">
              <span className="font-bold">{transcriptEntry.speaker}: </span>
              {transcriptEntry.content}
            </div>
          )
        })}
      </CollapsibleContent>
    </Collapsible>
  )
}
