import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import SignInPage from "./sign-in"
import { render, screen } from "@testing-library/react"

vi.mock("@clerk/clerk-react", () => ({
  SignIn: () => <div>Stock Clerk SignIn</div>,
}))

describe("sign-in route", () => {
  it("wraps stock Clerk SignIn with MCD fail-loud chrome copy", () => {
    render(
      <MemoryRouter initialEntries={["/sign-in?channel=mcd"]}>
        <SignInPage />
      </MemoryRouter>,
    )

    expect(screen.getByText(/Google or one email code/i)).toBeInTheDocument()
    expect(screen.getByText(/newest mail/i)).toBeInTheDocument()
    expect(screen.getByText(/Do not tap resend/)).toBeInTheDocument()
    expect(screen.getByText(/password box is a dead end/i)).toBeInTheDocument()
    expect(screen.getByText("Stock Clerk SignIn")).toBeInTheDocument()
  })
})
