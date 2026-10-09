import { useState } from "react"

import { ChevronDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
// TODO https://github.com/hey-api/openapi-ts/issues/1571
import type { AiVisitTranscript, TranscriptEntry } from "~/configuration/client"
import { secondsToMinutes } from "~/lib/date"

// Mirrors CLEANUP_AGE_DAYS in app/jobs/transcript_cleanup.py.
export const TRANSCRIPT_RETENTION_DAYS = 14

const DAY_MS = 24 * 60 * 60 * 1000

export function transcriptExpiry(createdAt: string): Date {
  return new Date(
    new Date(createdAt).getTime() + TRANSCRIPT_RETENTION_DAYS * DAY_MS,
  )
}

function RetentionNotice({ createdAt }: { createdAt?: string | null }) {
  if (!createdAt) {
    return (
      <>
        Transcripts are deleted {TRANSCRIPT_RETENTION_DAYS} days after they are
        created.
      </>
    )
  }
  const expiry = transcriptExpiry(createdAt).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })
  return <>Available until {expiry}.</>
}

function TranscriptUnavailable({ message }: { message: string }) {
  return (
    <>
      <div className="mb-2 text-xl font-semibold">Visit Transcript</div>
      <div className="text-muted-foreground text-sm italic">{message}</div>
    </>
  )
}

export default function VisitTranscriptViewer({
  transcript,
}: {
  transcript: AiVisitTranscript | null
}) {
  const [isOpen, setOpen] = useState(false)

  if (!transcript) {
    return <TranscriptUnavailable message="No Transcript Available." />
  }

  const expired =
    !!transcript.created_at &&
    transcriptExpiry(transcript.created_at) < new Date()
  if (transcript.deleted_at || expired) {
    return (
      <TranscriptUnavailable
        message={
          expired
            ? `This transcript has been deleted. Transcripts are kept for ${TRANSCRIPT_RETENTION_DAYS} days.`
            : "This transcript has been deleted."
        }
      />
    )
  }

  return (
    <Collapsible open={isOpen} onOpenChange={setOpen}>
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
          Duration: {secondsToMinutes(transcript.duration ?? undefined)}
        </div>
        <div className="text-muted-foreground mb-2 text-sm italic">
          <RetentionNotice createdAt={transcript.created_at} />
        </div>
        {transcript.transcript_data.map((transcriptEntry: TranscriptEntry) => {
          return (
            <div className="mb-2 break-words" key={transcriptEntry.startTime}>
              <span className="font-bold">{transcriptEntry.speaker}: </span>
              {transcriptEntry.content}
            </div>
          )
        })}
      </CollapsibleContent>
    </Collapsible>
  )
}
