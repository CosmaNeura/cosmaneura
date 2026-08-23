import { ChevronDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { useTerminology } from "~/utils/terminology"

interface MessageTemplatesProps {
  isSOAPOpen?: boolean
  setIsSOAPOpen?: (open: boolean) => void
  isNewPatientOpen?: boolean
  setIsNewPatientOpen?: (open: boolean) => void
  isTelehealthSOAPOpen?: boolean
  setIsTelehealthSOAPOpen?: (open: boolean) => void
  isTelehealthHPOpen?: boolean
  setIsTelehealthHPOpen?: (open: boolean) => void
  isTranscriptOpen?: boolean
  setIsTranscriptOpen?: (open: boolean) => void
}

export function MessageTemplates({
  isSOAPOpen,
  setIsSOAPOpen,
  isNewPatientOpen,
  setIsNewPatientOpen,
  isTelehealthSOAPOpen,
  setIsTelehealthSOAPOpen,
  isTelehealthHPOpen,
  setIsTelehealthHPOpen,
  isTranscriptOpen,
  setIsTranscriptOpen,
}: MessageTemplatesProps) {
  const t = useTerminology()
  return (
    <div className="space-y-4">
      <Collapsible open={isSOAPOpen} onOpenChange={setIsSOAPOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto p-0">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isSOAPOpen ? "rotate-180 transform" : ""
                }`}
              />
              <span className="ml-2 text-xl font-semibold">SOAP</span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-left text-sm">
            Template content for SOAP note
          </div>
        </CollapsibleContent>
      </Collapsible>

      <Collapsible open={isNewPatientOpen} onOpenChange={setIsNewPatientOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto p-0">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isNewPatientOpen ? "rotate-180 transform" : ""
                }`}
              />
              <span className="ml-2 text-xl font-semibold">
                New {t.patientTitle} Visit
              </span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-left text-sm">
            Template content for new {t.patient} visit
          </div>
        </CollapsibleContent>
      </Collapsible>

      <Collapsible
        open={isTelehealthSOAPOpen}
        onOpenChange={setIsTelehealthSOAPOpen}
      >
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto p-0">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isTelehealthSOAPOpen ? "rotate-180 transform" : ""
                }`}
              />
              <span className="ml-2 text-xl font-semibold">
                Telehealth SOAP
              </span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-left text-sm">
            Template content for telehealth SOAP note
          </div>
        </CollapsibleContent>
      </Collapsible>

      <Collapsible
        open={isTelehealthHPOpen}
        onOpenChange={setIsTelehealthHPOpen}
      >
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto p-0">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isTelehealthHPOpen ? "rotate-180 transform" : ""
                }`}
              />
              <span className="ml-2 text-xl font-semibold">Telehealth H&P</span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-left text-sm">
            Template content for telehealth H&P
          </div>
        </CollapsibleContent>
      </Collapsible>

      <div className="mt-6">
        <Collapsible open={isTranscriptOpen} onOpenChange={setIsTranscriptOpen}>
          <div className="flex items-start">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="h-auto p-0">
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${
                    isTranscriptOpen ? "rotate-180 transform" : ""
                  }`}
                />
                <span className="ml-2 text-xl font-semibold">
                  Full Transcript
                </span>
              </Button>
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="mt-3">
            <div className="text-left text-sm whitespace-pre-wrap">
              Transcript content and details
            </div>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </div>
  )
}
