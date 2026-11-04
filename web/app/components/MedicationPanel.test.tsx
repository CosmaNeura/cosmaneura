import { beforeEach, describe, expect, it, vi } from "vitest"

import { toast } from "~/hooks/use-toast"

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
  heldWritesEnabled = true,
) {
  medicationList.mockResolvedValue({ data: meds })
  patientReferenceResources.mockResolvedValue({
    data: {
      has_natmed_subscription: hasNatmed,
      held_medication_writes_enabled: heldWritesEnabled,
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
  it("hides new holds when disabled while retaining held labels, correction and confirmation", async () => {
    stubList(
      [
        CONFIRMED,
        { ...UNCONFIRMED, status: "active" },
        { ...UNCONFIRMED, id: "existing_hold", status: "held" },
      ],
      false,
      [],
      [],
      false,
    )
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    await screen.findByTestId("confirmed-medication")
    expect(
      screen.queryByRole("button", { name: "Hold" }),
    ).not.toBeInTheDocument()
    const active = screen.getAllByRole("combobox")[0]
    expect(
      within(active).queryByRole("option", { name: "Held temporarily" }),
    ).not.toBeInTheDocument()
    const held = screen.getAllByRole("combobox")[1]
    expect(held).toHaveValue("held")
    expect(
      within(held).getByRole("option", { name: "Held temporarily" }),
    ).toBeInTheDocument()
    fireEvent.change(held, { target: { value: "active" } })
    await waitFor(() =>
      expect(medicationUpdate).toHaveBeenCalledWith({
        path: { medication_id: "existing_hold" },
        headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
        body: { status: "active" },
      }),
    )
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Confirm for patient list" })[1],
      ).toBeEnabled(),
    )
    fireEvent.click(
      screen.getAllByRole("button", { name: "Confirm for patient list" })[1],
    )
    await waitFor(() =>
      expect(medicationUpdate).toHaveBeenLastCalledWith({
        path: { medication_id: "existing_hold" },
        headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
        body: { confirmed: true },
      }),
    )
  })

  it("keeps Resume and a manual draft without enabling a missing release control", async () => {
    let row = { ...CONFIRMED, status: "held" }
    stubList([row])
    medicationList.mockImplementation(() => Promise.resolve({ data: [row] }))
    patientReferenceResources.mockImplementation(() =>
      Promise.resolve({
        data: {
          has_natmed_subscription: false,
          directory: [],
          medications: [],
        },
      }),
    )
    medicationUpdate.mockImplementation(({ body }) => {
      row = { ...row, status: body.status }
      return Promise.resolve({ data: row })
    })
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    await screen.findByRole("button", { name: "Resume" })
    expect(screen.getByTestId("confirmed-medication")).toHaveTextContent(
      "held temporarily",
    )
    const input = screen.getByRole("textbox", { name: /Add a medication/ })
    fireEvent.change(input, { target: { value: "Unsaved synthetic entry" } })
    fireEvent.click(screen.getByRole("button", { name: "Resume" }))
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Resume" }),
      ).not.toBeInTheDocument(),
    )
    expect(
      screen.queryByRole("button", { name: "Hold" }),
    ).not.toBeInTheDocument()
    expect(input).toHaveValue("Unsaved synthetic entry")
    expect(medicationUpdate).toHaveBeenCalledWith({
      path: { medication_id: CONFIRMED.id },
      headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
      body: { status: "active" },
    })
  })

  it("declares held-reader support on all four reads while preserving patient and abort options", async () => {
    stubList([CONFIRMED])
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    await screen.findByTestId("confirmed-medication")

    const signal = medicationList.mock.calls[0][0].signal
    expect(signal).toBeInstanceOf(AbortSignal)
    expect(signal.aborted).toBe(false)
    for (const read of [
      medicationList,
      patientReferenceResources,
      medicationDrugReference,
      medicationNutrientDepletions,
    ]) {
      expect(read).toHaveBeenCalledExactlyOnceWith({
        path: { patient_id: "patient_1" },
        headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
        signal,
      })
    }
  })

  it("shows an unavailable list after a 409 reader refusal and retries without reloading", async () => {
    stubList([{ ...CONFIRMED, status: "held" }])
    medicationList.mockResolvedValueOnce({
      error: { detail: "Medication reader upgrade required" },
      response: { status: 409 },
    })
    const reload = vi.spyOn(window.location, "reload")
    const href = window.location.href
    try {
      render(
        <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
      )
      await screen.findByRole("status")
      expect(screen.getByRole("status")).toHaveTextContent(
        "Could not load the medication list for this patient.",
      )
      expect(
        screen.queryByText("No medications recorded yet."),
      ).not.toBeInTheDocument()
      expect(
        screen.queryByTestId("confirmed-medication"),
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("textbox", { name: /Add a medication/ }),
      ).not.toBeInTheDocument()
      expect(medicationCreate).not.toHaveBeenCalled()
      expect(medicationUpdate).not.toHaveBeenCalled()
      expect(medicationDelete).not.toHaveBeenCalled()

      fireEvent.click(
        screen.getByRole("button", { name: "Retry medication list" }),
      )
      await screen.findByText(/held temporarily/)
      expect(screen.queryByRole("status")).not.toBeInTheDocument()
      expect(medicationList).toHaveBeenCalledTimes(2)
      for (const [options] of medicationList.mock.calls) {
        expect(options.path).toEqual({ patient_id: "patient_1" })
        expect(options.headers).toEqual({
          "X-Cosmaneura-Medication-Reader": "held-v1",
        })
      }
      expect(medicationList.mock.calls[0][0].signal.aborted).toBe(true)
      expect(reload).not.toHaveBeenCalled()
      expect(window.location.href).toBe(href)
    } finally {
      reload.mockRestore()
    }
  })

  it("distinguishes held status without inferring confirmation or changing prior visits", async () => {
    stubList([
      { ...CONFIRMED, status: "held" },
      { ...UNCONFIRMED, status: "held" },
      {
        ...UNCONFIRMED,
        id: "prior",
        status: "held",
        appointment_id: "prior_visit",
      },
    ])
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    await waitFor(() =>
      expect(screen.getAllByText(/held temporarily/)).toHaveLength(3),
    )
    expect(
      screen.queryByRole("button", { name: "Hold" }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument()
    expect(medicationUpdate).not.toHaveBeenCalled()
    expect(
      within(screen.getByTestId("historical-medication")).queryByRole("button"),
    ).not.toBeInTheDocument()
  })

  it("holds and resumes only the explicit confirmed patient row, preserving dated provenance", async () => {
    let row = { ...CONFIRMED, list_updated_at: "2026-10-31T12:00:00Z" }
    stubList([row])
    medicationList.mockImplementation(() => Promise.resolve({ data: [row] }))
    medicationUpdate.mockImplementation(({ body }) => {
      row = { ...row, status: body.status }
      return Promise.resolve({ data: row })
    })
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_other" />,
    )
    await screen.findByRole("button", { name: "Hold" })
    fireEvent.click(screen.getByRole("button", { name: "Hold" }))
    await screen.findByRole("button", { name: "Resume" })
    expect(medicationUpdate).toHaveBeenLastCalledWith({
      headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
      path: { medication_id: CONFIRMED.id },
      body: { status: "held" },
    })
    expect(screen.getByTestId("confirmed-medication")).toHaveTextContent(
      "Source visit · 10/20/26",
    )
    expect(screen.getByTestId("confirmed-medication")).toHaveTextContent(
      "List updated · 10/31/26",
    )
    fireEvent.click(screen.getByRole("button", { name: "Resume" }))
    await screen.findByRole("button", { name: "Hold" })
    expect(medicationUpdate).toHaveBeenLastCalledWith({
      headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
      path: { medication_id: CONFIRMED.id },
      body: { status: "active" },
    })
    expect(screen.queryByText(/held temporarily/)).not.toBeInTheDocument()
  })

  it("requires an explicit current-visit status correction before separate confirmation", async () => {
    let row = { ...UNCONFIRMED, status: "stopped" }
    stubList([row, { ...row, id: "history", appointment_id: "visit_older" }])
    medicationList.mockImplementation(() =>
      Promise.resolve({
        data: [
          row,
          {
            ...UNCONFIRMED,
            id: "history",
            status: "stopped",
            appointment_id: "visit_older",
          },
        ],
      }),
    )
    medicationUpdate.mockImplementation(({ body }) => {
      row = { ...row, ...body }
      return Promise.resolve({ data: row })
    })
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    const select = await screen.findByRole("combobox", {
      name: "Status for fish oil",
    })
    fireEvent.change(select, { target: { value: "held" } })
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Status for fish oil" }),
      ).toHaveValue("held"),
    )
    expect(medicationUpdate).toHaveBeenLastCalledWith({
      headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
      path: { medication_id: UNCONFIRMED.id },
      body: { status: "held" },
    })
    expect(screen.getByTestId("unconfirmed-medication")).toHaveTextContent(
      "held temporarily",
    )
    expect(
      within(screen.getByTestId("historical-medication")).queryByRole(
        "combobox",
      ),
    ).not.toBeInTheDocument()
    expect(screen.queryByTestId("confirmed-medication")).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm for patient list" }),
    )
    await screen.findByTestId("confirmed-medication")
    expect(medicationUpdate).toHaveBeenLastCalledWith({
      headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
      path: { medication_id: UNCONFIRMED.id },
      body: { confirmed: true },
    })
    expect(screen.getByTestId("confirmed-medication")).toHaveTextContent(
      "held temporarily",
    )
  })

  it("does not apply a late hold response to a different patient", async () => {
    let resolveOld: (value: unknown) => void = () => {}
    stubList([CONFIRMED])
    medicationList.mockImplementation(({ path }) =>
      Promise.resolve({ data: [{ ...CONFIRMED, name: path.patient_id }] }),
    )
    medicationUpdate.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve
        }),
    )
    const view = render(
      <MedicationPanel patientId="patient_old" appointmentId="visit_current" />,
    )
    fireEvent.click(await screen.findByRole("button", { name: "Hold" }))
    await waitFor(() => expect(medicationUpdate).toHaveBeenCalledTimes(1))
    view.rerender(
      <MedicationPanel patientId="patient_new" appointmentId="visit_current" />,
    )
    await screen.findByText("patient_new · 500mg")
    await act(async () =>
      resolveOld({ data: { ...CONFIRMED, status: "held" } }),
    )
    expect(screen.queryByText(/held temporarily/)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Hold" })).toBeEnabled()
    expect(medicationList).toHaveBeenCalledTimes(2)
  })

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
        headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
        body: { confirmed: true },
      }),
    )
  })

  it("declares held-reader support when dismissing an open-visit suggestion", async () => {
    stubList([UNCONFIRMED])
    render(
      <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
    )
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }))
    await waitFor(() =>
      expect(medicationDelete).toHaveBeenCalledExactlyOnceWith({
        path: { medication_id: "patient_medication_2" },
        headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
      }),
    )
    await waitFor(() => expect(medicationList).toHaveBeenCalledTimes(2))
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
        headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
        body: { name: "Synthetic medication" },
      }),
    )
  })

  it("retains a manual draft after a 409 mutation refusal and clears it only after success", async () => {
    stubList([CONFIRMED])
    medicationCreate.mockResolvedValueOnce({
      error: { detail: "Medication reader upgrade required" },
      response: { status: 409 },
    })
    vi.mocked(toast).mockClear()
    const reload = vi.spyOn(window.location, "reload")
    try {
      render(
        <MedicationPanel patientId="patient_1" appointmentId="visit_current" />,
      )
      await screen.findByTestId("confirmed-medication")
      const input = screen.getByRole("textbox", { name: /Add a medication/ })
      fireEvent.change(input, { target: { value: "Unsaved synthetic entry" } })
      fireEvent.click(screen.getByRole("button", { name: "Add" }))
      await waitFor(() =>
        expect(toast).toHaveBeenCalledWith({
          title: "Could not add",
          variant: "destructive",
        }),
      )
      expect(input).toHaveValue("Unsaved synthetic entry")
      expect(screen.getByRole("button", { name: "Add" })).toBeEnabled()
      expect(medicationList).toHaveBeenCalledTimes(1)
      expect(medicationCreate).toHaveBeenCalledExactlyOnceWith({
        path: { patient_id: "patient_1" },
        headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
        body: { name: "Unsaved synthetic entry" },
      })
      expect(reload).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole("button", { name: "Add" }))
      await waitFor(() => expect(medicationCreate).toHaveBeenCalledTimes(2))
      await waitFor(() => expect(input).toHaveValue(""))
      expect(medicationList).toHaveBeenCalledTimes(2)
    } finally {
      reload.mockRestore()
    }
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
      headers: { "X-Cosmaneura-Medication-Reader": "held-v1" },
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
