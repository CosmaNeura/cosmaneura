import { useState } from "react"

import { Button } from "~/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog"
import {
  type PracticeRosterMember,
  restorePracticeMember,
  revokePracticeMember,
} from "~/configuration/client"
import { errorDetail } from "~/lib/error-detail"
import type { PracticeStatus } from "~/lib/practice-api"

/** Revoked by the owner, with access and the seat lasting until tonight. */
export function accessEnding(member: PracticeRosterMember) {
  return member.access_ends_at !== null && member.seat_active
}

/** Revoked by the owner and past the cut-off; the owner can still restore. */
function accessEnded(member: PracticeRosterMember) {
  return member.access_ends_at !== null && !member.seat_active
}

export function memberAccessLabel(member: PracticeRosterMember) {
  if (member.revoked) return "Revoked"
  if (accessEnding(member)) return "Access ends tonight at midnight ET"
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
  const name = member.display_name ?? "this clinician"
  const path = { member_id: member.member_id }

  if (member.revoked || member.role === "owner" || member.is_self) return null
  const restorable = member.access_ends_at !== null
  if (!restorable && !member.seat_active) return null

  async function act(run: () => Promise<{ error?: unknown; data?: unknown }>) {
    setBusy(true)
    setError(null)
    try {
      const { error: failure } = await run()
      if (failure) {
        setError(errorDetail(failure) ?? "That did not work. Try again.")
        return
      }
      setConfirming(false)
      await onChanged()
    } catch {
      setError("That did not work. Try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {restorable ? (
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Restore ${name}`}
          onClick={() => void act(() => restorePracticeMember({ path }))}
        >
          Restore
        </Button>
      ) : (
        <Dialog open={confirming} onOpenChange={setConfirming}>
          <Button
            variant="outline"
            size="sm"
            aria-label={`Revoke ${name}`}
            onClick={() => setConfirming(true)}
          >
            Revoke
          </Button>
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
      {error && !confirming ? (
        <p role="alert" className="text-destructive w-full">
          {error}
        </p>
      ) : null}
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
        {ended
          ? "Ask your practice owner if you need it restored."
          : "You keep access until midnight Eastern tonight."}
      </p>
    </div>
  )
}
