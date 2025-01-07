import * as React from "react"

import { MessageHeader } from "@/components/message/MessageHeader"
import { MessageIntake } from "@/components/message/MessageIntake"
import { MessageTemplates } from "@/components/message/MessageTemplates"
import { PatientDataSections } from "@/components/patient/PatientDataSections"
import { Mail } from "@/data/mock"

import AIVisitNote from "../AIVisitNote"
import { FeedbackWidget } from "./FeedbackWidget"

type MessageContentProps = {
  mail: Mail
  notes: []
  transcript: {}
}

function VisitTranscript() {
  return <></>
}

export function MessageContent({
  mail,
  notes,
  transcript,
  // showTemplates = true,
  // showStatus = false,
}: MessageContentProps) {
  return (
    <div className="space-y-6">
      <div className={"transform transition-all duration-150 ease-in-out"}>
        <div className="rounded-lg border bg-card p-6 shadow-sm">
          <MessageHeader
            mail={mail}
            // showStatus={showStatus}
          />

          {notes &&
            notes.map((note) => {
              return <AIVisitNote note={note} />
            })}

          <VisitTranscript />

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
