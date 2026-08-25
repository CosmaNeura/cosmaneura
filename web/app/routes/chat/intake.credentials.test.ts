import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { getNextQuestion } from "~/components/chat/intake/IntakeRuntimeProvider"

import { clientLoader } from "./intake"

vi.mock("~/utils/environment", () => ({
  requireEnv: () => "https://app.example.test",
}))

describe("public intake cross-origin requests", () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset().mockResolvedValue(
      new Response(
        JSON.stringify({ question: "Next?", status: "processing" }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("includes the platform session when loading an intake", async () => {
    await clientLoader({ params: { id: "intake_public_123" } } as never)

    expect(fetchMock).toHaveBeenCalledWith(
      "https://app.example.test/external/chat/intake/intake_public_123/question",
      { credentials: "include" },
    )
  })

  it("includes the platform session when submitting an answer", async () => {
    await getNextQuestion(
      "https://app.example.test/external/chat/intake/intake_public_123/question",
      "An answer",
    )

    expect(fetchMock).toHaveBeenCalledWith(
      "https://app.example.test/external/chat/intake/intake_public_123/question",
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: "An answer" }),
      },
    )
  })
})
