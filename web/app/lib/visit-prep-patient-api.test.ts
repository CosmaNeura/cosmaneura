import { describe, expect, it, vi } from "vitest"

import {
  VisitPrepPatientFailure,
  createVisitPrepPatientClient,
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

describe("patient public API boundary", () => {
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
