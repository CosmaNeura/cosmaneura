import { RouterProvider, createMemoryRouter, useLoaderData } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { PracticeInvitationInspection } from "~/configuration/client"
import {
  PENDING_INVITATION_KEY,
  clearPendingInvitation,
  forgetPendingInvitationOnSignOut,
} from "~/lib/pending-invitation"

import InvitePage, { clientLoader } from "./invite"
import { clientLoader as onboardingLoader } from "./onboarding"
import { fireEvent, render, screen } from "@testing-library/react"

const client = vi.hoisted(() => ({
  inspectPracticeInvitation: vi.fn(),
  acceptPracticeInvitation: vi.fn(),
  settingsData: vi.fn(),
  completeOnboarding: vi.fn(),
  updateSettingsData: vi.fn(),
}))
vi.mock("~/configuration/client", () => client)

const clerk = vi.hoisted(() => ({
  getClient: vi.fn(),
  signOut: vi.fn(),
}))
vi.mock("~/configuration/clerk", () => ({ getClient: clerk.getClient }))
vi.mock("@clerk/clerk-react", () => ({
  useClerk: () => ({ signOut: clerk.signOut }),
}))

const practiceApi = vi.hoisted(() => ({
  getPractice: vi.fn(),
  getOrganizationAccessOpportunity: vi.fn(),
}))
vi.mock("~/lib/practice-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/lib/practice-api")>()),
  ...practiceApi,
}))

const viewAs = vi.hoisted(() => ({ checkViewAsStatus: vi.fn() }))
vi.mock("~/lib/view-as-api", () => viewAs)

function inspection(
  overrides: Partial<PracticeInvitationInspection> = {},
): PracticeInvitationInspection {
  return {
    state: "ready",
    legal_name: "Harbor Family Medicine",
    baa_accepted: true,
    ...overrides,
  }
}

function inspected(overrides: Partial<PracticeInvitationInspection> = {}) {
  client.inspectPracticeInvitation.mockResolvedValue({
    data: inspection(overrides),
    response: new Response(null, { status: 200 }),
  })
}

function fakeClerkSession() {
  let listener: ((resources: { user?: unknown }) => void) | null = null
  const stop = forgetPendingInvitationOnSignOut({
    addListener: (callback) => {
      listener = callback
      return () => {
        listener = null
      }
    },
  })
  return {
    emit: (resources: { user?: unknown }) => listener?.(resources),
    stop,
  }
}

function LoadedInvite() {
  const props = { loaderData: useLoaderData() } as Parameters<
    typeof InvitePage
  >[0]
  return <InvitePage {...props} />
}

function openInvite() {
  const router = createMemoryRouter(
    [
      {
        path: "/invite",
        loader: () => clientLoader(),
        Component: LoadedInvite,
        hydrateFallbackElement: <div>Loading</div>,
      },
      { path: "/onboarding", Component: () => <div>Onboarding page</div> },
    ],
    { initialEntries: ["/invite"] },
  )
  render(<RouterProvider router={router} />)
  return router
}

beforeEach(() => {
  vi.clearAllMocks()
  clearPendingInvitation()
  window.sessionStorage.clear()
  window.history.replaceState(null, "", "/")
  clerk.getClient.mockResolvedValue({ user: { id: "user_invitee" } })
})

describe("invite link", () => {
  it("moves the token out of the address bar into this tab's storage", async () => {
    window.history.replaceState({ idx: 0 }, "", "/invite?x=1#t=secret-token")
    inspected()

    const result = await clientLoader()

    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe(
      "secret-token",
    )
    expect(window.location.hash).toBe("")
    expect(window.location.search).toBe("?x=1")
    expect(window.history.state).toEqual({ idx: 0 })
    expect(client.inspectPracticeInvitation).toHaveBeenCalledWith({
      body: { token: "secret-token" },
    })
    expect(result).toEqual({
      kind: "inspected",
      token: "secret-token",
      inspection: inspection(),
    })
  })

  it("keeps the stored token through the sign-in round trip", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "stored-token")
    inspected()

    await clientLoader()

    expect(client.inspectPracticeInvitation).toHaveBeenCalledWith({
      body: { token: "stored-token" },
    })
  })

  it("stores the token before a signed-out visitor is sent to sign in", async () => {
    window.history.replaceState(null, "", "/invite#t=before-sign-in")
    clerk.getClient.mockResolvedValue(undefined)

    expect(await clientLoader()).toEqual({ kind: "signing_in" })
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe(
      "before-sign-in",
    )
    expect(client.inspectPracticeInvitation).not.toHaveBeenCalled()
  })

  it("shows nothing while a signed-out visitor is sent to sign in", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "stored")
    clerk.getClient.mockResolvedValue(undefined)
    openInvite()

    await vi.waitFor(() =>
      expect(screen.queryByText("Loading")).not.toBeInTheDocument(),
    )
    expect(screen.queryByRole("heading")).not.toBeInTheDocument()
  })

  it("asks for the link when there is no token at all", async () => {
    openInvite()

    expect(
      await screen.findByRole("heading", { name: "Open your invitation link" }),
    ).toBeInTheDocument()
    expect(clerk.getClient).not.toHaveBeenCalled()
  })

  it("forgets an unknown or spent token", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "gone")
    client.inspectPracticeInvitation.mockResolvedValue({
      data: undefined,
      error: { detail: "Invitation not found." },
      response: new Response(null, { status: 404 }),
    })
    expect(await clientLoader()).toEqual({ kind: "unknown" })
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBeNull()

    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "used")
    inspected({ state: "used" })
    await clientLoader()
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBeNull()
  })

  it("requires every acknowledgement and a typed name before joining", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "ready-token")
    inspected()
    client.acceptPracticeInvitation.mockResolvedValue({ data: {} })
    const router = openInvite()

    const join = await screen.findByRole("button", {
      name: "Attest and join Harbor Family Medicine",
    })
    expect(screen.getByText("BAA on file")).toBeInTheDocument()
    expect(join).toBeDisabled()
    const boxes = screen.getAllByRole("checkbox")
    expect(boxes).toHaveLength(4)
    for (const box of boxes) fireEvent.click(box)
    expect(join).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Typed name"), {
      target: { value: "Riley Clinician" },
    })
    expect(join).toBeEnabled()
    fireEvent.click(join)

    expect(await screen.findByText("Onboarding page")).toBeInTheDocument()
    expect(client.acceptPracticeInvitation).toHaveBeenCalledWith({
      body: {
        token: "ready-token",
        attestation_name: "Riley Clinician",
        checks: ["workforce", "not-party", "direction", "phi"],
      },
    })
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBeNull()
    expect(router.state.location.pathname).toBe("/onboarding")
  })

  it("shows why acceptance was refused", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "ready-token")
    inspected()
    client.acceptPracticeInvitation.mockResolvedValue({
      error: {
        detail:
          "This practice can't add more clinicians right now. Ask the practice owner to contact Cosmaneura.",
      },
    })
    openInvite()

    for (const box of await screen.findAllByRole("checkbox")) {
      fireEvent.click(box)
    }
    fireEvent.change(screen.getByLabelText("Typed name"), {
      target: { value: "Riley Clinician" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Attest and join/ }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ask the practice owner to contact Cosmaneura.",
    )
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe(
      "ready-token",
    )
  })

  it("tells a member of another practice to sign up with a different email", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "elsewhere")
    inspected({ state: "member_elsewhere" })
    openInvite()

    expect(
      await screen.findByText(
        "This account already belongs to another practice. Sign up with a different email to join a new one.",
      ),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }))
    expect(clerk.signOut).toHaveBeenCalledWith({ redirectUrl: "/sign-up" })
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
  })

  it("tells a managed-access account to use its organization and forgets the link", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "managed")
    inspected({
      state: "managed_account",
      legal_name: null,
      baa_accepted: false,
    })
    const router = openInvite()

    expect(
      await screen.findByRole("heading", {
        name: "This account uses organization-managed access",
      }),
    ).toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))
    expect(await screen.findByText("Onboarding page")).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/onboarding")
  })

  it("names no practice to the wrong account", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "wrong")
    inspected({ state: "wrong_account", legal_name: null, baa_accepted: false })
    openInvite()

    expect(
      await screen.findByText(
        "This invitation was sent to a different email address. Sign in with that address.",
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Harbor/)).not.toBeInTheDocument()
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe("wrong")
  })
})

describe("no dead end on the invite page", () => {
  it("lets a doctor leave a check that failed and forgets the link", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "throttled")
    client.inspectPracticeInvitation.mockResolvedValue({
      data: undefined,
      error: { detail: "Too many attempts. Try again later." },
      response: new Response(null, { status: 429 }),
    })
    const router = openInvite()

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Try again later.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Not now" }))

    expect(await screen.findByText("Onboarding page")).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/onboarding")
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBeNull()
  })

  it("lets the wrong account continue without the invitation", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "not-mine")
    inspected({ state: "wrong_account", legal_name: null, baa_accepted: false })
    const router = openInvite()

    fireEvent.click(await screen.findByRole("button", { name: "Not now" }))

    expect(await screen.findByText("Onboarding page")).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/onboarding")
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBeNull()
  })

  it("keeps the link when the wrong account signs out to switch", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "switching")
    inspected({ state: "wrong_account", legal_name: null, baa_accepted: false })
    const session = fakeClerkSession()
    session.emit({ user: { id: "user_wrong" } })
    openInvite()

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Sign in with another account",
      }),
    )
    session.emit({ user: null })

    expect(clerk.signOut).toHaveBeenCalledWith({
      redirectUrl: "/sign-in?redirect_url=%2Finvite",
    })
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe(
      "switching",
    )
    session.stop()
  })

  it("forgets the link whenever this tab otherwise signs out", () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "someone-else")
    const session = fakeClerkSession()

    session.emit({ user: null })
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe(
      "someone-else",
    )
    session.emit({ user: { id: "user_a" } })
    session.emit({ user: null })

    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBeNull()
    session.stop()
  })
})

describe("onboarding with a pending invitation", () => {
  beforeEach(() => {
    viewAs.checkViewAsStatus.mockResolvedValue({
      state: "ready",
      status: { active: false, diagnostic_code: null },
    })
    client.settingsData.mockResolvedValue({
      data: {
        onboarding_completed: false,
        is_admin: false,
        practice_ready: false,
      },
      response: new Response(null, { status: 200 }),
    })
    practiceApi.getOrganizationAccessOpportunity.mockResolvedValue(null)
  })

  function load() {
    return onboardingLoader({
      request: new Request("https://app.cosmaneura.com/onboarding"),
    } as Parameters<typeof onboardingLoader>[0])
  }

  it("sends a doctor without a practice back to the invitation", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "pending")
    practiceApi.getPractice.mockResolvedValue(null)

    const outcome = await load().catch((thrown: unknown) => thrown)

    expect(outcome).toBeInstanceOf(Response)
    expect((outcome as Response).headers.get("Location")).toBe("/invite")
  })

  it("does not send the doctor back to an invitation that needs nothing from them", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "parked")
    inspected({ state: "wrong_account", legal_name: null, baa_accepted: false })
    await clientLoader()
    practiceApi.getPractice.mockResolvedValue(null)

    await expect(load()).resolves.toMatchObject({ practice: null })
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe("parked")
  })

  it("does not loop back after the invitation could not be checked", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "unchecked")
    client.inspectPracticeInvitation.mockResolvedValue({
      data: undefined,
      error: { detail: "This invitation could not be verified." },
      response: new Response(null, { status: 403 }),
    })
    await clientLoader()
    practiceApi.getPractice.mockResolvedValue(null)

    await expect(load()).resolves.toMatchObject({ practice: null })
  })

  it("sends the doctor back again once the link is opened again", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "parked")
    inspected({ state: "practice_not_ready" })
    await clientLoader()
    window.history.replaceState(null, "", "/invite#t=parked")
    clerk.getClient.mockResolvedValue(undefined)
    await clientLoader()
    practiceApi.getPractice.mockResolvedValue(null)

    const outcome = await load().catch((thrown: unknown) => thrown)

    expect((outcome as Response).headers.get("Location")).toBe("/invite")
  })

  it("stays on onboarding once the doctor has a practice", async () => {
    window.sessionStorage.setItem(PENDING_INVITATION_KEY, "pending")
    practiceApi.getPractice.mockResolvedValue({ legal_name: "Harbor" })

    await expect(load()).resolves.toMatchObject({
      practice: { legal_name: "Harbor" },
    })
  })
})
