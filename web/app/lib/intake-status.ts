import type { StatusBadgeOptions } from "@/components/shared/StatusBadge"
import type { IntakeStatus } from "~/configuration/client"
import type { LabelSet } from "~/utils/terminology"

// Shared by the intake sidebar and the intake detail page so both describe
// an intake the same way.
export function intakeStatusBadge(
  status: IntakeStatus,
  t: Pick<LabelSet, "patient">,
): { status: StatusBadgeOptions; label: string } {
  switch (status) {
    case "complete":
      return { status: "completed", label: "Completed" }
    case "processing":
      return { status: "in-progress", label: "Processing" }
    case "pending":
      return { status: "not-started", label: `Awaiting ${t.patient}` }
    case "none":
      return { status: "not-started", label: "Not sent" }
  }
}
