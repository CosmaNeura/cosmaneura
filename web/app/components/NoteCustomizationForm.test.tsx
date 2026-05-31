import { beforeEach, describe, expect, it, vi } from "vitest"

import { NoteCustomizationForm } from "./NoteCustomizationForm"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

// Mock the client module the form calls
const updateNoteCustomization = vi.fn().mockResolvedValue({ data: {} })
vi.mock("~/configuration/client", () => ({
  updateNoteCustomization: (args: unknown) => updateNoteCustomization(args),
}))
// Toast is a side effect; stub it
vi.mock("~/hooks/use-toast", () => ({ toast: vi.fn() }))

const SCHEMA = { id: "doctor_note_schema_abc", note_type: "SOAP" }
const CUSTOMIZATION = {
  doctor_note_schema_id: "doctor_note_schema_abc",
  subjective_source: "patient_only" as const,
  pronoun_style: "infer_from_transcript" as const,
  plan_ordering: "patient_then_doctor" as const,
  flag_new_medications: true,
  require_plan_reason: true,
  ros_distinct_from_subjective: false,
  custom_instructions: "Use metric units.",
}

describe("NoteCustomizationForm", () => {
  beforeEach(() => updateNoteCustomization.mockClear())

  it("renders the note type and the current custom instructions", () => {
    render(
      <NoteCustomizationForm schema={SCHEMA} customization={CUSTOMIZATION} />,
    )
    expect(screen.getByText("SOAP")).toBeInTheDocument()
    expect(screen.getByDisplayValue("Use metric units.")).toBeInTheDocument()
  })

  it("submits the schema id in the path and the form values in the body", async () => {
    render(
      <NoteCustomizationForm schema={SCHEMA} customization={CUSTOMIZATION} />,
    )
    fireEvent.click(screen.getByRole("button", { name: /save/i }))

    await waitFor(() =>
      expect(updateNoteCustomization).toHaveBeenCalledTimes(1),
    )
    const arg = updateNoteCustomization.mock.calls[0][0]
    expect(arg.path).toEqual({ schema_id: "doctor_note_schema_abc" })
    expect(arg.body.require_plan_reason).toBe(true)
    expect(arg.body.ros_distinct_from_subjective).toBe(false)
    expect(arg.body.custom_instructions).toBe("Use metric units.")
  })
})
