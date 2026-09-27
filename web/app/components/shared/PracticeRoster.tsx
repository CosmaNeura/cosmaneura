import { useEffect, useState } from "react"

import { CopyButton } from "~/components/shared/CopyButton"
import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import {
  type PracticeInvitationIssued,
  type PracticeRosterInvitation,
  type PracticeRosterMember,
  type PracticeRoster as Roster,
  createPracticeInvitation,
  practiceRoster,
  resendPracticeInvitation,
  revokePracticeInvitation,
} from "~/configuration/client"
import type { PracticeStatus } from "~/lib/practice-api"

/**
 * null when there is nothing to show; "restricted" when the owner's own access
 * is not in force; "unavailable" when the load failed.
 */
export type PracticeRosterLoad = Roster | "restricted" | "unavailable" | null

export async function loadPracticeRoster(): Promise<PracticeRosterLoad> {
  try {
    const { data, response } = await practiceRoster()
    if (data) return data
    // a lapsed practice's roster is behind the entitlement gate; the billing
    // panel already explains that state
    if (response?.status === 402) return null
    // settings stays reachable for an owner whose access was revoked, unseated
    // or lost its BAA; refreshing cannot change that answer
    if (response?.status === 403) return "restricted"
    return "unavailable"
  } catch {
    return "unavailable"
  }
}

function errorDetail(error: unknown, fallback: string) {
  if (error && typeof error === "object" && "detail" in error) {
    const { detail } = error as { detail: unknown }
    if (typeof detail === "string") return detail
  }
  return fallback
}

function count(n: number, singular: string, plural: string) {
  return `${n} ${n === 1 ? singular : plural}`
}

function accessLabel(member: PracticeRosterMember) {
  if (member.revoked) return "Revoked"
  if (!member.seat_active) return "Inactive"
  if (!member.access_in_force) return "No access"
  return "Active"
}

function InvitationLink({ issued }: { issued: PracticeInvitationIssued }) {
  const email = issued.invitation.invitee_email
  return (
    <div className="bg-muted/40 mt-3 rounded-md border p-3">
      <p role="status">
        {issued.email_queued
          ? "Email sent. You can also share this link."
          : `Send this link to ${email ?? "the clinician"}.`}
      </p>
      <p className="mt-2 font-mono text-xs break-all">{issued.invite_url}</p>
      <CopyButton text={issued.invite_url} className="mt-2" />
    </div>
  )
}

export function InviteClinicianForm({
  onIssued,
}: {
  onIssued: (issued: PracticeInvitationIssued) => void
}) {
  const [email, setEmail] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: failure } = await createPracticeInvitation({
        body: { email },
        // a fresh key per submit: the server replays a retried request and
        // refuses the same key for a different address
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
      if (data) {
        setEmail("")
        onIssued(data)
      } else {
        setError(errorDetail(failure, "The invitation could not be sent."))
      }
    } catch {
      setError("The invitation could not be sent.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(e) => void onSubmit(e)} className="mt-4 space-y-2">
      <label htmlFor="invite-clinician-email" className="block font-medium">
        Invite a clinician
      </label>
      <div className="flex gap-2">
        <Input
          id="invite-clinician-email"
          type="email"
          required
          autoComplete="off"
          placeholder="clinician@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={busy || !email.trim()}>
          Send invitation
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        An invitation is free. A seat is billed only once it is accepted.
      </p>
      {error ? (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      ) : null}
    </form>
  )
}

function InvitationRow({
  invitation,
  onIssued,
  onChanged,
}: {
  invitation: PracticeRosterInvitation
  onIssued: (issued: PracticeInvitationIssued) => void
  onChanged: () => Promise<void>
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const path = { invitation_id: invitation.invitation_id }

  async function act(run: () => Promise<{ error?: unknown; data?: unknown }>) {
    setBusy(true)
    setError(null)
    try {
      const { error: failure } = await run()
      if (failure) {
        setError(errorDetail(failure, "That did not work. Try again."))
        return
      }
      await onChanged()
    } catch {
      setError("That did not work. Try again.")
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    await act(async () => {
      const result = await resendPracticeInvitation({
        path,
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
      if (result.data) onIssued(result.data)
      return result
    })
  }

  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2">
      <span>
        <span className="font-medium">
          {invitation.invitee_email ?? "Invited clinician"}
        </span>
        <span className="text-muted-foreground">
          {" "}
          ·{" "}
          {invitation.expired
            ? "Expired"
            : `Invited · expires ${new Date(invitation.expires_at).toLocaleDateString()}`}
        </span>
      </span>
      <span className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => void resend()}
        >
          Resend
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => void act(() => revokePracticeInvitation({ path }))}
        >
          {invitation.expired ? "Remove" : "Revoke"}
        </Button>
      </span>
      {error ? (
        <p role="alert" className="text-destructive w-full">
          {error}
        </p>
      ) : null}
    </li>
  )
}

export function PracticeRoster({
  practice,
  roster: loaded,
}: {
  practice: PracticeStatus
  roster: PracticeRosterLoad
}) {
  const [roster, setRoster] = useState(loaded)
  const [issued, setIssued] = useState<PracticeInvitationIssued | null>(null)
  useEffect(() => setRoster(loaded), [loaded])

  async function reload() {
    setRoster(await loadPracticeRoster())
  }

  async function onIssued(next: PracticeInvitationIssued) {
    setIssued(next)
    await reload()
  }

  if (practice.role !== "owner" || !roster) return null
  if (roster === "unavailable" || roster === "restricted") {
    return (
      <div className="not-prose rounded-lg border p-4 text-sm">
        <p className="font-semibold">Practice members</p>
        <p role="alert" className="text-muted-foreground mt-2">
          {roster === "restricted"
            ? "Practice members are not available while your access is restricted."
            : "Practice members could not be loaded. Refresh to try again."}
        </p>
      </div>
    )
  }
  const { members, invitations } = roster
  const pending = invitations.filter((invitation) => !invitation.expired).length
  // a solo account has no one else yet, so "so far" would mislead; without a
  // seat summary the account kind is unknown and the line is omitted
  const onlyYou =
    practice.seat_summary?.account_kind === "practice" &&
    invitations.length === 0 &&
    members.every((member) => member.is_self)

  return (
    <div className="not-prose rounded-lg border p-4 text-sm">
      <p className="font-semibold">Practice members</p>
      <p className="text-muted-foreground mt-1">
        {count(roster.occupied_seats, "seat", "seats")} in use
        {pending > 0
          ? ` · ${count(pending, "invitation", "invitations")} pending — not billed until accepted`
          : null}
      </p>
      <ul className="mt-2 divide-y">
        {members.map((member) => (
          <li
            key={member.member_id}
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
        {invitations.map((invitation) => (
          <InvitationRow
            key={invitation.invitation_id}
            invitation={invitation}
            onIssued={setIssued}
            onChanged={reload}
          />
        ))}
      </ul>
      {onlyYou ? (
        <p className="text-muted-foreground mt-2 text-xs">Only you so far.</p>
      ) : null}
      <InviteClinicianForm onIssued={(next) => void onIssued(next)} />
      {issued ? <InvitationLink issued={issued} /> : null}
    </div>
  )
}
