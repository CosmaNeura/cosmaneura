import {
  RouterProvider,
  createMemoryRouter,
  useLoaderData,
  useRouteError,
} from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import IntakeChat, {
  ErrorBoundary,
  INTAKE_PROCESSING_POLL_MS,
  IntakeComplete,
} from "./intake"
import { act, render, screen } from "@testing-library/react"

vi.mock("~/components/chat/intake/IntakeThread", () => ({
  IntakeThread: () => <p>Chat thread</p>,
}))

afterEach(() => {
  vi.useRealTimers()
})

function renderRoute(loader: () => unknown) {
  const router = createMemoryRouter(
    [
      {
        path: "/chat/intake/:id",
        loader,
        Component: () => {
          const data = useLoaderData()
          return (
            <IntakeChat
              {...({ loaderData: data } as Parameters<typeof IntakeChat>[0])}
            />
          )
        },
        ErrorBoundary: () => (
          <ErrorBoundary
            {...({ error: useRouteError() } as Parameters<
              typeof ErrorBoundary
            >[0])}
          />
        ),
      },
    ],
    { initialEntries: ["/chat/intake/intake_test"] },
  )
  render(<RouterProvider router={router} />)
  return router
}

describe("intake chat route", () => {
  it("treats an intake without a question yet as still being prepared", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const loader = vi
      .fn()
      .mockResolvedValueOnce({ status: "in_progress", question: null })
      .mockResolvedValueOnce({
        status: "in_progress",
        question: "Synthetic question",
      })
    renderRoute(loader)

    expect(
      await screen.findByRole("heading", { name: "Preparing your questions…" }),
    ).toBeInTheDocument()
    expect(loader).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(INTAKE_PROCESSING_POLL_MS)
    })

    expect(loader).toHaveBeenCalledTimes(2)
    expect(await screen.findByText("Chat thread")).toBeInTheDocument()
  })

  it("explains a broken link without clinician navigation", async () => {
    renderRoute(() => {
      throw new Response("Not found", { status: 404 })
    })

    expect(
      await screen.findByRole("heading", { name: "This link isn't working" }),
    ).toBeInTheDocument()
    expect(screen.getByText(/contact your provider's office/)).toBeVisible()
    expect(screen.queryByRole("link")).toBeNull()
    expect(screen.queryByText(/404|Not found/)).toBeNull()
  })

  it("offers a retry for a temporary failure instead of blaming the link", async () => {
    renderRoute(() => {
      throw new TypeError("Failed to fetch")
    })

    expect(
      await screen.findByRole("heading", {
        name: "We couldn't load your questions",
      }),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible()
    expect(screen.queryByRole("link")).toBeNull()
  })
})

describe("intake completion", () => {
  it("says what happens next", () => {
    render(<IntakeComplete />)

    expect(screen.getByRole("heading", { name: "Thank you" })).toBeVisible()
    expect(
      screen.getByText(
        "Your answers are on their way to your provider. You can close this page.",
      ),
    ).toBeVisible()
  })
})
