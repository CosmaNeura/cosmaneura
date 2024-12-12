import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Mail } from '@/data/mock';

interface MessageIntakeProps {
  mail: Mail;
  isAccuityIntakeOpen?: boolean;
  setIsAccuityIntakeOpen?: (open: boolean) => void;
  isIntakeTranscriptOpen?: boolean;
  setIsIntakeTranscriptOpen?: (open: boolean) => void;
}

export function MessageIntake({
  mail,
  isAccuityIntakeOpen,
  setIsAccuityIntakeOpen,
  isIntakeTranscriptOpen,
  setIsIntakeTranscriptOpen,
}: MessageIntakeProps) {
  return (
    <>
      <div className="mt-6">
        <Collapsible open={isAccuityIntakeOpen} onOpenChange={setIsAccuityIntakeOpen}>
          <div className="flex items-start">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="p-0 h-auto">
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${
                    isAccuityIntakeOpen ? 'transform rotate-180' : ''
                  }`}
                />
                <span className="ml-2 text-xl font-semibold">
                  Accuity Intake
                </span>
              </Button>
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="mt-3">
            <div className="text-sm text-left">
              Accuity intake form content and details
            </div>
          </CollapsibleContent>
        </Collapsible>
      </div>

      <div className="mt-6">
        <Collapsible open={isIntakeTranscriptOpen} onOpenChange={setIsIntakeTranscriptOpen}>
          <div className="flex items-start">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="p-0 h-auto">
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${
                    isIntakeTranscriptOpen ? 'transform rotate-180' : ''
                  }`}
                />
                <span className="ml-2 text-xl font-semibold">
                  Intake Transcript
                </span>
              </Button>
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="mt-3">
            <div className="text-sm text-left">
              Intake transcript content and details
            </div>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </>
  );
}