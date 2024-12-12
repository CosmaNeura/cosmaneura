import { useState } from 'react';
import { MessageContent } from '@/components/shared/MessageContent';
import { Mail } from '@/data/mock';

interface NoteContentProps {
  mail: Mail | null;
}

export function NoteContent({ mail }: NoteContentProps) {
  const [isSOAPOpen, setIsSOAPOpen] = useState(false);
  const [isNewPatientOpen, setIsNewPatientOpen] = useState(false);
  const [isTelehealthSOAPOpen, setIsTelehealthSOAPOpen] = useState(false);
  const [isTelehealthHPOpen, setIsTelehealthHPOpen] = useState(false);
  const [isTranscriptOpen, setIsTranscriptOpen] = useState(false);

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
      isSOAPOpen={isSOAPOpen}
      setIsSOAPOpen={setIsSOAPOpen}
      isNewPatientOpen={isNewPatientOpen}
      setIsNewPatientOpen={setIsNewPatientOpen}
      isTelehealthSOAPOpen={isTelehealthSOAPOpen}
      setIsTelehealthSOAPOpen={setIsTelehealthSOAPOpen}
      isTelehealthHPOpen={isTelehealthHPOpen}
      setIsTelehealthHPOpen={setIsTelehealthHPOpen}
      isTranscriptOpen={isTranscriptOpen}
      setIsTranscriptOpen={setIsTranscriptOpen}
    />
  );
}