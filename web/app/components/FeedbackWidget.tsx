import { ThumbsDown, ThumbsUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export function FeedbackWidget() {
  return (
    <div className="mt-6 rounded-lg border bg-card shadow-sm">
      <div className="flex flex-col gap-3 p-3 text-sm">
        <span className="text-muted-foreground">
          Was this note helpful?
        </span>
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2 justify-start"
            >
              <ThumbsUp className="mr-1 h-3 w-3" />
              Yes
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2 justify-start"
            >
              <ThumbsDown className="mr-1 h-3 w-3" />
              No
            </Button>
          </div>
          <div className="flex gap-2 w-full sm:w-auto">
            <Input
              placeholder="Add feedback..."
              className="h-8 text-xs flex-1 sm:w-48"
            />
            <Button size="sm" className="h-8 whitespace-nowrap">
              Send
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}