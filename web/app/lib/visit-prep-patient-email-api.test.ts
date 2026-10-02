import { describe, expect, it, vi } from "vitest"

import {
  createVisitPrepPatientClient,
  visitPrepPatientEmailConsent,
  visitPrepPatientEmailConsentReceipt,
  visitPrepPatientSMSConsent,
} from "./visit-prep-patient-api"

const token = `vp1_${"A".repeat(43)}`
const choice = {
  contract: "visit_prep.patient_email_consent.v1",
  actor_role: "patient",
  disclosure_version: "visit_prep_email_consent_v1",
  grant_disclosure: "Synthetic grant.",
  grant_disclosure_fingerprint: "a".repeat(64),
  withdrawal_disclosure: "Synthetic withdrawal.",
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
const receipt = {
  event_sequence: 1,
  current_event_sequence: 1,
  event_kind: "withdrawn",
  expires_at: null,
  recorded_at: "2099-01-02T12:00:00Z",
  disposition: "recorded",
}
describe("patient email public contract", () => {
  it("separates SMS from email contracts and requires honest missing-contact state", async () => {
    const sms = {
      ...choice,
      contract: "visit_prep.patient_sms_consent.v1",
      disclosure_version: "visit_prep_sms_consent_v1",
      contact_available: true,
      phone_suppression_status: "not_connected",
    }
    expect(visitPrepPatientSMSConsent.safeParse(choice).success).toBe(false)
    expect(visitPrepPatientEmailConsent.safeParse(sms).success).toBe(false)
    expect(visitPrepPatientSMSConsent.safeParse(sms).success).toBe(true)
    expect(
      visitPrepPatientSMSConsent.safeParse({
        ...sms,
        phone_suppression_status: "stopped",
      }).success,
    ).toBe(false)
    expect(
      visitPrepPatientSMSConsent.safeParse({
        ...sms,
        phone_suppression_status: "stopped",
        can_grant: false,
      }).success,
    ).toBe(true)
    expect(
      visitPrepPatientSMSConsent.safeParse({
        ...sms,
        phone_suppression_status: "cleared",
      }).success,
    ).toBe(false)
    expect(
      visitPrepPatientSMSConsent.safeParse({ ...sms, contact_available: false })
        .success,
    ).toBe(false)
    expect(
      visitPrepPatientSMSConsent.safeParse({
        ...sms,
        contact_available: false,
        can_grant: false,
      }).success,
    ).toBe(true)
    expect(
      visitPrepPatientSMSConsent.safeParse({ ...sms, phone: "+12025550123" })
        .success,
    ).toBe(false)
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(sms)))
      .mockResolvedValueOnce(new Response(JSON.stringify(receipt)))
    const client = createVisitPrepPatientClient(
      "https://api.example.test",
      token,
      fetcher,
    )
    expect(await client.readSMSConsent({ role: "patient" })).toEqual(sms)
    await client.decideSMSConsent(
      {
        role: "patient",
        expected_event_sequence: 0,
        event_kind: "withdrawn",
        disclosure_fingerprint: sms.withdrawal_disclosure_fingerprint,
        expires_at: null,
        recipient_control_attested: false,
        caregiver_authority_attested: false,
      },
      "00000000-0000-4000-8000-000000000001",
    )
    expect(fetcher.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
      "/external/v1/visit-prep/sms-consent",
      "/external/v1/visit-prep/sms-consent/decision",
    ])
  })
  it("uses separate POSTs with fresh confirmation and an exact decision UUID, never credentials in URLs", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(choice)))
      .mockResolvedValueOnce(new Response(JSON.stringify(receipt)))
    const client = createVisitPrepPatientClient(
      "https://api.example.test",
      token,
      fetcher,
    )
    const confirmation = { first_name: "José", role: "patient" as const }
    expect(await client.readEmailConsent(confirmation)).toEqual(choice)
    const payload = {
      ...confirmation,
      expected_event_sequence: 0,
      event_kind: "withdrawn" as const,
      expires_at: null,
      disclosure_fingerprint: "b".repeat(64),
      recipient_control_attested: true,
      caregiver_authority_attested: false,
    }
    expect(
      await client.decideEmailConsent(
        payload,
        "00000000-0000-4000-8000-000000000001",
      ),
    ).toEqual(receipt)
    expect(fetcher.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
      "/external/v1/visit-prep/email-consent",
      "/external/v1/visit-prep/email-consent/decision",
    ])
    for (const [url, options] of fetcher.mock.calls) {
      expect(String(url)).not.toContain(token)
      expect(options).toMatchObject({
        method: "POST",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        headers: { Authorization: `Bearer ${token}` },
      })
      expect(JSON.parse(options.body)).toMatchObject(confirmation)
    }
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(payload)
    expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).toBe(
      "00000000-0000-4000-8000-000000000001",
    )
    client.dispose()
    await expect(client.readEmailConsent(confirmation)).rejects.toMatchObject({
      code: "unavailable",
    })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it.each([
    { email: "private@example.invalid" },
    { sending_authority: "qualified" },
    { delivery_status: "sent" },
    { identity_assurance: "verified_patient" },
    { disclosure_version: "unknown" },
    { event_sequence: 1 },
    { status: "withdrawn" },
    { can_grant: true, maximum_expires_at: "2098-01-01T00:00:00Z" },
    { grant_disclosure_fingerprint: "b".repeat(64) },
    { grant_disclosure: "x".repeat(8001) },
    { expires_at: "2099-01-02T00:00:00Z" },
  ])("rejects malformed or authority-inflated current state", (extra) => {
    expect(
      visitPrepPatientEmailConsent.safeParse({ ...choice, ...extra }).success,
    ).toBe(false)
  })
  it("accepts current expired, granted and withdrawn states without inventing authority", () => {
    const recorded = {
      ...choice,
      event_sequence: 1,
      recorded_at: "2098-12-31T12:00:00Z",
    }
    expect(
      visitPrepPatientEmailConsent.safeParse({
        ...recorded,
        status: "grant_recorded",
        expires_at: "2099-01-10T12:00:00Z",
      }).success,
    ).toBe(true)
    expect(
      visitPrepPatientEmailConsent.safeParse({
        ...recorded,
        status: "expired",
        expires_at: "2098-12-31T12:00:00Z",
      }).success,
    ).toBe(true)
    expect(
      visitPrepPatientEmailConsent.safeParse({
        ...recorded,
        status: "withdrawn",
        can_grant: false,
      }).success,
    ).toBe(true)
    expect(
      visitPrepPatientEmailConsent.safeParse({
        ...recorded,
        status: "grant_recorded",
        expires_at: "2098-12-31T12:00:00Z",
      }).success,
    ).toBe(false)
  })
  it.each([
    { current_event_sequence: 0 },
    { event_sequence: 0 },
    { expires_at: "2099-01-02T00:00:00Z" },
    { event_kind: "granted" },
    { current_event_sequence: 2 },
    { actor_id: "private" },
  ])("rejects invalid decision receipts", (extra) => {
    expect(
      visitPrepPatientEmailConsentReceipt.safeParse({ ...receipt, ...extra })
        .success,
    ).toBe(false)
  })
  it("permits historical replay only as a receipt, including a later stream sequence", () => {
    expect(
      visitPrepPatientEmailConsentReceipt.safeParse({
        ...receipt,
        disposition: "replayed",
        current_event_sequence: 2,
      }).success,
    ).toBe(true)
  })
})
