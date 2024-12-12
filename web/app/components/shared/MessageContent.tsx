import * as React from "react";
import { Mail } from '@/data/mock';
import { MessageHeader } from '@/components/message/MessageHeader';
import { MessageTemplates } from '@/components/message/MessageTemplates';
import { MessageIntake } from '@/components/message/MessageIntake';
import { PatientDataSections } from '@/components/patient/PatientDataSections';
import { FeedbackWidget } from './FeedbackWidget';

type MessageContentProps = {
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
  isAccuityIntakeOpen?: boolean;
  setIsAccuityIntakeOpen?: (open: boolean) => void;
  isIntakeTranscriptOpen?: boolean;
  setIsIntakeTranscriptOpen?: (open: boolean) => void;
  showTemplates?: boolean;
  showStatus?: boolean;
};

export function MessageContent({
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
  isAccuityIntakeOpen,
  setIsAccuityIntakeOpen,
  isIntakeTranscriptOpen,
  setIsIntakeTranscriptOpen,
  showTemplates = true,
  showStatus = false,
}: MessageContentProps) {
  const [isVisible, setIsVisible] = React.useState(true);
  const [currentMail, setCurrentMail] = React.useState(mail);

  React.useEffect(() => {
    if (mail.email !== currentMail.email) {
      setIsVisible(false);
      const timer = setTimeout(() => {
        setCurrentMail(mail);
        setIsVisible(true);
      }, 150);
      return () => clearTimeout(timer);
    }
  }, [mail, currentMail]);

  return (
    <div className="space-y-6">
      <div className={`transition-all duration-150 ease-in-out transform ${
        isVisible ? 'translate-y-0 opacity-100' : 'translate-y-4 opacity-0'
      }`}>
        <div className="rounded-lg border bg-card p-6 shadow-sm">
          <MessageHeader 
            mail={currentMail}
            showStatus={showStatus}
          />

          <PatientDataSections className="mb-6" mail={currentMail} />

          {showTemplates && (
            <MessageTemplates
              mail={currentMail}
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
          )}

          {!showTemplates && (
            <MessageIntake
              mail={currentMail}
              isAccuityIntakeOpen={isAccuityIntakeOpen}
              setIsAccuityIntakeOpen={setIsAccuityIntakeOpen}
              isIntakeTranscriptOpen={isIntakeTranscriptOpen}
              setIsIntakeTranscriptOpen={setIsIntakeTranscriptOpen}
            />
          )}
        </div>
      </div>

      <div className="flex flex-col sm:flex-row gap-4">
        <FeedbackWidget />
      </div>
    </div>
  );
}