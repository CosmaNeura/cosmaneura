import { beforeEach, describe, expect, it, vi } from "vitest"

import { NoteTemplateFieldsEditor } from "./NoteTemplateFieldsEditor"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const { getFields, updateFields } = vi.hoisted(() => ({
  getFields: vi.fn(),
  updateFields: vi.fn(),
}))
vi.mock("~/configuration/client", () => ({
  getNoteTemplateFields: getFields,
  updateNoteTemplateFields: updateFields,
}))
const baseline = {
  revision: "a".repeat(64),
  fields: [
    { heading: "Labs - 02/12/2026", guidance: "Discussed tests" },
    { heading: "Plan", guidance: "Discussed plan" },
  ],
}

async function open() {
  const onSaved = vi.fn()
  render(<NoteTemplateFieldsEditor schemaId="schema-1" onSaved={onSaved} />)
  expect(getFields).not.toHaveBeenCalled()
  fireEvent.click(
    screen.getByRole("button", { name: "Edit headings and guidance" }),
  )
  await screen.findByLabelText("Heading 1")
  return onSaved
}

describe("NoteTemplateFieldsEditor", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    getFields.mockResolvedValue({ data: baseline })
  })

  it("loads on demand and saves the exact revision with ordered fields", async () => {
    const onSaved = await open()
    expect(screen.getByRole("button", { name: "Save template" })).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Heading 1"), {
      target: { value: "Labs" },
    })
    const fields = [
      { ...baseline.fields[0], heading: "Labs" },
      baseline.fields[1],
    ]
    updateFields.mockResolvedValue({
      data: { revision: "b".repeat(64), fields },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save template" }))
    await screen.findByText("Template saved.")
    expect(updateFields).toHaveBeenCalledWith({
      path: { schema_id: "schema-1" },
      body: { expected_revision: baseline.revision, fields },
    })
    expect(onSaved).toHaveBeenCalledOnce()
  })

  it("keeps edits after a conflict until explicit discard and reload", async () => {
    const onSaved = await open()
    fireEvent.change(screen.getByLabelText("Heading 1"), {
      target: { value: "Labs" },
    })
    updateFields.mockResolvedValue({ response: { status: 409 } })
    fireEvent.click(screen.getByRole("button", { name: "Save template" }))
    await screen.findByRole("alert")
    expect(screen.getByLabelText("Heading 1")).toHaveValue("Labs")
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Save template" })).toBeDisabled()
    getFields.mockResolvedValue({
      data: { ...baseline, fields: [{ heading: "Tests", guidance: "" }] },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Discard edits and reload" }),
    )
    await waitFor(() =>
      expect(screen.getByLabelText("Heading 1")).toHaveValue("Tests"),
    )
  })

  it("can retry the same revision after a lost response", async () => {
    await open()
    fireEvent.change(screen.getByLabelText("Guidance 2"), {
      target: { value: "Explicit plan only" },
    })
    updateFields.mockRejectedValueOnce(new Error("lost response"))
    fireEvent.click(screen.getByRole("button", { name: "Save template" }))
    await screen.findByRole("alert")
    expect(screen.getByLabelText("Guidance 2")).toHaveValue(
      "Explicit plan only",
    )
    updateFields.mockResolvedValue({
      data: { ...baseline, revision: "b".repeat(64) },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save template" }))
    await screen.findByText("Template saved.")
    expect(updateFields.mock.calls[0]).toEqual(updateFields.mock.calls[1])
  })

  it("does not report failed loading or saving as success", async () => {
    getFields.mockResolvedValueOnce({ response: { status: 503 } })
    render(<NoteTemplateFieldsEditor schemaId="schema-1" onSaved={vi.fn()} />)
    fireEvent.click(
      screen.getByRole("button", { name: "Edit headings and guidance" }),
    )
    await screen.findByRole("alert")
    expect(updateFields).not.toHaveBeenCalled()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Edit headings and guidance" }),
    )
    await screen.findByLabelText("Heading 1")
    fireEvent.change(screen.getByLabelText("Heading 1"), {
      target: { value: "Plan" },
    })
    updateFields.mockResolvedValue({ response: { status: 422 } })
    fireEvent.click(screen.getByRole("button", { name: "Save template" }))
    await screen.findByRole("alert")
    expect(screen.getByLabelText("Heading 1")).toHaveValue("Plan")
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })
})
