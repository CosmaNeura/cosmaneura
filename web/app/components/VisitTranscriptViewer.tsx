import { formatDuration, intervalToDuration } from "date-fns"
import { useState } from "react"
import Markdown from "react-markdown"

import { ChevronDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
// TODO https://github.com/hey-api/openapi-ts/issues/1571
import type { AIVisitTranscript } from "~/configuration/client"
import { secondsToMinutes } from "~/lib/date"

export default function VisitTranscriptViewer({
  transcript,
}: {
  transcript: AIVisitTranscript
}) {
  if (!transcript) {
    return (
      <>
        <div className="mb-2 text-xl font-semibold">Visit Transcript</div>
        <div className="text-muted-foreground text-sm italic">
          No Transcript Available
        </div>
      </>
    )
  }

  // hook required for chevron state
  const [isOpen, setOpen] = useState(false)

  return (
    <Collapsible defaultOpen open={isOpen} onOpenChange={setOpen}>
      <div className="flex items-start">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="h-auto p-2">
            <ChevronDown
              className={`h-4 w-4 transition-transform ${
                isOpen ? "rotate-180 transform" : ""
              }`}
            />
            <span className="text-xl font-semibold">Visit Transcript</span>
          </Button>
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent className="mt-3">
        <div className="text-muted-foreground mb-2 text-sm italic">
          Duration: {secondsToMinutes(transcript.duration)}
        </div>
        <div className="text-muted-foreground mb-2 text-sm italic">
          Transcripts are automatically deleted after a couple weeks.
        </div>
        {transcript.transcript_data.map((transcriptEntry) => {
          return (
            <div className="mb-2" key={transcriptEntry.startTime}>
              <span className="font-bold">{transcriptEntry.speaker}: </span>
              {transcriptEntry.content}
            </div>
          )
        })}
      </CollapsibleContent>
    </Collapsible>
  )
}
