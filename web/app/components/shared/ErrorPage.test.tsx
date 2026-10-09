import { afterEach, describe, expect, it } from "vitest"

import PageNotFound from "./PageNotFound"
import { render, screen } from "@testing-library/react"

afterEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("PageNotFound", () => {
  it("offers a way home in the clinician app", () => {
    window.history.replaceState({}, "", "/notes/missing")
    render(<PageNotFound />)

    expect(
      screen.getByRole("heading", { level: 1, name: "Page not found" }),
    ).toBeVisible()
    expect(screen.getByRole("link", { name: "Go to home" })).toHaveAttribute(
      "href",
      "/",
    )
  })

  it("never links patients into the clinician app", () => {
    window.history.replaceState({}, "", "/chat/intake/unknown/extra")
    render(<PageNotFound />)

    expect(screen.queryByRole("link")).toBeNull()
  })
})
