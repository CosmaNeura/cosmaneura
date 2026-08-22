import { beforeEach, describe, expect, it, vi } from "vitest"

import { LabResultsPanel } from "./LabResultsPanel"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const labPanelList = vi.fn()
const labPanelCreate = vi.fn()
const labPanelDelete = vi.fn()
const labEvaluations = vi.fn()

vi.mock("~/configuration/client", () => ({
  labPanelList: (...a: unknown[]) => labPanelList(...a),
  labPanelCreate: (...a: unknown[]) => labPanelCreate(...a),
  labPanelDelete: (...a: unknown[]) => labPanelDelete(...a),
  labEvaluations: (...a: unknown[]) => labEvaluations(...a),
}))
vi.mock("~/hooks/use-toast", () => ({ toast: vi.fn() }))

const PANEL = {
  id: "lab_panel_1",
  source: "manual",
  status: "completed",
  collected_on: "2026-07-01",
  created_at: "2026-07-02T00:00:00Z",
  results: [
    {
      id: "lab_result_1",
      biomarker: "vitamin_d_25oh",
      raw_name: "Vitamin D",
      value: 28.5,
      unit: "ng/mL",
    },
  ],
}

beforeEach(() => {
  labPanelList.mockReset().mockResolvedValue({ data: [] })
  labPanelCreate.mockReset().mockResolvedValue({ error: undefined })
  labPanelDelete.mockReset().mockResolvedValue({ error: undefined })
  labEvaluations.mockReset().mockResolvedValue({ data: { entries: [] } })
})

describe("LabResultsPanel", () => {
  it("renders existing panels with results", async () => {
    labPanelList.mockResolvedValue({ data: [PANEL] })

    render(<LabResultsPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(screen.getByText("Vitamin D")).toBeInTheDocument(),
    )
    expect(screen.getByText(/28.5/)).toBeInTheDocument()
    expect(screen.getByText(/2026-07-01/)).toBeInTheDocument()
  })

  it("shows dual-range interpretation with classification", async () => {
    labPanelList.mockResolvedValue({ data: [PANEL] })
    labEvaluations.mockResolvedValue({
      data: {
        entries: [
          {
            biomarker: "vitamin_d_25oh",
            display_name: "Vitamin D, 25-OH",
            value: 35,
            unit: "ng/mL",
            collected_on: "2026-07-01",
            classification: "within_standard",
            standard_low: 30,
            standard_high: 100,
            optimal_low: 40,
            optimal_high: 60,
            source_url: null,
          },
        ],
      },
    })

    render(<LabResultsPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(
        screen.getByText(/In standard range, outside optimal/),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText(/optimal 40–60/)).toBeInTheDocument()
  })

  it("shows an empty state when there are no labs", async () => {
    render(<LabResultsPanel patientId="patient_1" />)
    await waitFor(() =>
      expect(screen.getByText(/No labs recorded yet/)).toBeInTheDocument(),
    )
  })

  it("submits manually entered results", async () => {
    render(<LabResultsPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add results" }),
      ).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole("button", { name: "Add results" }))

    fireEvent.change(screen.getByPlaceholderText(/Biomarker/), {
      target: { value: "TSH" },
    })
    fireEvent.change(screen.getByPlaceholderText("Value"), {
      target: { value: "2.1" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save labs" }))

    await waitFor(() =>
      expect(labPanelCreate).toHaveBeenCalledWith({
        path: { patient_id: "patient_1" },
        body: {
          collected_on: null,
          results: [{ name: "TSH", value: 2.1, unit: null }],
        },
      }),
    )
  })

  it("deletes a panel", async () => {
    labPanelList.mockResolvedValue({ data: [PANEL] })
    render(<LabResultsPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Delete" }),
      ).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole("button", { name: "Delete" }))

    await waitFor(() =>
      expect(labPanelDelete).toHaveBeenCalledWith({
        path: { lab_panel_id: "lab_panel_1" },
      }),
    )
  })
})
