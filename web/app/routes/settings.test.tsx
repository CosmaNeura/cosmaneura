import type { PropsWithChildren } from "react"
import { RouterProvider, createMemoryRouter, useLoaderData } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { PracticeRosterMember } from "~/configuration/client"
import type { PracticeStatus } from "~/lib/practice-api"

import SettingsPage, { clientLoader } from "./settings"
import { render, screen } from "@testing-library/react"

const client = vi.hoisted(() => ({
  settingsData: vi.fn(),
  practiceRoster: vi.fn(),
  updateSettingsData: vi.fn(),
  zoomAuthUrl: vi.fn(),
  zoomDisconnect: vi.fn(),
}))
vi.mock("~/configuration/client", () => client)

const practiceApi = vi.hoisted(() => ({
  getPractice: vi.fn(),
  getBaaAcceptances: vi.fn(),
}))
vi.mock("~/lib/practice-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/lib/practice-api")>()),
  ...practiceApi,
}))

vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
vi.mock("~/utils/terminology", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/utils/terminology")>()
  return { ...actual, useTerminology: () => actual.getTerminology(null) }
})
vi.mock("@/components/app-sidebar", () => ({ AppSidebar: () => null }))
vi.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: ({ children }: PropsWithChildren) => <div>{children}</div>,
  SidebarInset: ({ children }: PropsWithChildren) => <div>{children}</div>,
}))

function practice(overrides: Partial<PracticeStatus> = {}): PracticeStatus {
  return {
    legal_name: "Test Practice, PLLC",
    join_code: "TESTCODE",
    email_domain: "example.com",
    origin_channel: "direct",
    pastoral_pack: false,
    baa_accepted: true,
    role: "owner",
    attested: true,
    access_authorized: true,
    authorization_basis: "workforce_attestation",
    access_revoked: false,
    access_ends_at: null,
    access_ended: false,
    pilot_access_grant_active: false,
    trial_ends_at: null,
    billing_exempt: true,
    billing_exempt_until: null,
    billing_exempt_active: true,
    billing_enabled: false,
    billing_management_enabled: false,
    billing_portal_available: false,
    billing_checkout_resumable: false,
    billing_subscription_manageable: false,
    billing_checkout_available_at: null,
    entitled: true,
    subscription_status: null,
    seat_summary: null,
    ...overrides,
  }
}

const MEMBERS: PracticeRosterMember[] = [
  {
    member_id: "practice_member_owner",
    display_name: "Pat Owner",
    role: "owner",
    is_self: true,
    seat_active: true,
    revoked: false,
    access_in_force: true,
    access_ends_at: null,
    joined_at: "2026-08-20T20:00:00Z",
  },
  {
    member_id: "practice_member_clinician",
    display_name: "Alex Clinician",
    role: "workforce",
    is_self: false,
    seat_active: true,
    revoked: false,
    access_in_force: true,
    access_ends_at: null,
    joined_at: "2026-08-21T20:00:00Z",
  },
]

function LoadedSettings() {
  const props = { loaderData: useLoaderData() } as Parameters<
    typeof SettingsPage
  >[0]
  return <SettingsPage {...props} />
}

function openSettings() {
  const router = createMemoryRouter(
    [
      {
        path: "/settings",
        loader: (args) =>
          clientLoader(args as Parameters<typeof clientLoader>[0]),
        Component: LoadedSettings,
        hydrateFallbackElement: <div>Loading settings</div>,
      },
    ],
    { initialEntries: ["/settings"] },
  )
  render(<RouterProvider router={router} />)
}

beforeEach(() => {
  vi.clearAllMocks()
  client.settingsData.mockResolvedValue({
    data: {
      biography: "",
      notes_schema: [],
      note_customizations: [],
      zoom_connected: false,
      has_natmed_subscription: false,
      terminology: "doctor_patient",
      billing_recovery_required: false,
    },
    response: new Response(null, { status: 200 }),
  })
  client.practiceRoster.mockResolvedValue({
    data: { members: MEMBERS, invitations: [], occupied_seats: 2 },
    response: new Response(null, { status: 200 }),
  })
  practiceApi.getBaaAcceptances.mockResolvedValue([])
})

describe("settings practice roster", () => {
  it("shows the roster to the practice owner", async () => {
    practiceApi.getPractice.mockResolvedValue(practice())

    openSettings()

    expect(await screen.findByText("Practice members")).toBeInTheDocument()
    expect(screen.getByText("Alex Clinician")).toBeInTheDocument()
    expect(client.practiceRoster).toHaveBeenCalledTimes(1)
  })

  it("never requests the roster for a workforce member", async () => {
    practiceApi.getPractice.mockResolvedValue(practice({ role: "workforce" }))

    openSettings()

    expect(
      await screen.findByRole("heading", { name: "Practice" }),
    ).toBeInTheDocument()
    expect(screen.queryByText("Practice members")).not.toBeInTheDocument()
    expect(client.practiceRoster).not.toHaveBeenCalled()
  })

  it("loads settings and the practice without waiting on each other", async () => {
    practiceApi.getPractice.mockResolvedValue(practice())
    client.settingsData.mockReturnValue(new Promise(() => {}))

    void clientLoader({} as Parameters<typeof clientLoader>[0])

    await vi.waitFor(() => expect(client.practiceRoster).toHaveBeenCalled())
    expect(practiceApi.getBaaAcceptances).toHaveBeenCalled()
  })
})
