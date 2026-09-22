import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import SignInPage from "./sign-in"
import { render, screen } from "@testing-library/react"

vi.mock("@clerk/clerk-react", () => ({
  SignIn: () => <div>Stock Clerk SignIn</div>,
}))

describe("sign-in route", () => {
  it("wraps stock Clerk SignIn with MCD recovery guidance", () => {
    render(
      <MemoryRouter initialEntries={["/sign-in?channel=mcd"]}>
        <SignInPage />
      </MemoryRouter>,
    )

    expect(screen.getByText(/sign-in method shown below/i)).toBeInTheDocument()
    expect(screen.getByText(/newest message/i)).toBeInTheDocument()
    expect(
      screen.getByText(/request another code when available/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/password box is a dead end/i),
    ).not.toBeInTheDocument()
    expect(screen.getByText("Stock Clerk SignIn")).toBeInTheDocument()
  })
})
