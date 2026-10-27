import type { PropsWithChildren } from "react"
import { RouterProvider, createMemoryRouter, useLoaderData } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { PracticeRosterMember } from "~/configuration/client"
import type { PracticeStatus } from "~/lib/practice-api"

import SettingsPage, { clientLoader } from "./settings"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const client = vi.hoisted(() => ({
  settingsData: vi.fn(),
  practiceRoster: vi.fn(),
  updateSettingsData: vi.fn(),
  zoomAuthUrl: vi.fn(),
  zoomDisconnect: vi.fn(),
  getNoteTemplatePreference: vi.fn(),
  updateNoteTemplatePreference: vi.fn(),
}))
vi.mock("~/configuration/client", () => client)

const practiceApi = vi.hoisted(() => ({
  getPractice: vi.fn(),
  getBaaAcceptances: vi.fn(),
  getBaaCurrent: vi.fn(),
  acceptPracticeBaa: vi.fn(),
}))
vi.mock("~/lib/practice-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/lib/practice-api")>()),
  ...practiceApi,
}))

vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
const supportView = vi.hoisted(() => ({ active: false }))
vi.mock("~/components/ViewAsRouteGuard", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/components/ViewAsRouteGuard")>()),
  useViewAsActive: () => supportView.active,
}))
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
  return router
}

beforeEach(() => {
  vi.clearAllMocks()
  supportView.active = false
  client.getNoteTemplatePreference.mockResolvedValue({
    data: { default_schema_ref: null, revision: 0 },
    response: new Response(null, { status: 200 }),
  })
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
  practiceApi.getBaaCurrent.mockResolvedValue({
    doc_id: "CN-BAA-CUST-CW",
    version: "2.3",
    sha256: "f".repeat(64),
    html: "<p>Current synthetic agreement text</p>",
  })
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

  it("tells a revoked clinician when their access ends", async () => {
    practiceApi.getPractice.mockResolvedValue(
      practice({ role: "workforce", access_ends_at: "2026-09-28T04:00:00Z" }),
    )

    openSettings()

    expect(
      await screen.findByText(
        "Your practice owner ended your access to Cosmas.",
      ),
    ).toBeInTheDocument()
  })

  it("loads settings and the practice without waiting on each other", async () => {
    practiceApi.getPractice.mockResolvedValue(practice())
    client.settingsData.mockReturnValue(new Promise(() => {}))

    void clientLoader({} as Parameters<typeof clientLoader>[0])

    await vi.waitFor(() => expect(client.practiceRoster).toHaveBeenCalled())
    expect(practiceApi.getBaaAcceptances).toHaveBeenCalled()
  })
})

describe("settings current practice agreement", () => {
  const stale = () =>
    practice({
      baa_accepted: false,
      pilot_access_grant_active: true,
      entitled: false,
      billing_exempt: false,
      billing_exempt_active: false,
    })

  it("lets an existing pilot owner review without accepting or visiting onboarding", async () => {
    practiceApi.getPractice.mockResolvedValue(stale())
    openSettings()

    const review = await screen.findByRole("button", {
      name: "Review current BAA",
    })
    expect(practiceApi.getBaaCurrent).not.toHaveBeenCalled()
    fireEvent.click(review)

    expect(
      await screen.findByText("Current synthetic agreement text"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("region", { name: "Review practice agreement" }),
    ).toHaveFocus()
    expect(screen.getByRole("button", { name: "I Agree" })).toBeDisabled()
    expect(practiceApi.acceptPracticeBaa).not.toHaveBeenCalled()

    fireEvent.click(
      screen.getByRole("button", { name: "I’m not authorized to sign this" }),
    )
    expect(
      screen.getByRole("button", { name: "Review current BAA" }),
    ).toHaveFocus()
    expect(practiceApi.acceptPracticeBaa).not.toHaveBeenCalled()
  })

  it.each([
    ["workforce", { role: "workforce" }],
    ["revoked owner", { access_revoked: true }],
    ["ended owner", { access_ended: true }],
    ["current agreement", { baa_accepted: true }],
    [
      "organization-managed owner",
      { authorization_basis: "organization_roster" },
    ],
  ] as const)("does not offer signing to %s", async (_label, overrides) => {
    practiceApi.getPractice.mockResolvedValue({ ...stale(), ...overrides })
    openSettings()
    await screen.findByRole("heading", { name: "Practice" })
    expect(
      screen.queryByRole("button", { name: "Review current BAA" }),
    ).not.toBeInTheDocument()
    expect(practiceApi.getBaaCurrent).not.toHaveBeenCalled()
    expect(practiceApi.acceptPracticeBaa).not.toHaveBeenCalled()
  })

  it("does not offer signing while viewing another account", async () => {
    supportView.active = true
    practiceApi.getPractice.mockResolvedValue(stale())
    openSettings()
    await screen.findByRole("heading", { name: "Practice" })
    expect(
      screen.queryByRole("button", { name: "Review current BAA" }),
    ).not.toBeInTheDocument()
    expect(practiceApi.getBaaCurrent).not.toHaveBeenCalled()
  })

  it("refreshes practice status and acceptance history after explicit exact-document acceptance", async () => {
    const accepted = practice({
      entitled: false,
      billing_exempt: false,
      billing_exempt_active: false,
    })
    practiceApi.getPractice
      .mockResolvedValueOnce(stale())
      .mockResolvedValue(accepted)
    practiceApi.acceptPracticeBaa.mockResolvedValueOnce(accepted)
    practiceApi.getBaaAcceptances.mockResolvedValueOnce([]).mockResolvedValue([
      {
        doc_id: "CN-BAA-CUST-CW",
        version: "2.3",
        content_sha256: "f".repeat(64),
        accepted_at_utc: "2026-10-01T18:00:00Z",
        accepted_by_name: "Synthetic Owner",
        accepted_by_title: "Practice owner",
        accepted_by_email: "owner@synthetic.invalid",
      },
    ])
    openSettings()
    fireEvent.click(
      await screen.findByRole("button", { name: "Review current BAA" }),
    )
    await screen.findByText("Current synthetic agreement text")
    fireEvent.change(
      screen.getByRole("textbox", { name: /accepting individual/i }),
      { target: { value: "Synthetic Owner" } },
    )
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
      target: { value: "Practice owner" },
    })
    fireEvent.click(
      screen.getByRole("checkbox", { name: /I have read and agree/i }),
    )
    fireEvent.click(screen.getByRole("button", { name: "I Agree" }))

    expect(
      await screen.findByText(/agreement status is up to date/i),
    ).toBeInTheDocument()
    await waitFor(() =>
      expect(practiceApi.getBaaAcceptances).toHaveBeenCalledTimes(2),
    )
    expect(
      await screen.findByText(/accepted by Synthetic Owner/),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Review current BAA" }),
    ).not.toBeInTheDocument()
    expect(screen.getByText("Practice access is paused")).toBeInTheDocument()
    expect(practiceApi.acceptPracticeBaa).toHaveBeenCalledExactlyOnceWith({
      doc_id: "CN-BAA-CUST-CW",
      version: "2.3",
      sha256: "f".repeat(64),
      signature_name: "Synthetic Owner",
      signature_title: "Practice owner",
      checks: ["baa"],
    })
  })

  it("keeps signing disabled if the displayed agreement version is stale", async () => {
    practiceApi.getPractice.mockResolvedValue(stale())
    practiceApi.getBaaCurrent.mockResolvedValueOnce({
      doc_id: "CN-BAA-CUST-CW",
      version: "2.2",
      sha256: "a".repeat(64),
      html: "<p>Stale text</p>",
    })
    openSettings()
    fireEvent.click(
      await screen.findByRole("button", { name: "Review current BAA" }),
    )
    await screen.findByText(/agreement version could not be verified/i)
    expect(screen.queryByText("Stale text")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "I Agree" })).toBeDisabled()
    expect(practiceApi.acceptPracticeBaa).not.toHaveBeenCalled()
  })

  it("keeps the agreement open when the backend rejects current owner authority", async () => {
    practiceApi.getPractice.mockResolvedValue(stale())
    practiceApi.acceptPracticeBaa.mockRejectedValueOnce(
      new Error("Only the practice owner can accept the BAA."),
    )
    openSettings()
    fireEvent.click(
      await screen.findByRole("button", { name: "Review current BAA" }),
    )
    await screen.findByText("Current synthetic agreement text")
    fireEvent.change(
      screen.getByRole("textbox", { name: /accepting individual/i }),
      { target: { value: "Synthetic Owner" } },
    )
    fireEvent.click(
      screen.getByRole("checkbox", { name: /I have read and agree/i }),
    )
    fireEvent.click(screen.getByRole("button", { name: "I Agree" }))
    expect(
      await screen.findByText("Only the practice owner can accept the BAA."),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("region", { name: "Review practice agreement" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/agreement status is up to date/i),
    ).not.toBeInTheDocument()
    expect(practiceApi.getBaaAcceptances).toHaveBeenCalledTimes(1)
  })

  it("removes an open signing form if support view becomes active", async () => {
    practiceApi.getPractice.mockResolvedValue(stale())
    const router = openSettings()
    fireEvent.click(
      await screen.findByRole("button", { name: "Review current BAA" }),
    )
    await screen.findByText("Current synthetic agreement text")
    await act(async () => {
      supportView.active = true
      await router.revalidate()
    })
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "I Agree" }),
      ).not.toBeInTheDocument(),
    )
    expect(
      screen.queryByRole("button", { name: "Review current BAA" }),
    ).not.toBeInTheDocument()
    expect(practiceApi.acceptPracticeBaa).not.toHaveBeenCalled()
  })
})

describe("settings layout", () => {
  it("has one page heading, a heading per section, and jump links to them", async () => {
    practiceApi.getPractice.mockResolvedValue(practice())
    openSettings()

    const nav = await screen.findByRole("navigation", {
      name: "Settings sections",
    })
    expect(
      Array.from(nav.querySelectorAll("a")).map((a) => a.textContent),
    ).toEqual(["Practice", "Biography", "Note types", "Zoom"])
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    for (const name of ["Practice", "Biography", "Zoom Integration"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeVisible()
    }
  })

  it("moves to a section and focuses it, without smooth scrolling under reduced motion", async () => {
    practiceApi.getPractice.mockResolvedValue(practice())
    const scrollIntoView = vi
      .spyOn(Element.prototype, "scrollIntoView")
      .mockImplementation(() => {})
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: query === "(prefers-reduced-motion: reduce)",
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    )
    openSettings()

    const link = await screen.findByRole("link", { name: "Zoom" })
    expect(link).toHaveAttribute("href", "#settings-zoom")
    fireEvent.click(link)

    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "start",
    })
    expect(screen.getByRole("region", { name: "Zoom" })).toHaveFocus()
    scrollIntoView.mockRestore()
    vi.unstubAllGlobals()
  })

  it("keeps the router's history entry when jumping", async () => {
    practiceApi.getPractice.mockResolvedValue(practice())
    vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {})
    window.history.replaceState({ usr: null, key: "abc123", idx: 4 }, "")
    openSettings()

    fireEvent.click(await screen.findByRole("link", { name: "Biography" }))

    expect(window.history.state).toMatchObject({ key: "abc123", idx: 4 })
    expect(window.location.hash).toBe("#settings-biography")
    vi.restoreAllMocks()
  })

  it("leaves modifier clicks to the browser", async () => {
    practiceApi.getPractice.mockResolvedValue(practice())
    openSettings()

    const link = await screen.findByRole("link", { name: "Zoom" })
    const event = new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      metaKey: true,
    })
    link.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(false)
  })

  it("omits links for sections that are not shown", async () => {
    practiceApi.getPractice.mockResolvedValue(null)
    openSettings()

    const nav = await screen.findByRole("navigation", {
      name: "Settings sections",
    })
    const labels = Array.from(nav.querySelectorAll("a")).map(
      (a) => a.textContent,
    )
    expect(labels).not.toContain("Practice")
    expect(labels).not.toContain("AI output")
    expect(screen.queryByRole("region", { name: "Practice" })).toBeNull()
  })

  it("offers no jump links in the billing-recovery-only view", async () => {
    practiceApi.getPractice.mockResolvedValue(practice({ entitled: false }))
    client.settingsData.mockResolvedValue({
      data: {
        biography: "",
        notes_schema: [],
        note_customizations: [],
        zoom_connected: false,
        has_natmed_subscription: false,
        terminology: "doctor_patient",
        billing_recovery_required: true,
      },
      response: new Response(null, { status: 200 }),
    })
    openSettings()

    expect(
      await screen.findByRole("heading", { level: 2, name: "Practice" }),
    ).toBeVisible()
    expect(
      screen.queryByRole("navigation", { name: "Settings sections" }),
    ).toBeNull()
    expect(screen.queryByRole("heading", { name: "Biography" })).toBeNull()
  })
})
