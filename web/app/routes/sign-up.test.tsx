import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import SignUpPage from "./sign-up"
import { render, screen } from "@testing-library/react"

vi.mock("@clerk/clerk-react", () => ({
  SignUp: () => <div>Stock Clerk SignUp</div>,
}))

describe("sign-up route", () => {
  it("keeps the stock sign-up methods available with recovery guidance", () => {
    render(
      <MemoryRouter initialEntries={["/sign-up?channel=mcd"]}>
        <SignUpPage />
      </MemoryRouter>,
    )

    expect(screen.getByText(/sign-in method shown below/i)).toBeInTheDocument()
    expect(
      screen.getByText(/request another code when available/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/password box is a dead end/i),
    ).not.toBeInTheDocument()
    expect(screen.getByText("Stock Clerk SignUp")).toBeInTheDocument()
  })
})
