import { describe, expect, it, vi } from "vitest"

import {
  VisitPrepPatientFailure,
  createVisitPrepPatientClient,
  visitPrepPatientContribution,
  visitPrepPatientState,
} from "./visit-prep-patient-api"

const token = `vp1_${"A".repeat(43)}`
const invited = {
  status: "confirmation_required",
  physician_display_name: null,
  practice_display_name: null,
  scheduled_at: null,
  timezone_name: null,
  submitted_questions: 0,
  maximum_questions: 5,
  current_question: null,
}
const contribution = {
  basis: { original_response_count: 4, event_sequence: 0 },
  actor_role: "patient",
  original_interview_completed: true,
  current_answers_complete: true,
  current_answers_reviewed: false,
  topics: [
    {
      question: {
        key: "story",
        position: 5,
        prompt: "Tell me your story",
        helper_text: null,
        kind: "story",
        choices: [],
      },
      response_kind: "answered",
      answer: "Invented story.",
      initial_response_kind: "answered",
      initial_answer: "Invented story.",
      changed: false,
      recorded_at: "2099-01-01T12:00:00Z",
    },
  ],
}

describe("patient public API boundary", () => {
  it("reads saved answers only by POST with fresh recipient fields and keeps revision receipts value-free", async () => {
    const receipt = {
      disposition: "accepted",
      basis: { original_response_count: 4, event_sequence: 1 },
      current_answers_complete: true,
      current_answers_reviewed: false,
    }
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(contribution)))
      .mockResolvedValueOnce(new Response(JSON.stringify(receipt)))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...receipt, current_answers_reviewed: true }),
        ),
      )
    const client = createVisitPrepPatientClient(
      "https://api.example.test",
      token,
      fetcher,
    )
    const confirmation = { first_name: "José", role: "patient" as const }
    expect(await client.readContribution(confirmation)).toEqual(contribution)
    const change = {
      ...confirmation,
      basis: contribution.basis,
      question_key: "story",
      response_kind: "skipped" as const,
      answer: null,
    }
    await client.correctContribution(
      change,
      "00000000-0000-4000-8000-000000000001",
    )
    await client.reviewContribution(
      { ...confirmation, basis: receipt.basis },
      "00000000-0000-4000-8000-000000000002",
    )
    expect(fetcher.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
      "/external/v1/visit-prep/contribution",
      "/external/v1/visit-prep/contribution/response",
      "/external/v1/visit-prep/contribution/review",
    ])
    for (const [url, options] of fetcher.mock.calls) {
      expect(String(url)).not.toContain(token)
      expect(options).toMatchObject({
        method: "POST",
        cache: "no-store",
        referrerPolicy: "no-referrer",
      })
      expect(JSON.parse(options.body)).toMatchObject(confirmation)
    }
    client.dispose()
    await expect(client.readContribution(confirmation)).rejects.toMatchObject({
      code: "unavailable",
    })
  })

  it.each([
    { owner_practice_member_id: "unexpected-private-owner" },
    { current_answers_complete: false },
    { topics: [contribution.topics[0], contribution.topics[0]] },
    { topics: [{ ...contribution.topics[0], response_kind: "skipped" }] },
    { topics: [{ ...contribution.topics[0], changed: true }] },
    {
      topics: [
        {
          ...contribution.topics[0],
          question: { ...contribution.topics[0].question, kind: "choice" },
        },
      ],
    },
    { basis: { original_response_count: 0, event_sequence: 1 } },
  ])("rejects malformed or overbroad saved-answer payloads", (extra) => {
    expect(
      visitPrepPatientContribution.safeParse({ ...contribution, ...extra })
        .success,
    ).toBe(false)
  })

  it("sends the bearer only in its header and includes existing cookies solely for support-view denial", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(invited), { status: 200 }))
    const client = createVisitPrepPatientClient(
      "https://api.example.test/",
      token,
      fetcher,
    )
    expect(await client.resume()).toEqual(invited)
    const [url, options] = fetcher.mock.calls[0]
    expect(String(url)).toBe("https://api.example.test/external/v1/visit-prep")
    expect(String(url)).not.toContain(token)
    expect(options).toMatchObject({
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: { Authorization: `Bearer ${token}` },
    })
    client.dispose()
    await expect(client.resume()).rejects.toMatchObject({ code: "unavailable" })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it("preserves the caller's exact response and idempotency key across retries", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error(`secret ${token}`))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ interview: invited, disposition: "replayed" }),
          { status: 200 },
        ),
      )
    const client = createVisitPrepPatientClient(
      "https://api.example.test",
      token,
      fetcher,
    )
    const payload = {
      question_key: "reason",
      response_kind: "answered" as const,
      answer: "Synthetic story",
    }
    const key = "00000000-0000-4000-8000-000000000001"
    await expect(client.submit(payload, key)).rejects.toEqual(
      new VisitPrepPatientFailure("temporary"),
    )
    await client.submit(payload, key)
    const [, first] = fetcher.mock.calls[0]
    const [, second] = fetcher.mock.calls[1]
    expect(first.body).toBe(second.body)
    expect(first.headers["Idempotency-Key"]).toBe(
      second.headers["Idempotency-Key"],
    )
  })

  it.each([
    [403, "unavailable"],
    [404, "unavailable"],
    [409, "conflict"],
    [422, "invalid"],
    [429, "limited"],
    [503, "temporary"],
  ])(
    "maps status %s without reading arbitrary error content",
    async (status, code) => {
      const json = vi.fn()
      const fetcher = vi.fn().mockResolvedValue({ ok: false, status, json })
      const client = createVisitPrepPatientClient(
        "https://api.example.test",
        token,
        fetcher,
      )
      await expect(client.resume()).rejects.toMatchObject({
        code,
        message: "Visit Prep request unavailable",
      })
      expect(json).not.toHaveBeenCalled()
    },
  )

  it("fails closed on malformed state or preconfirmation identity disclosure", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...invited,
          physician_display_name: "Unexpected identity",
        }),
        { status: 200 },
      ),
    )
    const client = createVisitPrepPatientClient(
      "https://api.example.test",
      token,
      fetcher,
    )
    await expect(client.resume()).rejects.toMatchObject({ code: "temporary" })
    expect(
      visitPrepPatientState.safeParse({ ...invited, status: "in_progress" })
        .success,
    ).toBe(false)
  })

  it.each([
    "https://evil:secret@api.example.test",
    "https://api.example.test/?token=secret",
    "https://api.example.test/base",
    "http://api.example.test",
  ])("rejects unsafe configured API bases", (base) => {
    expect(() => createVisitPrepPatientClient(base, token, vi.fn())).toThrow(
      VisitPrepPatientFailure,
    )
  })

  it("aborts pending requests and refuses late results after disposal", async () => {
    let resolve!: (value: Response) => void
    const fetcher = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((done) => {
          resolve = done
        }),
    )
    const client = createVisitPrepPatientClient(
      "https://api.example.test",
      token,
      fetcher,
    )
    const result = client.resume()
    client.dispose()
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true)
    resolve(new Response(JSON.stringify(invited), { status: 200 }))
    await expect(result).rejects.toMatchObject({ code: "temporary" })
  })
})
