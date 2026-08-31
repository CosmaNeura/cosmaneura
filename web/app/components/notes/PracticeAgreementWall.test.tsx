import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"

import {
  PRACTICE_BAA_REQUIRED_DETAIL,
  PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
  practiceAgreementBlockFromUnknown,
} from "~/lib/practice-gate-error"

import { PracticeAgreementWall } from "./PracticeAgreementWall"
import { render, screen } from "@testing-library/react"

function renderWall(detail: string) {
  const block = practiceAgreementBlockFromUnknown({ detail })
  if (!block) throw new Error("expected a practice-gate block")
  return render(
    <MemoryRouter>
      <PracticeAgreementWall block={block} />
    </MemoryRouter>,
  )
}

describe("PracticeAgreementWall", () => {
  it("shows join-practice words for a missing-practice 403", () => {
    renderWall(PRACTICE_MEMBERSHIP_REQUIRED_DETAIL)

    expect(screen.getByRole("alert")).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: "Join the practice" }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(PRACTICE_MEMBERSHIP_REQUIRED_DETAIL),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/join the practice before notes will appear/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("heading", { name: "Accept the updated agreement" }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Join the practice" }),
    ).toHaveAttribute("href", "/onboarding")
    expect(
      screen.getByRole("link", { name: "Open practice settings" }),
    ).toHaveAttribute("href", "/settings")
  })

  it("shows accept-the-updated-agreement words for Maura's BAA v2.3 403", () => {
    renderWall(PRACTICE_BAA_REQUIRED_DETAIL)

    expect(screen.getByRole("alert")).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: "Accept the updated agreement" }),
    ).toBeInTheDocument()
    expect(screen.getByText(PRACTICE_BAA_REQUIRED_DETAIL)).toBeInTheDocument()
    expect(
      screen.getByText(
        /accept the updated agreement before notes will appear/i,
      ),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("heading", { name: "Join the practice" }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Accept the updated agreement" }),
    ).toHaveAttribute("href", "/onboarding")
  })
})
