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

function renderChat(setIsFinished = vi.fn()) {
  render(
    <IntakeRuntimeProvider
      api={API}
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

  it("keeps the answer and resends it once when sending fails", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
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
      "That didn't send. Your answer is still here.",
    )
    expect(screen.getAllByText("Synthetic answer")).toHaveLength(1)
    expect(screen.getByRole("textbox", { name: "Your answer" })).toBeDisabled()

    fireEvent.click(screen.getByRole("button", { name: "Try again" }))

    expect(await screen.findByText("Synthetic second question")).toBeVisible()
    expect(screen.queryByRole("alert")).toBeNull()
    expect(screen.getAllByText("Synthetic answer")).toHaveLength(1)
    expect(fetch).toHaveBeenCalledTimes(2)
    for (const [, init] of fetch.mock.calls) {
      expect(JSON.parse((init as RequestInit).body as string)).toEqual({
        answer: "Synthetic answer",
      })
    }
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
