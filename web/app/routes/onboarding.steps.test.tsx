import { beforeEach, describe, expect, it, vi } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import { AllSetStep, BiographyStep } from "./onboarding"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const client = vi.hoisted(() => ({
  updateSettingsData: vi.fn(),
  completeOnboarding: vi.fn(),
}))
vi.mock("~/configuration/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/configuration/client")>()),
  ...client,
}))

const toast = vi.hoisted(() => vi.fn())
vi.mock("~/hooks/use-toast", () => ({ toast }))
vi.mock("~/components/shared/PracticeJoinPanel", () => ({
  PracticeJoinPanel: () => null,
}))

beforeEach(() => {
  client.updateSettingsData.mockReset()
  client.completeOnboarding.mockReset()
  toast.mockReset()
})

function fillBiography(text: string) {
  fireEvent.change(screen.getByLabelText("Biography"), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save & Continue" }))
}

describe("onboarding biography step", () => {
  it("advances once the biography is saved", async () => {
    client.updateSettingsData.mockResolvedValue({ data: {}, error: undefined })
    const onComplete = vi.fn()
    render(
      <BiographyStep
        biography={null}
        onComplete={onComplete}
        onSkip={vi.fn()}
      />,
    )

    fillBiography("Synthetic biography")

    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce())
    expect(client.updateSettingsData).toHaveBeenCalledWith({
      body: { biography: "Synthetic biography" },
    })
  })

  it.each([
    ["the server rejects it", { data: undefined, error: { detail: "x" } }],
  ])("stays on the step with the text intact when %s", async (_, result) => {
    client.updateSettingsData.mockResolvedValue(result)
    const onComplete = vi.fn()
    render(
      <BiographyStep
        biography={null}
        onComplete={onComplete}
        onSkip={vi.fn()}
      />,
    )

    fillBiography("Synthetic biography")

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't save your biography",
          variant: "destructive",
        }),
      ),
    )
    expect(onComplete).not.toHaveBeenCalled()
    expect(screen.getByLabelText("Biography")).toHaveValue(
      "Synthetic biography",
    )
    expect(
      screen.getByRole("button", { name: "Save & Continue" }),
    ).toBeEnabled()
  })
})

describe("onboarding final step", () => {
  it("goes to notes once setup is complete", async () => {
    client.completeOnboarding.mockResolvedValue({
      data: null,
      error: undefined,
    })
    render(<AllSetStep practice={{} as PracticeStatus} />)

    fireEvent.click(screen.getByRole("button", { name: "Go to Cosmas" }))

    await waitFor(() => expect(window.location.pathname).toBe("/notes"))
  })

  it("shows the backend's explanation for a practice-access problem", async () => {
    client.completeOnboarding.mockResolvedValue({
      data: undefined,
      error: {
        detail:
          "Name or join a practice and accept the BAA before using Cosmas.",
      },
    })
    render(<AllSetStep practice={{} as PracticeStatus} />)

    fireEvent.click(screen.getByRole("button", { name: "Go to Cosmas" }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          description:
            "Name or join a practice and accept the BAA before using Cosmas.",
        }),
      ),
    )
  })

  it("stays put and lets the user retry when finishing setup fails", async () => {
    client.completeOnboarding.mockResolvedValue({
      data: undefined,
      error: { detail: "x" },
    })
    const start = window.location.href
    render(<AllSetStep practice={{} as PracticeStatus} />)

    fireEvent.click(screen.getByRole("button", { name: "Go to Cosmas" }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Couldn't finish setup" }),
      ),
    )
    expect(window.location.href).toBe(start)
    expect(screen.getByRole("button", { name: "Go to Cosmas" })).toBeEnabled()
  })
})
