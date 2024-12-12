import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function UploadRecordingButton() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="hidden md:flex"
          >
            <Upload className="mr-2 h-4 w-4" />
            Upload Recording
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          Upload an audio recording of the patient visit
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}