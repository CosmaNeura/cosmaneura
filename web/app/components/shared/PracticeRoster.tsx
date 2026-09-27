import {
  type PracticeRosterMember,
  practiceRoster,
} from "~/configuration/client"
import type { PracticeStatus } from "~/lib/practice-api"

/** null when there is nothing to show; "unavailable" when the load failed. */
export type PracticeRosterLoad = PracticeRosterMember[] | "unavailable" | null

export async function loadPracticeRoster(): Promise<PracticeRosterLoad> {
  try {
    const { data, response } = await practiceRoster()
    if (data) return data.members
    // a lapsed practice's roster is behind the entitlement gate; the billing
    // panel already explains that state
    return response?.status === 402 ? null : "unavailable"
  } catch {
    return "unavailable"
  }
}

function accessLabel(member: PracticeRosterMember) {
  if (!member.seat_active) return "Inactive"
  if (!member.access_in_force) return "No access"
  return "Active"
}

export function PracticeRoster({
  practice,
  members,
}: {
  practice: PracticeStatus
  members: PracticeRosterLoad
}) {
  if (practice.role !== "owner" || !members) return null
  if (members === "unavailable") {
    return (
      <div className="not-prose rounded-lg border p-4 text-sm">
        <p className="font-semibold">Practice members</p>
        <p role="alert" className="text-muted-foreground mt-2">
          Practice members could not be loaded. Refresh to try again.
        </p>
      </div>
    )
  }
  const onlyYou = members.every((member) => member.is_self)

  return (
    <div className="not-prose rounded-lg border p-4 text-sm">
      <p className="font-semibold">Practice members</p>
      <ul className="mt-2 divide-y">
        {members.map((member, index) => (
          <li
            key={index}
            className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2"
          >
            <span>
              <span className="font-medium">
                {member.display_name ?? "Name not recorded"}
              </span>
              {member.is_self ? (
                <span className="text-muted-foreground"> (you)</span>
              ) : null}
              <span className="text-muted-foreground">
                {" "}
                · {member.role === "owner" ? "Owner" : "Clinician"}
              </span>
            </span>
            <span
              className={
                member.seat_active && member.access_in_force
                  ? undefined
                  : "text-muted-foreground"
              }
            >
              {accessLabel(member)}
              <span className="text-muted-foreground">
                {" "}
                · joined {new Date(member.joined_at).toLocaleDateString()}
              </span>
            </span>
          </li>
        ))}
      </ul>
      {onlyYou ? (
        <p className="text-muted-foreground mt-2 text-xs">Only you so far.</p>
      ) : null}
    </div>
  )
}
