import { afterEach, describe, expect, it, vi } from "vitest"

import { IntakeRuntimeProvider } from "./IntakeRuntimeProvider"
import { IntakeThread } from "./IntakeThread"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const API = "https://api.example.test/external/chat/intake/intake_test/question"

function respond(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
}

function renderChat(setIsFinished = vi.fn(), retrySafe = true) {
  render(
    <IntakeRuntimeProvider
      api={API}
      retrySafe={retrySafe}
      setIsFinished={setIsFinished}
      initialMessages={[
        { role: "assistant", content: "Synthetic first question" },
      ]}
    >
      <IntakeThread />
    </IntakeRuntimeProvider>,
  )
}

function answer(text: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole("button", { name: /Send/ }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("intake chat", () => {
  it("shows a typing indicator and a send-only busy state while waiting", async () => {
    let resolve!: (value: Response) => void
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((r) => (resolve = r))),
    )
    renderChat()

    answer("Synthetic answer")

    expect(
      await screen.findByRole("status", {
        name: "Preparing the next question",
      }),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Sending" })).toBeDisabled()
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull()

    resolve(
      new Response(
        JSON.stringify({
          question: "Synthetic second question",
          status: "processing",
        }),
      ),
    )
    expect(await screen.findByText("Synthetic second question")).toBeVisible()
    expect(
      screen.queryByRole("status", { name: "Preparing the next question" }),
    ).toBeNull()
  })

  it("resends once after checking the server is still on the same question", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockImplementationOnce(() =>
        respond({
          question: "Synthetic first question",
          status: "in_progress",
          retry_safe: true,
        }),
      )
      .mockImplementationOnce(() =>
        respond({
          question: "Synthetic second question",
          status: "processing",
        }),
      )
    vi.stubGlobal("fetch", fetch)
    renderChat()

    answer("Synthetic answer")

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We couldn't confirm that sent. Your answer is still here.",
    )
    expect(screen.getAllByText("Synthetic answer")).toHaveLength(1)
    expect(screen.getByRole("textbox", { name: "Your answer" })).toBeDisabled()

    fireEvent.click(screen.getByRole("button", { name: "Try again" }))

    expect(await screen.findByText("Synthetic second question")).toBeVisible()
    expect(screen.queryByRole("alert")).toBeNull()
    expect(screen.getAllByText("Synthetic answer")).toHaveLength(1)
    const posts = fetch.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    )
    expect(posts).toHaveLength(2)
    for (const [, init] of posts) {
      expect(JSON.parse((init as RequestInit).body as string)).toEqual({
        answer: "Synthetic answer",
        expected_question: "Synthetic first question",
      })
    }
  })

  it("never resends an ambiguous answer to a receiver without an atomic guard", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockImplementationOnce(() =>
        respond({
          question: "Synthetic first question",
          status: "in_progress",
        }),
      )
    vi.stubGlobal("fetch", fetch)
    renderChat(vi.fn(), false)
    answer("Synthetic answer")
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(
      fetch.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toHaveLength(1)
    expect(screen.getByRole("textbox", { name: "Your answer" })).toBeDisabled()
  })

  it("does not resend an answer the server already recorded", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockImplementationOnce(() =>
        respond({
          question: "Synthetic second question",
          status: "in_progress",
        }),
      )
    vi.stubGlobal("fetch", fetch)
    renderChat()

    answer("Synthetic answer")
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }))

    expect(await screen.findByText("Synthetic second question")).toBeVisible()
    expect(
      fetch.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toHaveLength(1)
  })

  it("does not offer a retry when the link no longer works", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 404 }))),
    )
    renderChat()

    answer("Synthetic answer")

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We couldn't confirm your answer.",
    )
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull()
  })

  it("finishes when the server says the intake is complete", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => respond({ question: null, status: "completed" })),
    )
    const setIsFinished = vi.fn()
    renderChat(setIsFinished)

    answer("Synthetic final answer")

    await waitFor(() => expect(setIsFinished).toHaveBeenCalledWith(true))
  })

  it("explains how to send since Enter adds a line", () => {
    vi.stubGlobal("fetch", vi.fn())
    renderChat()

    expect(
      screen.getByRole("textbox", { name: "Your answer" }),
    ).toHaveAccessibleDescription(
      "Enter adds a new line. Tap Send when you're done.",
    )
  })
})
