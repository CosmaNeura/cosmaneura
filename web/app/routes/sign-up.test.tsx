import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import SignUpPage from "./sign-up"
import { render, screen } from "@testing-library/react"

vi.mock("@clerk/clerk-react", () => ({
  SignUp: () => <div>Stock Clerk SignUp</div>,
}))

describe("sign-up route", () => {
  it("keeps MCD sibling chrome honest about passwordless auth", () => {
    render(
      <MemoryRouter initialEntries={["/sign-up?channel=mcd"]}>
        <SignUpPage />
      </MemoryRouter>,
    )

    expect(screen.getByText(/Google or one email code/i)).toBeInTheDocument()
    expect(screen.getByText(/Do not tap resend/)).toBeInTheDocument()
    expect(screen.getByText(/password box is a dead end/i)).toBeInTheDocument()
    expect(screen.getByText("Stock Clerk SignUp")).toBeInTheDocument()
  })
})
