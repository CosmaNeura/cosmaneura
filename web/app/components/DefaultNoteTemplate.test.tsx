import { beforeEach, expect, it, vi } from "vitest"

import { DefaultNoteTemplate } from "./DefaultNoteTemplate"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const { get, save } = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn() }))
vi.mock("~/configuration/client", () => ({
  getNoteTemplatePreference: get,
  updateNoteTemplatePreference: save,
}))
const schemas = [
  { id: "schema_a", note_type: "My SOAP" },
  { id: "schema_b", note_type: "My focused note" },
]
beforeEach(() => {
  vi.resetAllMocks()
  get.mockResolvedValue({
    data: { default_schema_ref: "schema_a", revision: 3 },
  })
})

it("reads the saved account preference and only acknowledges the server-confirmed new default", async () => {
  render(<DefaultNoteTemplate schemas={schemas} />)
  await waitFor(() =>
    expect(screen.getByLabelText("Default Scribe template")).toHaveValue(
      "schema_a",
    ),
  )
  fireEvent.change(screen.getByLabelText("Default Scribe template"), {
    target: { value: "schema_b" },
  })
  expect(screen.queryByRole("status")).not.toBeInTheDocument()
  save.mockResolvedValue({
    data: { default_schema_ref: "schema_b", revision: 4 },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save default" }))
  await screen.findByText("Default template saved.")
  expect(save).toHaveBeenCalledWith({
    body: { expected_revision: 3, default_schema_ref: "schema_b" },
  })
})

it("retains an uncertain choice and checks the exact request before edits", async () => {
  render(<DefaultNoteTemplate schemas={schemas} />)
  await waitFor(() =>
    expect(screen.getByLabelText("Default Scribe template")).toHaveValue(
      "schema_a",
    ),
  )
  fireEvent.change(screen.getByLabelText("Default Scribe template"), {
    target: { value: "" },
  })
  save.mockRejectedValueOnce(new Error("Synthetic lost response"))
  fireEvent.click(screen.getByRole("button", { name: "Save default" }))
  await screen.findByRole("alert")
  expect(screen.getByLabelText("Default Scribe template")).toBeDisabled()
  save.mockResolvedValue({ data: { default_schema_ref: null, revision: 4 } })
  fireEvent.click(screen.getByRole("button", { name: "Check default save" }))
  await screen.findByText(
    "Default cleared. Choose a template for each new note.",
  )
  expect(save.mock.calls[1]).toEqual(save.mock.calls[0])
})

it("retains a conflicting choice and reloads only on the explicit discard action", async () => {
  render(<DefaultNoteTemplate schemas={schemas} />)
  await waitFor(() =>
    expect(screen.getByLabelText("Default Scribe template")).toHaveValue(
      "schema_a",
    ),
  )
  fireEvent.change(screen.getByLabelText("Default Scribe template"), {
    target: { value: "schema_b" },
  })
  save.mockResolvedValue({ response: { status: 409 } })
  fireEvent.click(screen.getByRole("button", { name: "Save default" }))
  await screen.findByRole("alert")
  expect(screen.getByLabelText("Default Scribe template")).toHaveValue(
    "schema_b",
  )
  expect(get).toHaveBeenCalledOnce()
  fireEvent.click(
    screen.getByRole("button", { name: "Discard choice and reload" }),
  )
  await waitFor(() =>
    expect(screen.getByLabelText("Default Scribe template")).toHaveValue(
      "schema_a",
    ),
  )
})
