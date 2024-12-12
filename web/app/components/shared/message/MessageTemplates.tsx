import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Mail } from '@/data/mock';

interface MessageTemplatesProps {
  mail: Mail;
  isSOAPOpen?: boolean;
  setIsSOAPOpen?: (open: boolean) => void;
  isNewPatientOpen?: boolean;
  setIsNewPatientOpen?: (open: boolean) => void;
  isTelehealthSOAPOpen?: boolean;
  setIsTelehealthSOAPOpen?: (open: boolean) => void;
  isTelehealthHPOpen?: boolean;
  setIsTelehealthHPOpen?: (open: boolean) => void;
  isTranscriptOpen?: boolean;
  setIsTranscriptOpen?: (open: boolean) => void;
}

export function MessageTemplates({
  mail,
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
  return (
    <div className="space-y-4">
      <Collapsible open={isSOAPOpen} onOpenChange={setIsSOAPOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="p-0 h-auto">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isSOAPOpen ? 'transform rotate-180' : ''
                }`}
              />
              <span className="ml-2 text-xl font-semibold">SOAP</span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-sm text-left">
            Template content for SOAP note
          </div>
        </CollapsibleContent>
      </Collapsible>

      <Collapsible open={isNewPatientOpen} onOpenChange={setIsNewPatientOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="p-0 h-auto">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isNewPatientOpen ? 'transform rotate-180' : ''
                }`}
              />
              <span className="ml-2 text-xl font-semibold">
                New Patient Visit
              </span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-sm text-left">
            Template content for new patient visit
          </div>
        </CollapsibleContent>
      </Collapsible>

      <Collapsible open={isTelehealthSOAPOpen} onOpenChange={setIsTelehealthSOAPOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="p-0 h-auto">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isTelehealthSOAPOpen ? 'transform rotate-180' : ''
                }`}
              />
              <span className="ml-2 text-xl font-semibold">
                Telehealth SOAP
              </span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-sm text-left">
            Template content for telehealth SOAP note
          </div>
        </CollapsibleContent>
      </Collapsible>

      <Collapsible open={isTelehealthHPOpen} onOpenChange={setIsTelehealthHPOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="p-0 h-auto">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isTelehealthHPOpen ? 'transform rotate-180' : ''
                }`}
              />
              <span className="ml-2 text-xl font-semibold">
                Telehealth H&P
              </span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-sm text-left">
            Template content for telehealth H&P
          </div>
        </CollapsibleContent>
      </Collapsible>

      <div className="mt-6">
        <Collapsible open={isTranscriptOpen} onOpenChange={setIsTranscriptOpen}>
          <div className="flex items-start">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="p-0 h-auto">
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${
                    isTranscriptOpen ? 'transform rotate-180' : ''
                  }`}
                />
                <span className="ml-2 text-xl font-semibold">
                  Full Transcript
                </span>
              </Button>
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="mt-3">
            <div className="whitespace-pre-wrap text-sm text-left">
              {mail.content}
            </div>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </div>
  );
}