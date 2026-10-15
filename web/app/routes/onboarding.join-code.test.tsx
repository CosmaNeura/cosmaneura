import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import OnboardingPage from "./onboarding"
import { fireEvent, render, screen } from "@testing-library/react"

const apiMocks = vi.hoisted(() => ({
  lookupPractice: vi.fn(),
  joinPractice: vi.fn(),
  createPractice: vi.fn(),
}))

vi.mock("~/lib/practice-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/lib/practice-api")>()),
  ...apiMocks,
}))

vi.mock("@clerk/clerk-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@clerk/clerk-react")>()),
  UserButton: () => null,
  useUser: () => ({
    user: {
      primaryEmailAddress: { emailAddress: "provider@synthetic.invalid" },
    },
  }),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

function openJoinForm() {
  const props = {
    loaderData: {
      accountRecoveryRequired: false,
      practice: null,
      organizationAccess: null,
      channel: "direct",
      email: "doctor@outside.example",
      onboarding_completed: false,
      biography: "",
      viewAsCheck: {
        state: "ready",
        status: {
          request_binding: "inactive",
          can_view_as: false,
          active: false,
          read_only: false,
          diagnostic_code: null,
          expires_at: null,
          actor: null,
          target: null,
        },
      },
    },
  } as Parameters<typeof OnboardingPage>[0]
  render(
    <MemoryRouter>
      <OnboardingPage {...props} />
    </MemoryRouter>,
  )
  fireEvent.click(screen.getByRole("button", { name: "I’ve been invited" }))
  fireEvent.change(screen.getByLabelText("Practice join code"), {
    target: { value: "TESTCODE" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Find" }))
}

describe("legacy join-code recovery", () => {
  it("shows the private wrong-domain recovery banner and clears it on Back", async () => {
    apiMocks.lookupPractice.mockResolvedValue({
      practice: null,
      mismatch: true,
    })
    openJoinForm()

    expect(
      await screen.findByText("That code did not accept this inbox."),
    ).toBeInTheDocument()
    expect(screen.getByText(/you’re on @outside.example/)).toBeInTheDocument()
    expect(screen.getByText(/Sign in with your work email/)).toBeInTheDocument()
    expect(apiMocks.lookupPractice).toHaveBeenCalledWith("TESTCODE")
    expect(apiMocks.joinPractice).not.toHaveBeenCalled()
    expect(apiMocks.createPractice).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: /Back/i }))
    fireEvent.click(screen.getByRole("button", { name: "This is my practice" }))
    expect(
      screen.queryByText("That code did not accept this inbox."),
    ).not.toBeInTheDocument()
  })

  it("keeps unknown codes on the join form without a mismatch banner", async () => {
    apiMocks.lookupPractice.mockResolvedValue({
      practice: null,
      mismatch: false,
    })
    openJoinForm()

    expect(
      await screen.findByText("No practice matches that code."),
    ).toBeInTheDocument()
    expect(screen.getByLabelText("Practice join code")).toBeInTheDocument()
    expect(
      screen.queryByText("That code did not accept this inbox."),
    ).not.toBeInTheDocument()
    expect(apiMocks.joinPractice).not.toHaveBeenCalled()
  })

  it("shows a permitted practice without taking the wrong-domain recovery path", async () => {
    apiMocks.lookupPractice.mockResolvedValue({
      mismatch: false,
      practice: {
        legal_name: "Synthetic Practice",
        email_domain: "outside.example",
        join_code: "TESTCODE",
        baa_accepted: true,
      },
    })
    openJoinForm()

    expect(await screen.findByText("Synthetic Practice")).toBeInTheDocument()
    expect(screen.getByLabelText("Practice join code")).toBeInTheDocument()
    expect(
      screen.queryByText("That code did not accept this inbox."),
    ).not.toBeInTheDocument()
    expect(apiMocks.joinPractice).not.toHaveBeenCalled()
  })

  it("joins through the workforce attestation with every acknowledgement", async () => {
    apiMocks.lookupPractice.mockResolvedValue({
      mismatch: false,
      practice: {
        legal_name: "Synthetic Practice",
        email_domain: "outside.example",
        join_code: "TESTCODE",
        baa_accepted: true,
      },
    })
    apiMocks.joinPractice.mockReturnValue(new Promise(() => {}))
    openJoinForm()

    const join = await screen.findByRole("button", {
      name: "Attest and join Synthetic Practice",
    })
    for (const box of screen.getAllByRole("checkbox")) fireEvent.click(box)
    fireEvent.change(screen.getByLabelText("Typed name"), {
      target: { value: "Riley Clinician" },
    })
    fireEvent.click(join)

    expect(apiMocks.joinPractice).toHaveBeenCalledWith({
      join_code: "TESTCODE",
      attestation_name: "Riley Clinician",
      checks: ["workforce", "not-party", "direction", "phi"],
    })
  })

  it("offers no attestation for a practice without a BAA", async () => {
    apiMocks.lookupPractice.mockResolvedValue({
      mismatch: false,
      practice: {
        legal_name: "Unsigned Practice",
        email_domain: "outside.example",
        join_code: "TESTCODE",
        baa_accepted: false,
      },
    })
    openJoinForm()

    expect(
      await screen.findByText(
        "This practice has not accepted the Cosmaneura Inc BAA. Capture stays locked.",
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Attest and join/ }),
    ).not.toBeInTheDocument()
  })
})
