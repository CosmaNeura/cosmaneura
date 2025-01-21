import { ThumbsDown, ThumbsUp } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function FeedbackWidget() {
  // TODO renenable when we have time to wire up an email
  return null

  return (
    <div className="mt-6 rounded-lg border bg-card shadow-sm">
      <div className="flex flex-col gap-3 p-3 text-sm">
        <span className="text-muted-foreground">Was this note helpful?</span>
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 justify-start px-2"
            >
              <ThumbsUp className="mr-1 h-3 w-3" />
              Yes
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 justify-start px-2"
            >
              <ThumbsDown className="mr-1 h-3 w-3" />
              No
            </Button>
          </div>
          <div className="flex w-full gap-2 sm:w-auto">
            <Input
              placeholder="Add feedback..."
              className="h-8 flex-1 text-xs sm:w-48"
            />
            <Button size="sm" className="h-8 whitespace-nowrap">
              Send
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
