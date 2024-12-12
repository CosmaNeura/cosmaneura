import { Download, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { Mail } from '@/data/mock';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface MessageHeaderProps {
  mail: Mail;
  onDownload: () => void;
  showStatus?: boolean;
}

export function MessageHeader({ mail, onDownload, showStatus }: MessageHeaderProps) {
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
            <span>{mail.date}</span>
          </p>
          {showStatus && mail.intakeCompletedAt && (
            <p className="text-sm">
              <span className="font-medium text-muted-foreground">Intake Completed At:</span>{' '}
              <span>{mail.date}</span>
            </p>
          )}
          <p className="text-sm">
            <span className="font-medium text-muted-foreground">Location:</span>{' '}
            <span>{mail.location}</span>
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex md:hidden">
          <Button variant="ghost" size="icon" onClick={onDownload}>
            <Download className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}