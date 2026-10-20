import { beforeEach, describe, expect, it, vi } from "vitest"

import { MedicationPanel } from "./MedicationPanel"
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"

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
  appointment_id: "visit_current",
  source_appointment_date: "2026-10-20T12:00:00Z",
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

    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )

    await waitFor(() =>
      expect(screen.getByText(/Extracted from this visit/)).toBeInTheDocument(),
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

    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )

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

    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )

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

    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )

    await waitFor(() =>
      expect(
        screen.getByText(/Possible nutrient depletions/),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText("Vitamin B12")).toBeInTheDocument()
    expect(screen.getByText(/well established/)).toBeInTheDocument()
    const source = screen.getByRole("link", { name: /source/ })
    expect(source).toHaveAttribute("href", "https://doi.org/10.1136/bmj.c2181")
    // the seed is partial by design; the server ships a caveat saying so and
    // it has to reach the provider, not just the response body
    expect(screen.getByTestId("depletion-disclaimer")).toHaveTextContent(
      "For clinician review.",
    )
  })

  it("hides the depletion disclaimer when no depletion is shown", async () => {
    stubList([CONFIRMED], false, [], [])

    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )

    await waitFor(() =>
      expect(screen.getByTestId("confirmed-medication")).toBeInTheDocument(),
    )
    expect(screen.queryByTestId("depletion-disclaimer")).not.toBeInTheDocument()
  })

  it("confirms an extracted medication", async () => {
    stubList([UNCONFIRMED])

    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Confirm for patient list" }),
      ).toBeInTheDocument(),
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm for patient list" }),
    )

    await waitFor(() =>
      expect(medicationUpdate).toHaveBeenCalledWith({
        path: { medication_id: "patient_medication_2" },
        body: { confirmed: true },
      }),
    )
  })

  it("keeps dated historical and unknown suggestions read-only while confirmed medications remain patient-wide", async () => {
    stubList([
      CONFIRMED,
      UNCONFIRMED,
      {
        ...UNCONFIRMED,
        id: "history",
        name: "losartan",
        dosage: "50 mg daily",
        appointment_id: "visit_older",
        source_appointment_date: "2026-10-07T12:00:00Z",
      },
      {
        ...UNCONFIRMED,
        id: "unknown",
        name: "Unknown source medication",
        appointment_id: null,
        source_appointment_date: null,
      },
    ])
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    await screen.findByText(/Historical and undated extraction suggestions/)
    expect(
      screen.getAllByRole("button", { name: "Confirm for patient list" }),
    ).toHaveLength(1)
    const historical = screen.getAllByTestId("historical-medication")
    historical.forEach((row) =>
      expect(within(row).queryByRole("button")).not.toBeInTheDocument(),
    )
    expect(within(historical[0]).getByRole("link")).toHaveAttribute(
      "href",
      "/notes/visit_older",
    )
    expect(historical[0]).toHaveTextContent("10/7/26")
    expect(screen.getByText(/Confirm adds to the patient/)).toBeInTheDocument()
    expect(screen.getByTestId("confirmed-medication")).toHaveTextContent(
      "metformin",
    )
    expect(medicationUpdate).not.toHaveBeenCalled()
  })

  it("groups only exact same-visit extraction duplicates and flags different dose descriptions without inferring supersession", async () => {
    const current = {
      ...UNCONFIRMED,
      name: "losartan",
      display_name: "losartan",
      dosage: "25 mg daily",
    }
    stubList([
      current,
      { ...current, id: "duplicate" },
      {
        ...current,
        id: "history",
        dosage: "50 mg daily",
        appointment_id: "visit_older",
      },
      { ...current, id: "unknown1", appointment_id: null },
      { ...current, id: "unknown2", appointment_id: null },
    ])
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    await screen.findByText(/2 matching extractions/)
    expect(screen.getAllByTestId("unconfirmed-medication")).toHaveLength(1)
    expect(screen.getAllByTestId("historical-medication")).toHaveLength(3)
    expect(screen.getByRole("note")).toHaveTextContent(
      "Different extracted dose or schedule descriptions for losartan",
    )
    expect(screen.queryByText(/superseded/i)).not.toBeInTheDocument()
  })

  it("hides old patient state immediately and rejects a late fetch after a patient switch", async () => {
    let resolveOld: (value: unknown) => void = () => {}
    stubList([])
    medicationList.mockImplementation(
      ({ path }: { path: { patient_id: string } }) =>
        path.patient_id === "patient_old"
          ? new Promise((resolve) => {
              resolveOld = resolve
            })
          : Promise.resolve({
              data: [{ ...CONFIRMED, name: "New patient medication" }],
            }),
    )
    const view = render(
      <MedicationPanel patientId="patient_old" appointmentId="visit_old" />,
    )
    view.rerender(
      <MedicationPanel patientId="patient_new" appointmentId="visit_current" />,
    )
    await screen.findByText("New patient medication · 500mg")
    await act(async () => {
      resolveOld({ data: [{ ...CONFIRMED, name: "Old patient medication" }] })
    })
    expect(screen.queryByText(/Old patient medication/)).not.toBeInTheDocument()
    expect(screen.getByText(/New patient medication/)).toBeInTheDocument()
    expect(medicationList.mock.calls[0][0].signal.aborted).toBe(true)
  })

  it("preserves manual addition to the patient list", async () => {
    stubList([CONFIRMED])
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    await screen.findByTestId("confirmed-medication")
    fireEvent.change(
      screen.getByRole("textbox", { name: /Add a medication/ }),
      { target: { value: "Synthetic medication" } },
    )
    fireEvent.click(screen.getByRole("button", { name: "Add" }))
    await waitFor(() =>
      expect(medicationCreate).toHaveBeenCalledWith({
        path: { patient_id: "patient_1" },
        body: { name: "Synthetic medication" },
      }),
    )
  })

  it("ignores a late mutation completion after switching away and back to the same patient", async () => {
    let resolveOld: (value: { error?: unknown }) => void = () => {}
    stubList([CONFIRMED])
    medicationCreate.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve
        }),
    )
    const view = render(
      <MedicationPanel patientId="patient_old" appointmentId="visit_current" />,
    )
    await screen.findByTestId("confirmed-medication")
    fireEvent.change(
      screen.getByRole("textbox", { name: /Add a medication/ }),
      {
        target: { value: "Original draft" },
      },
    )
    fireEvent.click(screen.getByRole("button", { name: "Add" }))
    await waitFor(() => expect(medicationCreate).toHaveBeenCalledTimes(1))

    view.rerender(
      <MedicationPanel patientId="patient_new" appointmentId="visit_current" />,
    )
    await screen.findByTestId("confirmed-medication")
    view.rerender(
      <MedicationPanel patientId="patient_old" appointmentId="visit_current" />,
    )
    await screen.findByTestId("confirmed-medication")
    const input = screen.getByRole("textbox", { name: /Add a medication/ })
    fireEvent.change(input, { target: { value: "New draft" } })
    await act(async () => resolveOld({}))

    expect(input).toHaveValue("New draft")
    expect(screen.getByRole("button", { name: "Add" })).toBeEnabled()
    expect(medicationList).toHaveBeenCalledTimes(3)
    expect(medicationCreate).toHaveBeenCalledWith({
      path: { patient_id: "patient_old" },
      body: { name: "Original draft" },
    })
  })

  it("prompts to connect NatMed when unsubscribed", async () => {
    stubList([CONFIRMED], false)

    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )

    await waitFor(() =>
      expect(
        screen.getByText(/Connect your NatMed Pro subscription/),
      ).toBeInTheDocument(),
    )
  })
})
