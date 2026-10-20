import type { PropsWithChildren } from "react"
import {
  RouterProvider,
  createMemoryRouter,
  useLoaderData,
  useRouteError,
} from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { queryClient } from "~/configuration/tanstack-query"
import AuthenticatedLayout, {
  ErrorBoundary as AuthBoundary,
  clientLoader as authLoader,
} from "~/layouts/authenticated"
import NotesLayout, {
  ErrorBoundary as NotesBoundary,
  clientLoader as notesLoader,
} from "~/layouts/notes"
import { READ_DEADLINE_MS } from "~/lib/read-deadline"

import { ErrorBoundary, clientLoader } from "./detail"
import { act, fireEvent, render, screen } from "@testing-library/react"

const { detail, list, settings, status, observe } = vi.hoisted(() => ({
  detail: vi.fn(),
  list: vi.fn(),
  settings: vi.fn(),
  status: vi.fn(),
  observe: vi.fn((value) => value),
}))
vi.mock("~/utils/terminology", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/utils/terminology")>()
  return { ...actual, useTerminology: () => actual.getTerminology(null) }
})
vi.mock("~/configuration/client", () => ({
  noteDetail: detail,
  noteList: list,
}))
vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
vi.mock("~/configuration/tanstack-query", async () => {
  const { QueryClient } = await import("@tanstack/react-query")
  return {
    queryClient: new QueryClient({
      defaultOptions: { queries: { retry: false } },
    }),
    SETTINGS_STALE_TIME: 60_000,
  }
})
vi.mock("client/@tanstack/react-query.gen", () => ({
  noteListOptions: () => ({ queryKey: ["noteList"] }),
  settingsDataOptions: () => ({ queryKey: ["settings"], queryFn: settings }),
}))
vi.mock("~/lib/view-as-binding", async (original) => ({
  ...(await original<typeof import("~/lib/view-as-binding")>()),
  rawViewAsControlRequest: status,
  observeViewAsStatusBinding: observe,
}))
vi.mock("~/components/AdminBar", () => ({ default: () => null }))
vi.mock("~/components/ZoomConnectionAlert", () => ({
  ZoomConnectionAlert: () => null,
}))
vi.mock("@/components/app-sidebar", () => ({
  AppSidebar: () => <div>Visits sidebar</div>,
}))
vi.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: ({ children }: PropsWithChildren) => <div>{children}</div>,
  SidebarInset: ({ children }: PropsWithChildren) => <div>{children}</div>,
  SidebarTrigger: () => <button>Toggle sidebar</button>,
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
const readySettings = {
  is_admin: false,
  onboarding_completed: true,
  practice_ready: true,
  billing_recovery_required: false,
}
let router: ReturnType<typeof createMemoryRouter> | undefined

function openVisit() {
  function Auth() {
    const loaderData = useLoaderData()
    return (
      <AuthenticatedLayout
        {...({ loaderData } as Parameters<typeof AuthenticatedLayout>[0])}
      />
    )
  }
  function Notes() {
    return <NotesLayout loaderData={useLoaderData()} />
  }
  function AuthError() {
    return <AuthBoundary error={useRouteError()} />
  }
  function Visit() {
    return <div>Visit loaded: {useLoaderData().focusedNoteId}</div>
  }
  function DetailError() {
    return (
      <ErrorBoundary
        {...({ error: useRouteError() } as Parameters<typeof ErrorBoundary>[0])}
      />
    )
  }
  function NotesError() {
    return <NotesBoundary error={useRouteError()} />
  }
  router = createMemoryRouter(
    [
      {
        loader: (args) => authLoader(args as Parameters<typeof authLoader>[0]),
        element: <Auth />,
        errorElement: <AuthError />,
        hydrateFallbackElement: <div>Loading account</div>,
        children: [
          {
            path: "/notes",
            loader: (args) =>
              notesLoader(args as Parameters<typeof notesLoader>[0]),
            element: <Notes />,
            errorElement: <NotesError />,
            children: [
              {
                path: ":id",
                loader: (args) =>
                  clientLoader(args as Parameters<typeof clientLoader>[0]),
                element: <Visit />,
                errorElement: <DetailError />,
              },
            ],
          },
        ],
      },
    ],
    { initialEntries: ["/notes/synthetic_visit?reviewNote=synthetic_note"] },
  )
  render(<RouterProvider router={router} />)
}

beforeEach(() => {
  window.history.replaceState(null, "", "/notes/synthetic_visit")
  vi.clearAllMocks()
  queryClient.clear()
  status.mockResolvedValue(inactive)
  settings.mockResolvedValue(readySettings)
  list.mockResolvedValue({ data: { appointments: [] } })
  detail.mockResolvedValue({ response: new Response(null, { status: 503 }) })
})
afterEach(() => {
  router?.dispose()
  queryClient.clear()
  vi.useRealTimers()
  window.history.replaceState(null, "", "/")
})

describe("visit retry with its actual ancestor loaders", () => {
  it.each(["status", "settings", "list"] as const)(
    "settles a stalled %s read without accepting its late result",
    async (stalled) => {
      openVisit()
      expect(
        await screen.findByRole("heading", {
          name: "Couldn’t load this visit",
        }),
      ).toBeVisible()
      // Force a settings read during revalidation, rather than a cache hit.
      queryClient.removeQueries({ queryKey: ["settings"], exact: true })
      let finish!: (value: unknown) => void
      const reader = { status, settings, list }[stalled]
      reader.mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve
        }),
      )
      vi.useFakeTimers({
        toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
      })
      fireEvent.click(screen.getByRole("button", { name: "Try again" }))
      await act(async () => {
        await Promise.resolve()
      })
      expect(
        screen.getByRole("button", { name: "Trying again…" }),
      ).toBeDisabled()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS + 1)
      })
      expect(router?.state.revalidation).toBe("idle")
      expect(screen.queryByText(/Visit loaded:/)).not.toBeInTheDocument()
      if (stalled === "list") {
        expect(
          screen.getByRole("heading", { name: "Couldn’t load your visits" }),
        ).toBeVisible()
        expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled()
      } else {
        expect(
          screen.getByRole("heading", {
            name: "Couldn’t verify account access",
          }),
        ).toBeVisible()
        expect(screen.queryByText("Visits sidebar")).not.toBeInTheDocument()
      }
      const observed = observe.mock.calls.length
      const queryKey = stalled === "settings" ? ["settings"] : ["noteList"]
      const cacheBefore = queryClient.getQueryData(queryKey)
      await act(async () => {
        finish(
          stalled === "status"
            ? inactive
            : stalled === "settings"
              ? readySettings
              : { data: { appointments: [{ id: "late" }] } },
        )
      })
      expect(queryClient.getQueryData(queryKey)).toEqual(cacheBefore)
      expect(observe).toHaveBeenCalledTimes(observed)
      expect(router?.state.revalidation).toBe("idle")
      if (stalled !== "list") {
        // A later support-view verification cannot bypass the failed settings
        // loader. Access must be rechecked through the router before rendering.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS)
        })
        expect(observe.mock.calls.length).toBeGreaterThan(observed)
        expect(
          screen.getByRole("heading", {
            name: "Couldn’t verify account access",
          }),
        ).toBeVisible()
        expect(screen.queryByText("Visits sidebar")).not.toBeInTheDocument()
      }
      detail.mockResolvedValue({
        data: { notes: [{ id: "synthetic_note" }] },
      })
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Try again" }))
      })
      expect(screen.getByText("Visit loaded: synthetic_note")).toBeVisible()
    },
  )
})
