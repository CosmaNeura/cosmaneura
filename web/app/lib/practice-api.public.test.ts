import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  acceptPublicPracticeInvitation,
  createPublicPracticeInvitation,
  deactivatePublicPracticeMember,
  reactivatePublicPracticeMember,
} from "./practice-api"

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  getToken: vi.fn(async () => "test-session-token"),
}))

vi.mock("~/configuration/clerk", () => ({
  getSessionToken: () => mocks.getToken(),
}))

vi.mock("~/lib/billing-recovery", () => ({
  internalApiFetch: mocks.fetch,
  safeBillingReturnTo: (value: string | null | undefined) => value ?? "/notes",
}))

vi.mock("~/utils/environment", () => ({
  requireEnv: () => "https://api.example",
}))

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

const memberResponse = {
  member_id: "member_test",
  email: "clinician@example.test",
  role: "workforce",
  seat_state: "active",
  credential_state: "pending",
  access_revoked: false,
}

describe("public onboarding practice API", () => {
  beforeEach(() => {
    mocks.fetch.mockReset()
    mocks.getToken.mockReset().mockResolvedValue("test-session-token")
  })

  it("sends the invitation idempotency key only in the authenticated request header", async () => {
    mocks.fetch.mockResolvedValueOnce(
      jsonResponse(
        {
          invitation_id: "invitation_test",
          invitee_email: "clinician@example.test",
          status: "pending",
          role: "workforce",
          issued_at: "2026-08-27T12:00:00Z",
          expires_at: "2026-09-03T12:00:00Z",
          invite_token: "a".repeat(43),
          reused_idempotency_key: false,
        },
        201,
      ),
    )

    await createPublicPracticeInvitation(
      { email: "clinician@example.test", expires_in_days: 7 },
      "request-key-123",
    )

    expect(mocks.fetch).toHaveBeenCalledWith(
      "https://api.example/internal/v1/practice/invitations",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: {
          Authorization: "Bearer test-session-token",
          "Content-Type": "application/json",
          "Idempotency-Key": "request-key-123",
        },
        body: JSON.stringify({
          email: "clinician@example.test",
          expires_in_days: 7,
        }),
      }),
    )
  })

  it("omits the optional invitation expiry so the API default can apply", async () => {
    mocks.fetch.mockResolvedValueOnce(
      jsonResponse(
        {
          invitation_id: "invitation_default_expiry",
          invitee_email: "clinician@example.test",
          status: "pending",
          role: "workforce",
          issued_at: "2026-08-27T12:00:00Z",
          expires_at: "2026-09-03T12:00:00Z",
          invite_token: "a".repeat(43),
          reused_idempotency_key: false,
        },
        201,
      ),
    )

    await createPublicPracticeInvitation(
      { email: "clinician@example.test" },
      "request-key-default-expiry",
    )

    expect(mocks.fetch).toHaveBeenCalledWith(
      "https://api.example/internal/v1/practice/invitations",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ email: "clinician@example.test" }),
      }),
    )
  })

  it("uses only the canonical invitation and seat mutation routes", async () => {
    mocks.fetch
      .mockResolvedValueOnce(jsonResponse(memberResponse))
      .mockResolvedValueOnce(
        jsonResponse({ ...memberResponse, seat_state: "inactive" }),
      )
      .mockResolvedValueOnce(jsonResponse(memberResponse))

    await acceptPublicPracticeInvitation({
      token: "a".repeat(43),
      attestation_name: "Test Clinician",
      checks: ["workforce", "not-party", "direction", "phi"],
    })
    await deactivatePublicPracticeMember("member_test")
    await reactivatePublicPracticeMember("member_test")

    expect(mocks.fetch.mock.calls.map(([url]) => url)).toEqual([
      "https://api.example/internal/v1/practice/invitations/accept",
      "https://api.example/internal/v1/practice/members/member_test/deactivate",
      "https://api.example/internal/v1/practice/members/member_test/reactivate",
    ])
    expect(mocks.fetch.mock.calls.map(([, init]) => init?.method)).toEqual([
      "POST",
      "POST",
      "POST",
    ])
  })

  it("does not start an API request while sign-in redirection is in progress", async () => {
    const redirectError = new Error("redirecting to sign in")
    mocks.getToken.mockRejectedValueOnce(redirectError)

    await expect(
      createPublicPracticeInvitation(
        { email: "clinician@example.test", expires_in_days: 7 },
        "request-key-redirect",
      ),
    ).rejects.toBe(redirectError)
    expect(mocks.fetch).not.toHaveBeenCalled()
  })
})
