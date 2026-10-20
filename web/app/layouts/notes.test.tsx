import { MemoryRouter } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { queryClient } from "~/configuration/tanstack-query"
import {
  PRACTICE_BAA_REQUIRED_DETAIL,
  PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
} from "~/lib/practice-gate-error"

import NotesLayout, { ErrorBoundary, loadNotesLayoutData } from "./notes"
import { render, screen } from "@testing-library/react"

const noteList = vi.fn()

vi.mock("~/utils/terminology", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/utils/terminology")>()
  return { ...actual, useTerminology: () => actual.getTerminology(null) }
})
vi.mock("~/configuration/client", () => ({
  noteList: (...args: unknown[]) => noteList(...args),
}))

vi.mock("~/configuration/tanstack-query", async () => {
  const { QueryClient } = await import("@tanstack/react-query")
  return {
    queryClient: new QueryClient({
      defaultOptions: { queries: { retry: false } },
    }),
  }
})

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
  queryClient.clear()
})

afterEach(() => queryClient.clear())

describe("loadNotesLayoutData", () => {
  it("returns an updated-agreement wall for a BAA-in-force 403", async () => {
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
    expect(queryClient.getQueryData(["noteList"])).toBeUndefined()
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
    expect(queryClient.getQueryData(["noteList"])).toBeUndefined()
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
    const payload = {
      appointments: [{ id: "appt_1" }],
      note_statuses: { appt_1: "not-started" },
    }
    noteList.mockResolvedValue({
      data: payload,
      error: undefined,
      response: { status: 200 },
    })

    const loaded = await loadNotesLayoutData()

    expect(loaded.blocked).toBeNull()
    expect(loaded.appointments).toEqual(payload.appointments)
    expect(loaded.noteStatuses).toEqual(payload.note_statuses)
    expect(queryClient.getQueryData(["noteList"])).toEqual(payload)
  })

  it("does not invent note status when an older backend omits it", async () => {
    noteList.mockResolvedValue({
      data: {
        appointments: [{ id: "appt_legacy", appointment_name: "Named" }],
      },
      response: { status: 200 },
    })

    expect((await loadNotesLayoutData()).noteStatuses).toEqual({})
  })

  it("shows a confirmed canonical name while retaining the original visit and cached source", async () => {
    const payload = {
      appointments: [
        {
          id: "appt_invented",
          patient_id: "patient_source",
          patient_name: "Invented name-only entry",
        },
      ],
      patient_names: { appt_invented: "Invented confirmed patient" },
    }
    noteList.mockResolvedValue({ data: payload, response: { status: 200 } })
    const loaded = await loadNotesLayoutData()
    expect(loaded.appointments[0]).toMatchObject({
      id: "appt_invented",
      patient_id: "patient_source",
      patient_name: "Invented confirmed patient",
    })
    expect(queryClient.getQueryData(["noteList"])).toEqual(payload)
    expect(payload.appointments[0].patient_name).toBe(
      "Invented name-only entry",
    )
  })

  it("shares an in-flight request between concurrent route loads", async () => {
    const payload = { appointments: [{ id: "appt_shared" }] }
    let resolve!: (value: unknown) => void
    noteList.mockReturnValue(
      new Promise((done) => {
        resolve = done
      }),
    )
    const first = loadNotesLayoutData()
    const second = loadNotesLayoutData()
    expect(noteList).toHaveBeenCalledTimes(1)
    resolve({ data: payload, response: { status: 200 } })
    expect(await first).toEqual(await second)
    expect(queryClient.getQueryData(["noteList"])).toEqual(payload)
  })

  it("rechecks access after a success, clears stale notes on denial, and recovers", async () => {
    const payload = {
      appointments: [{ id: "appt_before" }],
      note_statuses: { appt_before: "completed" },
    }
    const recovered = { appointments: [{ id: "appt_after" }] }
    noteList
      .mockResolvedValueOnce({ data: payload, response: { status: 200 } })
      .mockResolvedValueOnce({
        error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
        response: { status: 403 },
      })
      .mockResolvedValueOnce({ data: recovered, response: { status: 200 } })
    await loadNotesLayoutData()
    expect(queryClient.getQueryData(["noteList"])).toEqual(payload)
    const blocked = await loadNotesLayoutData()
    expect(blocked.appointments).toEqual([])
    expect(blocked.noteStatuses).toEqual({})
    expect(blocked.blocked?.kind).toBe("accept_agreement")
    expect(queryClient.getQueryData(["noteList"])).toBeUndefined()
    expect(await loadNotesLayoutData()).toEqual({
      ...recovered,
      noteStatuses: {},
      blocked: null,
    })
    expect(noteList).toHaveBeenCalledTimes(3)
  })

  it("aborts and fences a late response when the session guard clears the cache", async () => {
    let resolve!: (value: unknown) => void
    noteList.mockReturnValue(
      new Promise((done) => {
        resolve = done
      }),
    )
    const loading = loadNotesLayoutData()
    const failure = expect(loading).rejects.toThrow()
    const signal = noteList.mock.calls[0][0].signal as AbortSignal
    expect(signal.aborted).toBe(false)
    queryClient.clear()
    expect(signal.aborted).toBe(true)
    resolve({
      data: { appointments: [{ id: "appt_stale_session" }] },
      response: { status: 200 },
    })
    await failure
    expect(queryClient.getQueryData(["noteList"])).toBeUndefined()
  })

  it.each([
    { status: 500, detail: PRACTICE_BAA_REQUIRED_DETAIL },
    { status: 403, detail: "An unrelated access problem" },
  ])("propagates unrelated failures without retrying: %j", async (error) => {
    noteList.mockRejectedValue(error)
    await expect(loadNotesLayoutData()).rejects.toEqual(error)
    expect(noteList).toHaveBeenCalledTimes(1)
  })
})

describe("NotesLayout", () => {
  it("shows the agreement wall instead of an empty notes list", () => {
    const loaded = {
      appointments: [],
      noteStatuses: {},
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
