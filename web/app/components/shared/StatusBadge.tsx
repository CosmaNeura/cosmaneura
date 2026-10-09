import { cn } from "@/lib/utils"

export type StatusBadgeOptions = "completed" | "in-progress" | "not-started"

const DEFAULT_LABELS: Record<StatusBadgeOptions, string> = {
  completed: "Completed",
  "in-progress": "In progress",
  "not-started": "Not started",
}

// Pending is a normal state (a note not yet written, a meeting not yet held),
// so it reads as neutral rather than as an error.
const TONES = {
  light: {
    completed: ["bg-success/10 text-foreground", "bg-success"],
    "in-progress": ["bg-accent/20 text-foreground", "bg-accent"],
    "not-started": ["bg-muted text-muted-foreground", "bg-muted-foreground/60"],
  },
  navy: {
    completed: ["bg-success/25 text-sidebar-foreground", "bg-success"],
    "in-progress": [
      "bg-sidebar-primary/20 text-sidebar-foreground",
      "bg-sidebar-primary",
    ],
    "not-started": [
      "bg-sidebar-foreground/10 text-on-navy-muted",
      "bg-sidebar-foreground/50",
    ],
  },
} satisfies Record<string, Record<StatusBadgeOptions, [string, string]>>

interface StatusBadgeProps {
  status: StatusBadgeOptions
  label?: string
  surface?: keyof typeof TONES
  className?: string
}

export function StatusBadge({
  status,
  label,
  surface = "light",
  className,
}: StatusBadgeProps) {
  const [badgeTone, dotTone] = TONES[surface][status]
  return (
    <div
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        badgeTone,
        className,
      )}
    >
      <span
        className={cn("mr-1 h-1.5 w-1.5 rounded-full", dotTone)}
        aria-hidden="true"
      />
      {label ?? DEFAULT_LABELS[status]}
    </div>
  )
}
