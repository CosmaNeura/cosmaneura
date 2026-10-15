import { afterEach, describe, expect, it, vi } from "vitest"

import { ViewAsAccountControl } from "./ViewAsAccountControl"
import { cleanup, render, screen } from "@testing-library/react"

const state = vi.hoisted(() => ({ viewAsActive: false }))

vi.mock("~/components/ViewAsRouteGuard", () => ({
  useViewAsActive: () => state.viewAsActive,
}))

vi.mock("@clerk/clerk-react", () => ({
  useUser: () => ({
    user: { primaryEmailAddress: { emailAddress: "synthetic@example.test" } },
  }),
  UserButton: () => <button>Account menu</button>,
}))

afterEach(() => {
  cleanup()
  state.viewAsActive = false
  vi.unstubAllEnvs()
})

describe("account control layout", () => {
  it("keeps the narrow rail compact without hiding the account menu", () => {
    vi.stubEnv("VITE_PYTHON_URL", "https://staging-app.cosmaneura.com")
    render(<ViewAsAccountControl compact />)

    expect(screen.getByText(/Signed in as/)).toHaveClass("sr-only")
    expect(
      screen.getByTitle("Signed in as synthetic@example.test"),
    ).toBeVisible()
    expect(screen.getByText("Staging")).toHaveClass("sr-only")
    expect(screen.getByTitle("Staging")).toHaveAttribute("aria-hidden", "true")
    expect(screen.getByRole("button", { name: "Account menu" })).toBeVisible()
  })

  it("keeps the full identity visible outside the icon rail", () => {
    render(<ViewAsAccountControl />)
    expect(screen.getByText(/Signed in as/)).not.toHaveClass("sr-only")
  })

  it("preserves disabled account controls during read-only support viewing", () => {
    state.viewAsActive = true
    render(<ViewAsAccountControl compact />)
    expect(screen.queryByRole("button", { name: "Account menu" })).toBeNull()
    expect(screen.getByText("Account controls disabled")).toHaveClass("sr-only")
  })
})
