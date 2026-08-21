import { beforeEach, describe, expect, it, vi } from "vitest"

import { TerminologySettings, saveTerminology } from "./TerminologySettings"
import { render, screen } from "@testing-library/react"

const updateSettingsData = vi.fn()
vi.mock("~/configuration/client", () => ({
  updateSettingsData: (...args: unknown[]) => updateSettingsData(...args),
}))

const toast = vi.fn()
vi.mock("~/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => toast(...args),
}))

const invalidateQueries = vi.fn()
vi.mock("~/configuration/tanstack-query", () => ({
  queryClient: {
    invalidateQueries: (...args: unknown[]) => invalidateQueries(...args),
  },
}))

vi.mock("client/@tanstack/react-query.gen", () => ({
  settingsDataOptions: () => ({ queryKey: ["settingsData"] }),
}))

beforeEach(() => {
  updateSettingsData.mockReset()
  toast.mockReset()
  invalidateQueries.mockReset()
})

describe("TerminologySettings", () => {
  it("renders the current selection", () => {
    render(
      <TerminologySettings
        terminology="therapist_client"
        biography="behavioral health"
      />,
    )

    expect(screen.getByText("Terminology")).toBeInTheDocument()
    expect(
      screen.getByRole("radio", { name: /Therapist \/ Client/ }),
    ).toHaveAttribute("aria-checked", "true")
    expect(
      screen.getByRole("radio", { name: /Doctor \/ Patient/ }),
    ).toHaveAttribute("aria-checked", "false")
  })
})

describe("saveTerminology", () => {
  it("persists the selection and keeps the biography", async () => {
    updateSettingsData.mockResolvedValue({ error: undefined })

    const saved = await saveTerminology("counselor_client", "my bio")

    expect(saved).toBe(true)
    expect(updateSettingsData).toHaveBeenCalledWith({
      body: { biography: "my bio", terminology: "counselor_client" },
    })
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["settingsData"],
    })
  })

  it("reports failure without invalidating the cache", async () => {
    updateSettingsData.mockResolvedValue({ error: { detail: "nope" } })

    const saved = await saveTerminology("therapist_client", null)

    expect(saved).toBe(false)
    expect(invalidateQueries).not.toHaveBeenCalled()
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ variant: "destructive" }),
    )
  })
})
