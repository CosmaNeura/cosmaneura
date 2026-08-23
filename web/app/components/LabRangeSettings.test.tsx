import { beforeEach, describe, expect, it, vi } from "vitest"

import { LabRangeSettings } from "./LabRangeSettings"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const labRangeList = vi.fn()
const labRangeUpsert = vi.fn()
const labRangeReset = vi.fn()

vi.mock("~/configuration/client", () => ({
  labRangeList: (...a: unknown[]) => labRangeList(...a),
  labRangeUpsert: (...a: unknown[]) => labRangeUpsert(...a),
  labRangeReset: (...a: unknown[]) => labRangeReset(...a),
}))
vi.mock("~/hooks/use-toast", () => ({ toast: vi.fn() }))

const DEFAULT_TSH = {
  biomarker: "tsh",
  display_name: "TSH",
  unit: "mIU/L",
  standard_low: 0.4,
  standard_high: 4.0,
  optimal_low: 0.5,
  optimal_high: 2.5,
  source_url: null,
  is_override: false,
}

beforeEach(() => {
  labRangeList.mockReset().mockResolvedValue({ data: [DEFAULT_TSH] })
  labRangeUpsert.mockReset().mockResolvedValue({ error: undefined })
  labRangeReset.mockReset().mockResolvedValue({ error: undefined })
})

describe("LabRangeSettings", () => {
  it("saves an edited optimal range as an override", async () => {
    render(<LabRangeSettings />)

    await waitFor(() => expect(screen.getByText("TSH")).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText("TSH opt high"), {
      target: { value: "2.0" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(labRangeUpsert).toHaveBeenCalledWith({
        path: { biomarker: "tsh" },
        body: {
          standard_low: 0.4,
          standard_high: 4.0,
          optimal_low: 0.5,
          optimal_high: 2.0,
          source_url: null,
        },
      }),
    )
  })

  it("offers reset only for overridden markers", async () => {
    labRangeList.mockResolvedValue({
      data: [{ ...DEFAULT_TSH, is_override: true }],
    })
    render(<LabRangeSettings />)

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Reset" })).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole("button", { name: "Reset" }))

    await waitFor(() =>
      expect(labRangeReset).toHaveBeenCalledWith({
        path: { biomarker: "tsh" },
      }),
    )
  })
})
