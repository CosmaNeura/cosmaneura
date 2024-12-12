import { useState } from 'react';
import { MessageContent } from '@/components/shared/MessageContent';
import { Mail } from '@/data/mock';

interface IntakeContentProps {
  mail: Mail | null;
}

export function IntakeContent({ mail }: IntakeContentProps) {
  const [isAccuityIntakeOpen, setIsAccuityIntakeOpen] = useState(false);
  const [isIntakeTranscriptOpen, setIsIntakeTranscriptOpen] = useState(false);

  if (!mail) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-5rem)]">
        <div className="text-center text-muted-foreground">
          <h2 className="text-xl font-semibold mb-2">Select a patient</h2>
          <p className="text-sm">Choose a patient from the list to view their details</p>
        </div>
      </div>
    );
  }

  return (
    <MessageContent
      mail={mail}
      onDownload={() => {}}
      showTemplates={false}
      isAccuityIntakeOpen={isAccuityIntakeOpen}
      setIsAccuityIntakeOpen={setIsAccuityIntakeOpen}
      isIntakeTranscriptOpen={isIntakeTranscriptOpen}
      setIsIntakeTranscriptOpen={setIsIntakeTranscriptOpen}
      showStatus={true}
    />
  );
}