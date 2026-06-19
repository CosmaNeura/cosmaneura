import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export type EngagementRoleKey = "self_directed" | "collaborative" | "directed"

type RoleMeta = { description: string; badge: string; dot: string }

const ROLE_META: Record<EngagementRoleKey, RoleMeta> = {
  self_directed: {
    description: "Wants full information and to lead the decision themselves.",
    badge: "bg-success/10 text-success",
    dot: "bg-success",
  },
  collaborative: {
    description: "Wants to share the decision with the clinician.",
    badge: "bg-primary/10 text-primary",
    dot: "bg-primary",
  },
  directed: {
    description: "Prefers the clinician to lead and decide.",
    badge: "bg-warning/10 text-warning",
    dot: "bg-warning",
  },
}

const FALLBACK: RoleMeta = {
  description: "Communication style estimate.",
  badge: "bg-muted text-muted-foreground",
  dot: "bg-muted-foreground",
}

export function EngagementBadge({
  roleKey,
  roleName,
  className,
}: {
  roleKey: EngagementRoleKey
  roleName: string
  className?: string
}) {
  const meta = ROLE_META[roleKey] ?? FALLBACK

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          title={meta.description}
          className={cn(
            "inline-flex cursor-default items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
            meta.badge,
            className,
          )}
        >
          <span className={cn("mr-1 h-1.5 w-1.5 rounded-full", meta.dot)} />
          {roleName}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <p className="max-w-xs">{meta.description}</p>
        <p className="text-muted-foreground mt-1 text-[10px]">
          Communication aid — not a diagnosis.
        </p>
      </TooltipContent>
    </Tooltip>
  )
}
