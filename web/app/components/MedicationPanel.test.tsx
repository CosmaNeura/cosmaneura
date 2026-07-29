import { beforeEach, describe, expect, it, vi } from "vitest"

import { MedicationPanel } from "./MedicationPanel"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const medicationList = vi.fn()
const medicationUpdate = vi.fn()
const medicationDelete = vi.fn()
const medicationCreate = vi.fn()
const patientReferenceResources = vi.fn()
const medicationDrugReference = vi.fn()
const medicationNutrientDepletions = vi.fn()

vi.mock("~/configuration/client", () => ({
  medicationList: (...a: unknown[]) => medicationList(...a),
  medicationUpdate: (...a: unknown[]) => medicationUpdate(...a),
  medicationDelete: (...a: unknown[]) => medicationDelete(...a),
  medicationCreate: (...a: unknown[]) => medicationCreate(...a),
  patientReferenceResources: (...a: unknown[]) =>
    patientReferenceResources(...a),
  medicationDrugReference: (...a: unknown[]) => medicationDrugReference(...a),
  medicationNutrientDepletions: (...a: unknown[]) =>
    medicationNutrientDepletions(...a),
}))
vi.mock("~/hooks/use-toast", () => ({ toast: vi.fn() }))

const CONFIRMED = {
  id: "patient_medication_1",
  name: "metformin",
  normalized_name: "metformin",
  rxnorm_cui: "6809",
  kind: "prescription",
  status: "active",
  source: "transcript",
  dosage: "500mg",
  confirmed: true,
}
const UNCONFIRMED = {
  ...CONFIRMED,
  id: "patient_medication_2",
  name: "fish oil",
  confirmed: false,
}

function stubList(
  meds: unknown[],
  hasNatmed = false,
  drugRef: unknown[] = [],
  depletions: unknown[] = [],
) {
  medicationList.mockResolvedValue({ data: meds })
  patientReferenceResources.mockResolvedValue({
    data: {
      has_natmed_subscription: hasNatmed,
      directory: [],
      medications: meds
        .filter((m) => (m as { confirmed: boolean }).confirmed)
        .map((m) => ({
          medication: m,
          links: [
            {
              label: "openFDA drug label",
              url: "https://labels.fda.gov/?search=metformin",
              source: "FDA",
              requires_subscription: false,
            },
          ],
        })),
    },
  })
  medicationDrugReference.mockResolvedValue({
    data: { source: "openFDA (U.S. FDA, public domain)", medications: drugRef },
  })
  medicationNutrientDepletions.mockResolvedValue({
    data: { disclaimer: "For clinician review.", medications: depletions },
  })
}

beforeEach(() => {
  medicationList.mockReset()
  medicationUpdate.mockReset().mockResolvedValue({ error: undefined })
  medicationDelete.mockReset().mockResolvedValue({ error: undefined })
  medicationCreate.mockReset().mockResolvedValue({ error: undefined })
  patientReferenceResources.mockReset()
  medicationDrugReference.mockReset()
  medicationNutrientDepletions.mockReset()
})

describe("MedicationPanel", () => {
  it("separates unconfirmed extracted rows from confirmed ones and shows links", async () => {
    stubList([CONFIRMED, UNCONFIRMED])

    render(<MedicationPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(screen.getByText(/Extracted from the visit/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/fish oil/)).toBeInTheDocument()
    expect(screen.getByText(/metformin/)).toBeInTheDocument()
    // confirmed row surfaces its reference deep link
    expect(screen.getByText(/openFDA drug label/)).toBeInTheDocument()
  })

  it("renders openFDA label sections for a ready drug", async () => {
    stubList([CONFIRMED], false, [
      {
        medication_id: "patient_medication_1",
        name: "metformin",
        status: "ready",
        sections: [
          {
            key: "adverse_reactions",
            label: "Adverse reactions",
            text: "Nausea and vitamin B12 depletion.",
          },
        ],
      },
    ])

    render(<MedicationPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(screen.getByText(/FDA label info/)).toBeInTheDocument(),
    )
    expect(screen.getByText("Adverse reactions")).toBeInTheDocument()
    expect(screen.getByText(/vitamin B12 depletion/)).toBeInTheDocument()
  })

  it("shows a pending state while FDA info is fetched", async () => {
    stubList([CONFIRMED], false, [
      {
        medication_id: "patient_medication_1",
        name: "metformin",
        status: "pending",
        sections: [],
      },
    ])

    render(<MedicationPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(screen.getByText(/Fetching FDA label info/)).toBeInTheDocument(),
    )
  })

  it("renders nutrient depletions with evidence tier and citation", async () => {
    stubList(
      [CONFIRMED],
      false,
      [],
      [
        {
          medication_id: "patient_medication_1",
          name: "metformin",
          depletions: [
            {
              nutrient: "Vitamin B12",
              evidence: "well_established",
              mechanism: "Metformin lowers serum B12.",
              citation: {
                reference: "de Jager J, et al. BMJ. 2010;340:c2181.",
                url: "https://doi.org/10.1136/bmj.c2181",
              },
            },
          ],
        },
      ],
    )

    render(<MedicationPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(
        screen.getByText(/Possible nutrient depletions/),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText("Vitamin B12")).toBeInTheDocument()
    expect(screen.getByText(/well established/)).toBeInTheDocument()
    const source = screen.getByRole("link", { name: /source/ })
    expect(source).toHaveAttribute("href", "https://doi.org/10.1136/bmj.c2181")
  })

  it("confirms an extracted medication", async () => {
    stubList([UNCONFIRMED])

    render(<MedicationPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Confirm" }),
      ).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }))

    await waitFor(() =>
      expect(medicationUpdate).toHaveBeenCalledWith({
        path: { medication_id: "patient_medication_2" },
        body: { confirmed: true },
      }),
    )
  })

  it("prompts to connect NatMed when unsubscribed", async () => {
    stubList([CONFIRMED], false)

    render(<MedicationPanel patientId="patient_1" />)

    await waitFor(() =>
      expect(
        screen.getByText(/Connect your NatMed Pro subscription/),
      ).toBeInTheDocument(),
    )
  })
})
