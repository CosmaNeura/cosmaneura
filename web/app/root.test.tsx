import { RouterProvider, createMemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"

import { BillingRecoveryRedirectError } from "~/lib/billing-recovery"

import App, { ErrorBoundary } from "./root"
import { render, screen } from "@testing-library/react"

describe("Root App", () => {
  it("renders without crashing", () => {
    const router = createMemoryRouter([
      {
        path: "/",
        element: <App />,
        children: [
          {
            path: "/",
            element: <div>Test content</div>,
          },
        ],
      },
    ])

    render(<RouterProvider router={router} />)
    expect(screen.getByText("Test content")).toBeInTheDocument()
  })
})

describe("root error boundary", () => {
  it("renders a non-error fallback while billing recovery redirects", () => {
    const error = new BillingRecoveryRedirectError(
      "/settings?billing=required&returnTo=%2Fnotes",
    )
    const props = { error } as Parameters<typeof ErrorBoundary>[0]

    render(ErrorBoundary(props))

    expect(
      screen.getByText(/redirecting to restore practice access/i),
    ).toBeInTheDocument()
    expect(screen.queryByText("Oops!")).not.toBeInTheDocument()
  })

  it("retains the normal boundary for unrelated failures", () => {
    const props = { error: new Error("unrelated") } as Parameters<
      typeof ErrorBoundary
    >[0]

    render(ErrorBoundary(props))

    expect(screen.getByText("Oops!")).toBeInTheDocument()
  })
})
