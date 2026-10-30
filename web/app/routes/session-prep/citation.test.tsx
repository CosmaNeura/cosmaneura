import { afterEach, describe, expect, it, vi } from "vitest"

import { retainedFixture } from "~/sovereign/RetainedCitation.fixture"
import { finalReference } from "~/sovereign/SovereignCitationDisplay.fixture"

import Citation from "./citation"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"

const control = vi.hoisted(() => ({
  read: vi.fn(),
  status: vi.fn(),
  reference: "",
  invalidate: () => {},
}))
vi.mock("~/configuration/client", () => ({
  retainedSessionPrepCitation: control.read,
}))
vi.mock("~/lib/view-as-api", () => ({ getViewAsStatus: control.status }))
vi.mock("~/lib/view-as-binding", () => ({
  subscribeToViewAsStateChanges: (callback: () => void) => {
    control.invalidate = callback
    return () => {}
  },
}))
vi.mock("react-router", () => ({
  useParams: () => ({ citation_ref: control.reference }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.restoreAllMocks()
})
function fixture() {
  control.reference = `${finalReference}.1`
  control.status.mockResolvedValue({ active: false })
  control.read.mockResolvedValue({ data: retainedFixture() })
  return render(<Citation />)
}
describe("authenticated retained-source viewer", () => {
  it("uses only the generated retained SDK path and renders the exact old version safely", async () => {
    fixture()
    await screen.findByText(
      "Original reviewed synthetic version. Literal <script> text. Ω",
    )
    expect(document.querySelector("script")).toBeNull()
    expect(control.read).toHaveBeenCalledTimes(1)
    expect(control.read.mock.calls[0][0]).toMatchObject({
      path: { final_ref: finalReference, ordinal: 1 },
      cache: "no-store",
      throwOnError: false,
    })
    expect(document.body.textContent).toContain("2026-10-01T09:00:00Z")
    expect(document.body.textContent).toContain("2026-10-02T10:00:00Z")
    expect(document.body.textContent).not.toContain(finalReference)
    act(() => control.invalidate())
    expect(document.body.textContent).not.toContain(
      "Original reviewed synthetic version",
    )
  })
  it("discards an old-reference response after a same-day new reference opens", async () => {
    let resolve!: (value: unknown) => void
    control.reference = `${finalReference}.1`
    control.status.mockResolvedValue({ active: false })
    control.read.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    const view = render(<Citation />)
    await waitFor(() => expect(control.read).toHaveBeenCalledTimes(1))
    const second = retainedFixture()
    second.citation.citation_ref = `${finalReference}.2`
    second.citation.ordinal = 2
    second.citation.source_at = "2026-10-01T17:00:00Z"
    control.read.mockResolvedValue({ data: second })
    control.reference = `${finalReference}.2`
    view.rerender(<Citation />)
    await screen.findByText("Visit · 2026-10-01T17:00:00Z")
    await act(async () => resolve({ data: retainedFixture() }))
    expect(document.body.textContent).not.toContain(
      "Visit · 2026-10-01T09:00:00Z",
    )
  })
  it.each(["denied", "tampered", "view_as", "late_view_as"])(
    "keeps %s unavailable without a current-note fallback",
    async (kind) => {
      control.reference = `${finalReference}.1`
      control.status.mockResolvedValue({ active: false })
      const value = retainedFixture()
      if (kind === "tampered") value.sections[0].text = "Wrong corrected source"
      control.read.mockResolvedValue(
        kind === "denied" ? { error: { status: 404 } } : { data: value },
      )
      if (kind === "view_as") control.status.mockResolvedValue({ active: true })
      if (kind === "late_view_as")
        control.status
          .mockResolvedValueOnce({ active: false })
          .mockResolvedValue({ active: true })
      render(<Citation />)
      await screen.findByText(
        "This reviewed source is unavailable for your current account or has expired.",
      )
      expect(document.body.textContent).not.toContain("Wrong corrected source")
      expect(document.body.textContent).not.toContain(
        "Original reviewed synthetic version",
      )
      expect(control.read).toHaveBeenCalledTimes(kind === "view_as" ? 0 : 1)
    },
  )
})
