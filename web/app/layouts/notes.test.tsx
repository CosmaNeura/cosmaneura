import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  PRACTICE_BAA_REQUIRED_DETAIL,
  PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
} from "~/lib/practice-gate-error"

import NotesLayout, { ErrorBoundary, loadNotesLayoutData } from "./notes"
import { render, screen } from "@testing-library/react"

const noteList = vi.fn()
const setQueryData = vi.fn()

vi.mock("~/configuration/client", () => ({
  noteList: (...args: unknown[]) => noteList(...args),
}))

vi.mock("~/configuration/tanstack-query", () => ({
  queryClient: {
    setQueryData: (...args: unknown[]) => setQueryData(...args),
  },
}))

vi.mock("client/@tanstack/react-query.gen", () => ({
  noteListOptions: () => ({ queryKey: ["noteList"] }),
}))

vi.mock("@/components/app-sidebar", () => ({
  AppSidebar: () => null,
}))

vi.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}))

vi.mock("~/lib/appointment", () => ({
  appointmentsToSidebarEntries: () => [],
}))

beforeEach(() => {
  noteList.mockReset()
  setQueryData.mockReset()
})

describe("loadNotesLayoutData", () => {
  it("returns an updated-agreement wall for Maura's BAA-in-force 403", async () => {
    noteList.mockResolvedValue({
      data: undefined,
      error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
      response: { status: 403 },
    })

    const loaded = await loadNotesLayoutData()

    expect(loaded.blocked?.kind).toBe("accept_agreement")
    expect(loaded.blocked?.headline).toBe("Accept the updated agreement")
    expect(loaded.blocked?.detail).toBe(PRACTICE_BAA_REQUIRED_DETAIL)
    expect(loaded.blocked?.kind).not.toBe("join_practice")
    expect(setQueryData).not.toHaveBeenCalled()
  })

  it("returns a join-practice wall payload when notes 403 without throwing", async () => {
    noteList.mockResolvedValue({
      data: undefined,
      error: { detail: PRACTICE_MEMBERSHIP_REQUIRED_DETAIL },
      response: { status: 403 },
    })

    const loaded = await loadNotesLayoutData()

    expect(loaded.blocked?.kind).toBe("join_practice")
    expect(loaded.blocked?.headline).toBe("Join the practice")
    expect(loaded.appointments).toEqual([])
    expect(setQueryData).not.toHaveBeenCalled()
  })

  it("returns an accept-agreement wall payload when notes 403 is thrown", async () => {
    noteList.mockRejectedValue({
      detail: PRACTICE_BAA_REQUIRED_DETAIL,
      status: 403,
    })

    const loaded = await loadNotesLayoutData()

    expect(loaded.blocked?.kind).toBe("accept_agreement")
    expect(loaded.blocked?.headline).toBe("Accept the updated agreement")
    expect(loaded.blocked?.instruction).toMatch(
      /accept the updated agreement before notes will appear/i,
    )
    expect(loaded.blocked?.instruction).not.toMatch(/join the practice/i)
  })

  it("seeds the notes cache on a successful list", async () => {
    const payload = { appointments: [{ id: "appt_1" }] }
    noteList.mockResolvedValue({
      data: payload,
      error: undefined,
      response: { status: 200 },
    })

    const loaded = await loadNotesLayoutData()

    expect(loaded.blocked).toBeNull()
    expect(loaded.appointments).toEqual(payload.appointments)
    expect(setQueryData).toHaveBeenCalledWith(["noteList"], payload)
  })
})

describe("NotesLayout", () => {
  it("shows the agreement wall instead of an empty notes list", () => {
    const loaded = {
      appointments: [],
      blocked: {
        kind: "accept_agreement" as const,
        detail: PRACTICE_BAA_REQUIRED_DETAIL,
        headline: "Accept the updated agreement",
        instruction: "Accept the updated agreement before notes will appear.",
      },
    }

    render(
      <MemoryRouter>
        <NotesLayout loaderData={loaded} />
      </MemoryRouter>,
    )

    expect(
      screen.getByRole("heading", { name: "Accept the updated agreement" }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /accept the updated agreement before notes will appear/i,
      ),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("heading", { name: "Join the practice" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Select a patient")).not.toBeInTheDocument()
  })
})

describe("notes ErrorBoundary", () => {
  it("renders the agreement wall for a thrown BAA 403", () => {
    render(
      <MemoryRouter>
        <ErrorBoundary
          error={{
            detail: PRACTICE_BAA_REQUIRED_DETAIL,
            status: 403,
          }}
        />
      </MemoryRouter>,
    )

    expect(
      screen.getByRole("heading", { name: "Accept the updated agreement" }),
    ).toBeInTheDocument()
    expect(screen.getByText(PRACTICE_BAA_REQUIRED_DETAIL)).toBeInTheDocument()
  })
})
