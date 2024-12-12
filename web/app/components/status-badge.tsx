import { cn } from "@/lib/utils";

type Status = "completed" | "in-progress" | "not-started";

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        status === "completed" && "bg-success/10 text-success",
        status === "in-progress" && "bg-warning/10 text-warning",
        status === "not-started" && "bg-error/10 text-error",
        className
      )}
    >
      <span className={cn(
        "mr-1 h-1.5 w-1.5 rounded-full",
        status === "completed" && "bg-success",
        status === "in-progress" && "bg-warning",
        status === "not-started" && "bg-error"
      )} />
      {status === "completed" && "Completed"}
      {status === "in-progress" && "In Progress"}
      {status === "not-started" && "Not Started"}
    </div>
  );
}