import { describe, expect, it, vi } from "vitest"

import {
  type PatientEmailConsent as EmailConsentState,
  VisitPrepPatientFailure,
} from "~/lib/visit-prep-patient-api"

import { PatientEmailConsent } from "./PatientEmailConsent"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const consent: EmailConsentState = {
  contract: "visit_prep.patient_email_consent.v1",
  actor_role: "caregiver",
  disclosure_version: "visit_prep_email_consent_v1",
  grant_disclosure:
    "Synthetic optional email disclosure, not SMS or marketing.",
  grant_disclosure_fingerprint: "a".repeat(64),
  withdrawal_disclosure:
    "Synthetic withdrawal disclosure. Answers are unchanged.",
  withdrawal_disclosure_fingerprint: "b".repeat(64),
  status: "not_recorded",
  event_sequence: 0,
  recorded_at: null,
  expires_at: null,
  maximum_expires_at: "2099-01-15T15:00:00Z",
  can_grant: true,
  checked_at: "2099-01-01T12:00:00Z",
  identity_assurance: "invitation_bearer",
  sending_authority: "not_qualified",
  delivery_status: "not_connected",
}
function setup(
  value = consent,
  continuedRole: "patient" | "caregiver" | null = null,
) {
  const client = {
    resume: vi.fn(),
    confirm: vi.fn(),
    submit: vi.fn(),
    complete: vi.fn(),
    reportWrongRecipient: vi.fn(),
    dispose: vi.fn(),
    readContribution: vi.fn(),
    correctContribution: vi.fn(),
    explainContribution: vi.fn(),
    reviewContribution: vi.fn(),
    readClarification: vi.fn(),
    decideClarification: vi.fn(),
    readFollowup: vi.fn(),
    answerFollowup: vi.fn(),
    readEmailConsent: vi.fn().mockResolvedValue(value),
    decideEmailConsent: vi.fn(),
  }
  const props = { client, onClose: vi.fn(), onUnavailable: vi.fn() }
  render(<PatientEmailConsent {...props} continuedRole={continuedRole} />)
  return props
}
async function load(role = "caregiver") {
  fireEvent.change(screen.getByLabelText("Recipient first name"), {
    target: { value: "José" },
  })
  fireEvent.change(screen.getByLabelText("This invitation was sent to"), {
    target: { value: role },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Load current email choices" }),
  )
  await screen.findByText(/No email choice recorded at last load/)
}
function attest() {
  fireEvent.click(screen.getByLabelText(/I reviewed these disclosures/))
  fireEvent.click(screen.getByLabelText(/I am authorized to make/))
}
function receipt(extra = {}) {
  return {
    event_sequence: 1,
    current_event_sequence: 1,
    event_kind: "granted",
    expires_at: "2099-01-10T12:00:00Z",
    recorded_at: "2099-01-02T12:00:00Z",
    disposition: "recorded",
    ...extra,
  }
}
describe("optional patient email choice", () => {
  it("loads with remembered confirmation without repeating the identity check", async () => {
    const { client } = setup(consent, "caregiver")
    await screen.findByText(/No email choice recorded at last load/)
    expect(client.readEmailConsent).toHaveBeenCalledWith({ role: "caregiver" })
    expect(
      screen.queryByLabelText("Recipient first name"),
    ).not.toBeInTheDocument()
  })
  it("leaves the interview accessible when the independent capture control is unavailable", async () => {
    const { client, onClose } = setup()
    client.readEmailConsent.mockRejectedValueOnce(
      new VisitPrepPatientFailure("conflict"),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Load current email choices" }),
    )
    await screen.findByText(/Email choices could not be confirmed/)
    expect(
      screen.queryByRole("button", { name: "Record permission choice" }),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Back to preparation" }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(client.decideEmailConsent).not.toHaveBeenCalled()
  })
  it("discards a late decision acknowledgment after concealment without automatic retries", async () => {
    const { client } = setup()
    await load()
    let resolve!: (value: ReturnType<typeof receipt>) => void
    client.decideEmailConsent.mockImplementationOnce(
      () =>
        new Promise<ReturnType<typeof receipt>>((done) => {
          resolve = done
        }),
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Do not allow / withdraw email invitations",
      }),
    )
    fireEvent(window, new Event("blur"))
    await act(async () =>
      resolve(receipt({ event_kind: "withdrawn", expires_at: null })),
    )
    expect(
      screen.queryByText(/Your decision was recorded/),
    ).not.toBeInTheDocument()
    expect(screen.getByLabelText("Recipient first name")).toHaveValue("")
    expect(client.decideEmailConsent).toHaveBeenCalledOnce()
    await load()
    expect(client.decideEmailConsent).toHaveBeenCalledOnce()
  })
  it("does not read or decide on mount or when returning without choosing", () => {
    const { client, onClose } = setup()
    expect(client.readEmailConsent).not.toHaveBeenCalled()
    expect(client.decideEmailConsent).not.toHaveBeenCalled()
    expect(screen.getByText(/without allowing emails/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Back to preparation" }))
    expect(onClose).toHaveBeenCalledOnce()
  })
  it("requires fresh confirmation, separate caregiver attestation and bounded explicit UTC expiry", async () => {
    const { client } = setup()
    await load()
    expect(client.readEmailConsent).toHaveBeenCalledWith({
      first_name: "José",
      role: "caregiver",
    })
    const grant = screen.getByRole("button", {
      name: "Record permission choice",
    })
    expect(grant).toBeDisabled()
    fireEvent.click(screen.getByLabelText(/I reviewed these disclosures/))
    fireEvent.change(screen.getByLabelText("Permission expiry (UTC)"), {
      target: { value: "2099-01-10T12:00" },
    })
    expect(grant).toBeDisabled()
    fireEvent.click(screen.getByLabelText(/I am authorized to make/))
    expect(grant).toBeEnabled()
    for (const invalid of ["2099-01-16T12:00", "2098-12-31T12:00", ""]) {
      fireEvent.change(screen.getByLabelText("Permission expiry (UTC)"), {
        target: { value: invalid },
      })
      expect(grant).toBeDisabled()
    }
    fireEvent.change(screen.getByLabelText("Permission expiry (UTC)"), {
      target: { value: "2099-01-10T12:00" },
    })
    client.decideEmailConsent.mockResolvedValue(receipt())
    fireEvent.click(grant)
    await screen.findByText(/Your decision was recorded/)
    expect(client.decideEmailConsent).toHaveBeenCalledOnce()
    const [payload, key] = client.decideEmailConsent.mock.calls[0]
    expect(payload).toEqual({
      first_name: "José",
      role: "caregiver",
      expected_event_sequence: 0,
      event_kind: "granted",
      disclosure_fingerprint: "a".repeat(64),
      expires_at: "2099-01-10T12:00:00.000Z",
      recipient_control_attested: true,
      caregiver_authority_attested: true,
    })
    expect(key).toMatch(/^[0-9a-f-]{14}4[0-9a-f-]{21}$/)
    expect(
      screen.queryByText(/No email choice recorded at last load/),
    ).not.toBeInTheDocument()
    expect(screen.getByLabelText("Recipient first name")).toHaveValue("")
    expect(client.readEmailConsent).toHaveBeenCalledOnce()
    expect(client.submit).not.toHaveBeenCalled()
  })
  it("can decline without an expiry and withdraw after completion without granting", async () => {
    const { client } = setup({ ...consent, can_grant: false })
    await load()
    expect(
      screen.queryByLabelText("Permission expiry (UTC)"),
    ).not.toBeInTheDocument()
    client.decideEmailConsent.mockResolvedValue(
      receipt({ event_kind: "withdrawn", expires_at: null }),
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Do not allow / withdraw email invitations",
      }),
    )
    await screen.findByText(/Your decision was recorded/)
    expect(client.decideEmailConsent.mock.calls[0][0]).toMatchObject({
      event_kind: "withdrawn",
      expires_at: null,
      disclosure_fingerprint: "b".repeat(64),
      expected_event_sequence: 0,
      recipient_control_attested: false,
      caregiver_authority_attested: false,
    })
  })
  it.each(["temporary", "conflict", "limited"] as const)(
    "clears an uncertain %s decision and recovers only by explicit read",
    async (code) => {
      const { client } = setup()
      await load()
      attest()
      client.decideEmailConsent.mockRejectedValue(
        new VisitPrepPatientFailure(code),
      )
      fireEvent.click(
        screen.getByRole("button", {
          name: "Do not allow / withdraw email invitations",
        }),
      )
      await waitFor(() =>
        expect(screen.getByLabelText("Recipient first name")).toHaveValue(""),
      )
      expect(client.decideEmailConsent).toHaveBeenCalledOnce()
      expect(
        screen.queryByRole("button", { name: /Retry/ }),
      ).not.toBeInTheDocument()
      await load()
      expect(client.readEmailConsent).toHaveBeenCalledTimes(2)
      expect(client.decideEmailConsent).toHaveBeenCalledOnce()
    },
  )
  it.each([
    { event_kind: "withdrawn", expires_at: null },
    { event_sequence: 2 },
    { expires_at: "2099-01-12T12:00:00Z" },
  ])("never claims confirmation for a divergent receipt", async (extra) => {
    const { client } = setup()
    await load()
    attest()
    fireEvent.change(screen.getByLabelText("Permission expiry (UTC)"), {
      target: { value: "2099-01-10T12:00" },
    })
    client.decideEmailConsent.mockResolvedValue(receipt(extra))
    fireEvent.click(
      screen.getByRole("button", { name: "Record permission choice" }),
    )
    await screen.findByText(/A choice may have been saved/)
    expect(
      screen.queryByText(/Your decision was recorded/),
    ).not.toBeInTheDocument()
  })
  it("ignores late reads and clears confirmation and checked boxes on focus loss", async () => {
    const { client } = setup()
    let resolve!: (value: EmailConsentState) => void
    client.readEmailConsent.mockImplementationOnce(
      () =>
        new Promise<EmailConsentState>((done) => {
          resolve = done
        }),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Load current email choices" }),
    )
    fireEvent(window, new Event("blur"))
    await act(async () => resolve({ ...consent, actor_role: "patient" }))
    expect(screen.getByLabelText("Recipient first name")).toHaveValue("")
    expect(screen.queryByText(consent.grant_disclosure)).not.toBeInTheDocument()
    await load()
    attest()
    fireEvent(window, new Event("blur"))
    expect(screen.queryByText(consent.grant_disclosure)).not.toBeInTheDocument()
    await load()
    expect(
      screen.getByLabelText(/I reviewed these disclosures/),
    ).not.toBeChecked()
    expect(screen.getByLabelText(/I am authorized to make/)).not.toBeChecked()
  })
  it("propagates access denial and rejects a role-divergent read", async () => {
    const { client, onUnavailable } = setup()
    client.readEmailConsent.mockResolvedValueOnce({
      ...consent,
      actor_role: "patient",
    })
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.change(screen.getByLabelText("This invitation was sent to"), {
      target: { value: "caregiver" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Load current email choices" }),
    )
    await screen.findByText(/A choice may have been saved/)
    client.readEmailConsent.mockRejectedValueOnce(
      new VisitPrepPatientFailure("unavailable"),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Load current email choices" }),
    )
    await waitFor(() => expect(onUnavailable).toHaveBeenCalledOnce())
    expect(screen.queryByText(consent.grant_disclosure)).not.toBeInTheDocument()
  })
})
