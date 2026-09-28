import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "~/components/ui/dialog"
import {
  type PracticeRosterMember,
  restorePracticeMember,
  revokePracticeMember,
} from "~/configuration/client"
import { errorDetail } from "~/lib/error-detail"
import type { PracticeStatus } from "~/lib/practice-api"

/** Revoked by the owner, with access and the seat lasting until tonight. */
export function accessEnding(
  member: PracticeRosterMember,
): member is PracticeRosterMember & { access_ends_at: string } {
  return member.access_ends_at !== null && member.seat_active
}

/** Revoked by the owner and past the cut-off; the owner can still restore. */
function accessEnded(member: PracticeRosterMember) {
  return member.access_ends_at !== null && !member.seat_active
}

/**
 * The server's cut-off in the viewer's own clock, so "midnight ET" is not
 * misread west of Eastern time or across the viewer's midnight.
 */
export function localAccessEnd(accessEndsAt: string, timeZone?: string) {
  return new Date(accessEndsAt).toLocaleString(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
    timeZone,
  })
}

export function memberAccessLabel(member: PracticeRosterMember) {
  if (member.revoked) return "Revoked"
  if (accessEnding(member)) {
    return `Access ends tonight at midnight ET (${localAccessEnd(member.access_ends_at)} your time)`
  }
  if (accessEnded(member)) return "Access revoked"
  if (!member.seat_active) return "Inactive"
  if (!member.access_in_force) return "No access"
  return "Active"
}

export function MemberAccessActions({
  member,
  onChanged,
}: {
  member: PracticeRosterMember
  onChanged: () => Promise<void>
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refocus, setRefocus] = useState(false)
  const action = useRef<HTMLButtonElement>(null)
  const name = member.display_name ?? "this clinician"
  // tells apart the buttons of members who never recorded a name
  const label =
    member.display_name ??
    `clinician joined ${new Date(member.joined_at).toLocaleDateString()}`
  const path = { member_id: member.member_id }

  const restorable = member.access_ends_at !== null
  // the confirmation belongs to the Revoke button, which a reload can replace
  const dialogShown = confirming && !restorable
  useEffect(() => {
    if (restorable) setConfirming(false)
  }, [restorable])
  // the button that was pressed is replaced by its opposite once the roster
  // reloads; keep keyboard focus on the row instead of dropping it
  useEffect(() => {
    if (!refocus) return
    action.current?.focus()
    setRefocus(false)
  }, [refocus])
  const rowError =
    error && !dialogShown ? (
      <p role="alert" className="text-destructive w-full">
        {error}
      </p>
    ) : null

  if (
    member.revoked ||
    member.role === "owner" ||
    member.is_self ||
    (!restorable && !member.seat_active)
  ) {
    return rowError
  }

  async function act(run: () => Promise<{ error?: unknown; data?: unknown }>) {
    setBusy(true)
    setError(null)
    let succeeded = false
    try {
      const { error: failure } = await run()
      if (failure) {
        setError(errorDetail(failure) ?? "That did not work. Try again.")
      } else {
        succeeded = true
        setConfirming(false)
      }
    } catch {
      setError("That did not work. Try again.")
    } finally {
      setBusy(false)
    }
    // a refusal usually means the member changed elsewhere (revoked,
    // restored, or the last seat taken), so the list is refreshed either way
    await onChanged()
    if (succeeded) setRefocus(true)
  }

  return (
    <>
      {restorable ? (
        <Button
          ref={action}
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Restore ${label}`}
          onClick={() => void act(() => restorePracticeMember({ path }))}
        >
          Restore
        </Button>
      ) : (
        <Dialog open={dialogShown} onOpenChange={setConfirming}>
          <DialogTrigger asChild>
            <Button
              ref={action}
              variant="outline"
              size="sm"
              aria-label={`Revoke ${label}`}
            >
              Revoke
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Revoke {name}’s access?</DialogTitle>
              <DialogDescription>
                They keep access until midnight Eastern tonight. Their seat
                frees then. No credit for the rest of this billing period; your
                bill drops at renewal. You can restore them anytime.
              </DialogDescription>
            </DialogHeader>
            {error ? (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            ) : null}
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={busy}>
                  Cancel
                </Button>
              </DialogClose>
              <Button
                variant="destructive"
                disabled={busy}
                onClick={() => void act(() => revokePracticeMember({ path }))}
              >
                Revoke access
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      {rowError}
    </>
  )
}

/** Tells a clinician their practice owner revoked them. */
export function PracticeAccessEndingNotice({
  practice,
}: {
  practice: PracticeStatus
}) {
  const ended = practice.access_ended
  // a permanent revocation comes from support, not from the practice owner
  if (practice.access_revoked || (!practice.access_ends_at && !ended)) {
    return null
  }
  return (
    <div
      role="status"
      className="not-prose border-destructive/40 bg-destructive/5 rounded-lg border p-4 text-sm"
    >
      <p className="font-semibold">
        Your practice owner ended your access to Cosmas.
      </p>
      <p className="text-muted-foreground mt-1">
        {ended || !practice.access_ends_at
          ? "Ask your practice owner if you need it restored."
          : `You keep access until midnight Eastern tonight (${localAccessEnd(practice.access_ends_at)} your time).`}
      </p>
    </div>
  )
}
