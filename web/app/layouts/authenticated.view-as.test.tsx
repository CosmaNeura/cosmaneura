import { describe, expect, it } from "vitest"

import AuthenticatedLayout from "./authenticated"
import { render, screen } from "@testing-library/react"

describe("authenticated layout view-as safety", () => {
  it("renders only the verification interstitial when status is unavailable", () => {
    const props = {
      loaderData: {
        viewAsCheck: { state: "unavailable", status: null },
      },
    } as Parameters<typeof AuthenticatedLayout>[0]

    render(<AuthenticatedLayout {...props} />)

    expect(
      screen.getByText("Support-view status unavailable"),
    ).toBeInTheDocument()
    expect(
      document.querySelector("[data-view-as-interstitial]"),
    ).toBeInTheDocument()
  })
})
