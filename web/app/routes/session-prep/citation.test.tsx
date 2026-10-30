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
const auth = vi.hoisted(() => {
  const state = {
    isLoaded: true,
    userId: "user_synthetic_a" as string | null,
    sessionId: "session_synthetic_a" as string | null,
    sdkOverride: null as {
      user: { id: string } | null
      session: { id: string } | null
    } | null,
    listeners: new Set<
      (resources: {
        user: { id: string } | null
        session: { id: string } | null
      }) => void
    >(),
  }
  const clerk = {
    get user() {
      return state.userId ? { id: state.userId } : null
    },
    get session() {
      return state.sessionId ? { id: state.sessionId } : null
    },
    addListener: (
      callback: (resources: {
        user: { id: string } | null
        session: { id: string } | null
      }) => void,
    ) => {
      state.listeners.add(callback)
      return () => {
        state.listeners.delete(callback)
      }
    },
  }
  return { state, clerk }
})
vi.mock("~/configuration/clerk", () => ({
  getClient: async () =>
    auth.state.sdkOverride ?? {
      user: auth.clerk.user,
      session: auth.clerk.session,
    },
}))
vi.mock("@clerk/clerk-react", () => ({
  useClerk: () => auth.clerk,
  useAuth: () => ({
    isLoaded: auth.state.isLoaded,
    userId: auth.state.userId,
    sessionId: auth.state.sessionId,
  }),
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
  auth.state.isLoaded = true
  auth.state.userId = "user_synthetic_a"
  auth.state.sessionId = "session_synthetic_a"
  auth.state.listeners.clear()
  auth.state.sdkOverride = null
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
  it.each(["account", "session", "missed_notification", "signout"])(
    "fences a late old200 on real Clerk %s change despite inactive support status",
    async (kind) => {
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
      const oldSignal = control.read.mock.calls[0][0].signal as AbortSignal
      control.read.mockResolvedValue({ error: { status: 403 } })
      act(() => {
        if (kind !== "session")
          auth.state.userId = kind === "signout" ? null : "user_synthetic_b"
        auth.state.sessionId = kind === "signout" ? null : "session_synthetic_b"
        if (kind !== "missed_notification")
          for (const callback of auth.state.listeners)
            callback({ user: auth.clerk.user, session: auth.clerk.session })
      })
      // A missing Clerk notification is also fenced by direct live resource checks
      // after await; the transport intentionally ignores the abort signal.
      if (kind !== "missed_notification") {
        view.rerender(<Citation />)
        expect(oldSignal.aborted).toBe(true)
      }
      await act(async () => resolve({ data: retainedFixture() }))
      await screen.findByText(
        "This reviewed source is unavailable for your current account or has expired.",
      )
      expect(document.body.textContent).not.toContain(
        "Original reviewed synthetic version",
      )
      expect(
        control.status.mock.results.every((result) => result.type === "return"),
      ).toBe(true)
    },
  )

  it("refuses stale SDK auth resources even when the live Clerk hook is already on the new account", async () => {
    control.reference = `${finalReference}.1`
    auth.state.userId = "user_synthetic_b"
    auth.state.sessionId = "session_synthetic_b"
    auth.state.sdkOverride = {
      user: { id: "user_synthetic_a" },
      session: { id: "session_synthetic_a" },
    }
    control.status.mockResolvedValue({ active: false })
    control.read.mockResolvedValue({ data: retainedFixture() })
    render(<Citation />)
    await screen.findByText(
      "This reviewed source is unavailable for your current account or has expired.",
    )
    expect(control.read).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain(
      "Original reviewed synthetic version",
    )
  })

  it("clears displayed source immediately when Clerk replaces the account session", async () => {
    const view = fixture()
    await screen.findByText(
      "Original reviewed synthetic version. Literal <script> text. Ω",
    )
    control.read.mockResolvedValue({ error: { status: 403 } })
    act(() => {
      auth.state.userId = "user_synthetic_b"
      auth.state.sessionId = "session_synthetic_b"
      for (const callback of auth.state.listeners)
        callback({ user: auth.clerk.user, session: auth.clerk.session })
    })
    view.rerender(<Citation />)
    expect(document.body.textContent).not.toContain(
      "Original reviewed synthetic version",
    )
    await screen.findByText(
      "This reviewed source is unavailable for your current account or has expired.",
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
