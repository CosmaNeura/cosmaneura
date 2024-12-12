import { StatusBadge } from '@/components/shared/StatusBadge';
import { Mail } from '@/data/mock';

interface MessageHeaderProps {
  mail: Mail;
  showStatus?: boolean;
}

export function MessageHeader({ mail, showStatus }: MessageHeaderProps) {
  return (
    <div className="mb-6 flex items-start justify-between">
      <div className="text-left">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">{mail.name}</h1>
          {showStatus && <StatusBadge status={mail.status || "not-started"} />}
        </div>
        <div className="mt-1 space-y-1">
          <p className="text-sm">
            <span className="font-medium text-muted-foreground">Appointment Date:</span>{' '}
            <span>{mail.appointmentDate}</span>
          </p>
          {showStatus && mail.intakeCompletedAt && (
            <p className="text-sm">
              <span className="font-medium text-muted-foreground">Intake Completed At:</span>{' '}
              <span>{mail.intakeCompletedAt}</span>
            </p>
          )}
          <p className="text-sm">
            <span className="font-medium text-muted-foreground">Location:</span>{' '}
            <span>{mail.location}</span>
          </p>
        </div>
      </div>
    </div>
  );
}