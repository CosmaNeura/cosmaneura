import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type PatientConfirmation,
  type PatientEmailConsent,
  type PatientEmailConsentDecision,
  type PatientSMSConsent,
  type VisitPrepPatientClient,
  VisitPrepPatientFailure,
} from "~/lib/visit-prep-patient-api"

const labels = {
  not_recorded: "No email choice recorded at last load",
  grant_recorded:
    "Permission choice recorded at last load — sending not enabled",
  withdrawn: "Email permission withdrawn at last load",
  expired: "Recorded email permission expired at last load",
}
const errors = {
  unavailable:
    "This link is unavailable. Ask your care team for a new invitation.",
  invalid: "Check your choice. Confirm the recipient again before continuing.",
  conflict:
    "Email choices could not be confirmed. Confirm the recipient again to load current state.",
  limited:
    "Too many confirmation attempts. Wait 15 minutes before trying again.",
  temporary:
    "We could not confirm the request. A choice may have been saved. Confirm the recipient again to load current state; do not repeat the decision automatically.",
}

/** Shared optional-choice UI; independent channel endpoints and consent history. */
export function PatientEmailConsent({
  client,
  onClose,
  onUnavailable,
  continuedRole = null,
  channel = "email",
}: {
  client: VisitPrepPatientClient
  onClose: () => void
  onUnavailable: () => void
  continuedRole?: PatientConfirmation["role"] | null
  channel?: "email" | "sms"
}) {
  const sms = channel === "sms"
  const channelName = sms ? "SMS" : "email"
  const contactName = sms ? "mobile number" : "email address"
  const generation = useRef(0)
  const mounted = useRef(false)
  const pending = useRef(false)
  const confirmation = useRef<PatientConfirmation | null>(null)
  const [value, setValue] = useState<
    PatientEmailConsent | PatientSMSConsent | null
  >(null)
  const [firstName, setFirstName] = useState("")
  const [role, setRole] = useState<PatientConfirmation["role"]>("patient")
  const [expiry, setExpiry] = useState("")
  const [recipientAttested, setRecipientAttested] = useState(false)
  const [caregiverAttested, setCaregiverAttested] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  function clear() {
    generation.current += 1
    pending.current = false
    confirmation.current = null
    setValue(null)
    setFirstName("")
    setRole("patient")
    setExpiry("")
    setRecipientAttested(false)
    setCaregiverAttested(false)
    setBusy(false)
    setMessage(null)
  }

  useEffect(() => {
    mounted.current = true
    generation.current += 1
    pending.current = false
    function conceal() {
      if (document.visibilityState !== "visible") clear()
    }
    window.addEventListener("blur", clear)
    document.addEventListener("visibilitychange", conceal)
    return () => {
      mounted.current = false
      generation.current += 1
      confirmation.current = null
      window.removeEventListener("blur", clear)
      document.removeEventListener("visibilitychange", conceal)
    }
  }, [])

  useEffect(() => {
    if (continuedRole) load()
  }, [continuedRole, channel])

  async function run(operation: (version: number) => Promise<void>) {
    if (pending.current) return
    const version = ++generation.current
    pending.current = true
    setBusy(true)
    setMessage(null)
    try {
      await operation(version)
    } catch (error) {
      if (!mounted.current || generation.current !== version) return
      const code =
        error instanceof VisitPrepPatientFailure ? error.code : "temporary"
      clear()
      setMessage(
        sms
          ? code === "unavailable"
            ? errors.unavailable
            : "We could not confirm the SMS choice. A decision may already have been saved. Load current SMS choices before deciding again."
          : errors[code],
      )
      if (code === "unavailable") onUnavailable()
    } finally {
      if (mounted.current && generation.current === version) {
        pending.current = false
        setBusy(false)
      }
    }
  }

  function load() {
    if (pending.current || (!continuedRole && !firstName.trim())) return
    const confirmed: PatientConfirmation = continuedRole
      ? { role: continuedRole }
      : { first_name: firstName, role }
    void run(async (version) => {
      const next = await (sms
        ? client.readSMSConsent(confirmed)
        : client.readEmailConsent(confirmed))
      if (!mounted.current || generation.current !== version) return
      if (next.actor_role !== confirmed.role)
        throw new VisitPrepPatientFailure("temporary")
      confirmation.current = confirmed
      setValue(next)
      setFirstName("")
      setExpiry("")
      setRecipientAttested(false)
      setCaregiverAttested(false)
    })
  }

  const deadline =
    sms && value
      ? Date.parse(value.maximum_expires_at)
      : /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(expiry)
        ? Date.parse(expiry + ":00Z")
        : NaN
  const attestations =
    recipientAttested &&
    (value?.actor_role !== "caregiver" || caregiverAttested)
  const canDecide =
    value !== null && value.event_sequence < 2_147_483_647 && !busy
  const canGrant =
    canDecide &&
    attestations &&
    value!.can_grant &&
    Number.isFinite(deadline) &&
    deadline > Math.max(Date.now(), Date.parse(value!.checked_at)) &&
    deadline <= Date.parse(value!.maximum_expires_at)

  function decide(kind: "granted" | "withdrawn") {
    if (
      pending.current ||
      !value ||
      !confirmation.current ||
      !canDecide ||
      (kind === "granted" && !canGrant)
    )
      return
    const payload: PatientEmailConsentDecision = {
      ...confirmation.current,
      expected_event_sequence: value.event_sequence,
      event_kind: kind,
      disclosure_fingerprint:
        kind === "granted"
          ? value.grant_disclosure_fingerprint
          : value.withdrawal_disclosure_fingerprint,
      expires_at: kind === "granted" ? new Date(deadline).toISOString() : null,
      recipient_control_attested: kind === "granted" && recipientAttested,
      caregiver_authority_attested:
        kind === "granted" &&
        value.actor_role === "caregiver" &&
        caregiverAttested,
    }
    void run(async (version) => {
      const key = crypto.randomUUID()
      // Clear the displayed snapshot before submitting this frozen intent.
      setValue(null)
      confirmation.current = null
      setExpiry("")
      setRecipientAttested(false)
      setCaregiverAttested(false)
      const receipt = await (sms
        ? client.decideSMSConsent(payload, key)
        : client.decideEmailConsent(payload, key))
      if (!mounted.current || generation.current !== version) return
      if (
        receipt.event_kind !== kind ||
        receipt.event_sequence !== payload.expected_event_sequence + 1 ||
        (receipt.expires_at === null
          ? payload.expires_at !== null
          : payload.expires_at === null ||
            Date.parse(receipt.expires_at) !== Date.parse(payload.expires_at))
      )
        throw new VisitPrepPatientFailure("temporary")
      clear()
      setMessage(
        sms
          ? "Your decision was recorded. Load current SMS choices to review it. No text has been sent by this screen."
          : "Your decision was recorded. Confirm the recipient again to load current email choices. No email has been sent by this screen.",
      )
    })
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold">Optional {channelName} choices</h1>
      <p className="mt-3">
        You can prepare for your visit without allowing{" "}
        {sms ? "texts" : "emails"}. These choices do not change your answers.
      </p>
      <p className="mt-3 text-sm">
        This branch records a choice only. {sms ? "SMS" : "Email"} delivery is
        not connected, and recorded permission is not yet qualified for sending.
        This does not permit {sms ? "email" : "SMS"} or marketing.
      </p>
      {!value && continuedRole ? (
        <Button className="mt-6" disabled={busy} onClick={load}>
          {busy ? "Loading…" : `Load current ${channelName} choices`}
        </Button>
      ) : !value ? (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            load()
          }}
        >
          <p className="mt-4">
            Enter the invitation recipient’s first name and role again. This is
            not identity verification.
          </p>
          <label htmlFor="email-recipient-name" className="mt-4 block">
            Recipient first name
          </label>
          <input
            id="email-recipient-name"
            value={firstName}
            maxLength={120}
            autoComplete="off"
            disabled={busy}
            onChange={(event) => setFirstName(event.target.value)}
            className="mt-2 w-full rounded border bg-white p-3 text-base"
          />
          <label htmlFor="email-recipient-role" className="mt-4 block">
            This invitation was sent to
          </label>
          <select
            id="email-recipient-role"
            value={role}
            disabled={busy}
            onChange={(event) =>
              setRole(event.target.value as PatientConfirmation["role"])
            }
            className="mt-2 w-full rounded border bg-white p-3 text-base"
          >
            <option value="patient">The patient</option>
            <option value="caregiver">
              A caregiver answering for the patient
            </option>
          </select>
          <Button
            type="submit"
            className="mt-6"
            disabled={busy || !firstName.trim()}
          >
            {busy ? "Checking…" : `Load current ${channelName} choices`}
          </Button>
        </form>
      ) : (
        <>
          <p role="status" className="mt-6 font-medium">
            {sms
              ? labels[value.status]
                  .replaceAll("email", "SMS")
                  .replaceAll("Email", "SMS")
              : labels[value.status]}
          </p>
          <p className="mt-2 text-sm">
            Checked at {value.checked_at}. Reload before relying on this
            snapshot.
          </p>
          {value.expires_at && (
            <p className="mt-2 text-sm">Recorded expiry: {value.expires_at}</p>
          )}
          <h2 className="mt-6 text-lg font-semibold">
            {sms
              ? "Invitation and reminder text permission"
              : "Invitation email permission"}
          </h2>
          <p className="mt-3 break-words whitespace-pre-wrap">
            {value.grant_disclosure}
          </p>
          <h2 className="mt-6 text-lg font-semibold">
            Decline or withdraw permission
          </h2>
          <p className="mt-3 break-words whitespace-pre-wrap">
            {value.withdrawal_disclosure}
          </p>
          {value.can_grant && (
            <>
              <label className="mt-6 flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={recipientAttested}
                  disabled={busy}
                  onChange={(event) =>
                    setRecipientAttested(event.target.checked)
                  }
                  className="mt-1"
                />
                <span>
                  I reviewed these disclosures and confirm I am the intended
                  recipient, authorized to receive{" "}
                  {sms ? "invitations and reminders" : "invitations"} at the{" "}
                  {contactName} already provided.
                </span>
              </label>
              {value.actor_role === "caregiver" && (
                <label className="mt-4 flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={caregiverAttested}
                    disabled={busy}
                    onChange={(event) =>
                      setCaregiverAttested(event.target.checked)
                    }
                    className="mt-1"
                  />
                  <span>
                    I am authorized to make this communication choice for the
                    patient. This is my attestation, not independent
                    verification.
                  </span>
                </label>
              )}
            </>
          )}
          {sms && "contact_available" in value && !value.contact_available && (
            <p className="mt-6">
              No mobile number was provided for this preparation. You can
              continue without texts.
            </p>
          )}
          {sms &&
            "phone_suppression_status" in value &&
            value.phone_suppression_status === "stopped" && (
              <p className="mt-6" role="status">
                A phone-wide text opt-out is recorded for this sender and mobile
                number. You cannot clear it by allowing texts for this
                preparation. You can continue preparing for your visit without
                texts; email choices are separate.
              </p>
            )}
          {value.can_grant ? (
            <>
              {sms ? (
                <p className="mt-6 text-sm">
                  Permission ends no later than{" "}
                  {new Date(value.maximum_expires_at).toLocaleString()}.
                  Rescheduling does not automatically extend a recorded grant.
                </p>
              ) : (
                <>
                  <label htmlFor="email-expiry" className="mt-6 block">
                    Permission expiry (UTC)
                  </label>
                  <input
                    id="email-expiry"
                    type="datetime-local"
                    value={expiry}
                    disabled={busy}
                    onChange={(event) => setExpiry(event.target.value)}
                    className="mt-2 w-full rounded border bg-white p-3 text-base"
                  />
                  <p className="mt-2 text-sm">
                    Choose a future UTC time no later than{" "}
                    {value.maximum_expires_at}.
                  </p>
                </>
              )}
              <Button
                className="mt-4"
                disabled={!canGrant}
                onClick={() => decide("granted")}
              >
                Record permission choice
              </Button>
            </>
          ) : (
            <p className="mt-6">
              {sms && "contact_available" in value && !value.contact_available
                ? "Text permission is unavailable because no mobile number was provided."
                : sms &&
                    "phone_suppression_status" in value &&
                    value.phone_suppression_status === "stopped"
                  ? "New text permission cannot be recorded while this phone-wide opt-out is in place."
                  : "New permission cannot be recorded for this completed or expired preparation. You can still withdraw through a valid invitation."}
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-3">
            <Button
              variant="outline"
              disabled={
                !canDecide ||
                (sms &&
                  "contact_available" in value &&
                  !value.contact_available)
              }
              onClick={() => decide("withdrawn")}
            >
              Do not allow / withdraw {channelName}{" "}
              {sms ? "texts" : "invitations"}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={clear}>
              Reload {channelName} choices
            </Button>
          </div>
          <p className="mt-3 text-sm">
            Declining or withdrawing does not require the permission checkboxes
            or an expiry.
          </p>
        </>
      )}
      {message && (
        <p role="status" className="mt-6 text-sm">
          {message}
        </p>
      )}
      <Button
        variant="ghost"
        className="mt-6"
        disabled={busy}
        onClick={onClose}
      >
        Back to preparation
      </Button>
    </section>
  )
}
