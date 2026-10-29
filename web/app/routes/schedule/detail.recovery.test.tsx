import {
  Outlet,
  RouterProvider,
  createMemoryRouter,
  useLoaderData,
  useRouteError,
} from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  ErrorBoundary as ListBoundary,
  clientLoader as listLoader,
} from "~/layouts/schedule"
import { resetViewAsBindingStateForTests } from "~/lib/view-as-binding"

import { ErrorBoundary, clientLoader } from "./detail"
import { fireEvent, render, screen } from "@testing-library/react"

const { read, status, patients, notes } = vi.hoisted(() => ({
  read: vi.fn(),
  status: vi.fn(),
  patients: vi.fn(),
  notes: vi.fn(),
}))
vi.mock("~/configuration/client", () => ({
  scheduleList: read,
  patientList: patients,
  noteList: notes,
  authorizeZoomCapture: vi.fn(),
  cancelMeeting: vi.fn(),
}))
vi.mock("~/utils/terminology", async (original) => {
  const actual = await original<typeof import("~/utils/terminology")>()
  return { ...actual, useTerminology: () => actual.getTerminology(null) }
})
vi.mock("~/lib/view-as-binding", async (original) => ({
  ...(await original<typeof import("~/lib/view-as-binding")>()),
  rawViewAsControlRequest: status,
}))

const inactive = {
  request_binding: "inactive",
  can_view_as: false,
  active: false,
  read_only: false,
  diagnostic_code: null,
  expires_at: null,
  actor: null,
  target: null,
}
const meeting = {
  zoom_meeting_id: "synthetic_current",
  topic: "Synthetic follow-up",
  status: "scheduled",
  appointment_id: null,
}

function Boundary() {
  return <ErrorBoundary error={useRouteError()} />
}

function LoadedMeeting() {
  const value = useLoaderData() as { meeting: { topic: string } }
  return <div>Meeting loaded: {value.meeting.topic}</div>
}

function openMeeting() {
  const router = createMemoryRouter(
    [
      {
        path: "/schedule",
        element: <Outlet />,
        children: [
          { index: true, element: <div>Schedule index</div> },
          {
            path: ":zoom_meeting_id",
            loader: (args) =>
              clientLoader(args as Parameters<typeof clientLoader>[0]),
            element: <LoadedMeeting />,
            errorElement: <Boundary />,
            hydrateFallbackElement: <div>Loading meeting</div>,
          },
        ],
      },
      { path: "/notes", element: <div>Visits index</div> },
    ],
    { initialEntries: ["/schedule/synthetic_current"] },
  )
  render(<RouterProvider router={router} />)
  return router
}

beforeEach(() => {
  window.history.replaceState({}, "", "/schedule/synthetic_current")
  resetViewAsBindingStateForTests()
  read.mockReset()
  patients.mockReset()
  notes.mockReset()
  status.mockResolvedValue(inactive)
})
afterEach(() => {
  window.history.replaceState({}, "", "/")
  resetViewAsBindingStateForTests()
})

describe("schedule recovery through the real router and support-view guard", () => {
  it("offers safe navigation for a missing meeting without mounting its controls", async () => {
    read.mockResolvedValue({
      data: { meetings: [], capture_available: true },
      response: new Response(null, { status: 200 }),
    })
    openMeeting()
    expect(
      await screen.findByRole("heading", { name: "Meeting unavailable" }),
    ).toBeVisible()
    expect(screen.queryByText(/Meeting loaded/)).not.toBeInTheDocument()
    expect(patients).not.toHaveBeenCalled()
    expect(notes).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("link", { name: "Return to schedule" }))
    expect(await screen.findByText("Schedule index")).toBeVisible()
    expect(read).toHaveBeenCalledTimes(1)
  })

  it("retries a temporary failure once, disables duplicate clicks and restores the meeting", async () => {
    read.mockResolvedValueOnce({
      response: new Response(null, { status: 503 }),
      error: { detail: "Private server diagnostic" },
    })
    let finish!: (value: unknown) => void
    read.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)))
    openMeeting()
    expect(
      await screen.findByRole("heading", {
        name: "Couldn’t load this meeting",
      }),
    ).toBeVisible()
    expect(screen.queryByText(/Private server/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(
      await screen.findByRole("button", { name: "Trying again…" }),
    ).toBeDisabled()
    expect(read).toHaveBeenCalledTimes(2)
    finish({
      data: { meetings: [meeting], capture_available: false },
      response: new Response(null, { status: 200 }),
    })
    expect(
      await screen.findByText("Meeting loaded: Synthetic follow-up"),
    ).toBeVisible()
    expect(read).toHaveBeenCalledTimes(2)
    expect(patients).not.toHaveBeenCalled()
    expect(notes).not.toHaveBeenCalled()
  })

  it("does not expose successful-looking meeting data carried by a failed list response", async () => {
    read.mockResolvedValue({
      data: { meetings: [{ ...meeting, topic: "Other provider chart" }] },
      response: new Response(null, { status: 403 }),
      error: { detail: "Other provider identifiers" },
    })
    openMeeting()
    expect(
      await screen.findByRole("heading", {
        name: "Couldn’t load this meeting",
      }),
    ).toBeVisible()
    expect(screen.queryByText(/Other provider/)).not.toBeInTheDocument()
    expect(patients).not.toHaveBeenCalled()
    expect(notes).not.toHaveBeenCalled()
  })

  it("keeps the recovery screen hidden when support-view verification is unavailable", async () => {
    status.mockRejectedValue(new Error("Verification unavailable"))
    read.mockResolvedValue({ response: new Response(null, { status: 503 }) })
    openMeeting()
    expect(
      await screen.findByRole("heading", {
        name: "We could not verify support view",
      }),
    ).toBeVisible()
    expect(
      screen.queryByText("Couldn’t load this meeting"),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/Meeting loaded/)).not.toBeInTheDocument()
  })

  it("retains the active read-only support banner on a missing-meeting error", async () => {
    status.mockResolvedValue({
      ...inactive,
      request_binding: `vab1.${"a".repeat(64)}`,
      can_view_as: true,
      active: true,
      read_only: true,
      expires_at: "2099-01-01T00:00:00Z",
      actor: { doctor_id: "synthetic_actor", email: "actor@synthetic.invalid" },
      target: {
        doctor_id: "synthetic_target",
        email: "target@synthetic.invalid",
      },
    })
    read.mockResolvedValue({
      data: { meetings: [] },
      response: new Response(null, { status: 200 }),
    })
    openMeeting()
    expect(
      await screen.findByRole("heading", { name: "Page unavailable" }),
    ).toBeVisible()
    expect(screen.getAllByText(/Read-only/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Meeting loaded/)).not.toBeInTheDocument()
  })

  it("recovers the parent schedule read without mounting a protected outlet", async () => {
    read.mockResolvedValue({
      response: new Response(null, { status: 503 }),
      error: { detail: "Private schedule diagnostic" },
    })
    function ParentBoundary() {
      return <ListBoundary error={useRouteError()} />
    }
    const router = createMemoryRouter(
      [
        {
          path: "/schedule",
          loader: (args) =>
            listLoader(args as Parameters<typeof listLoader>[0]),
          element: <div>Protected schedule outlet</div>,
          errorElement: <ParentBoundary />,
          hydrateFallbackElement: <div>Loading schedule</div>,
        },
      ],
      { initialEntries: ["/schedule"] },
    )
    render(<RouterProvider router={router} />)
    expect(
      await screen.findByRole("heading", {
        name: "Couldn’t load your schedule",
      }),
    ).toBeVisible()
    expect(
      screen.queryByText("Protected schedule outlet"),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/Private schedule/)).not.toBeInTheDocument()
  })
})
