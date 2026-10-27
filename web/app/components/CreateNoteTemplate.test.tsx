import { beforeEach, expect, it, vi } from "vitest"

import type { TemplateField } from "~/configuration/client"

import { CreateNoteTemplate } from "./CreateNoteTemplate"
import { fireEvent, render, screen } from "@testing-library/react"

const { create } = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock("~/configuration/client", () => ({ createNoteTemplate: create }))
beforeEach(() => vi.resetAllMocks())

it("starts from four general SOAP sections and sends only the current owner's named preset data", async () => {
  const onCreated = vi.fn()
  render(<CreateNoteTemplate onCreated={onCreated} />)
  fireEvent.click(screen.getByRole("button", { name: "Create a template" }))
  expect(screen.getByLabelText("Heading 1")).toHaveValue("Subjective")
  expect(screen.getByLabelText("Heading 4")).toHaveValue("Plan")
  expect(screen.queryByLabelText("Starting point")).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText("Template name"), {
    target: { value: "My SOAP" },
  })
  create.mockResolvedValue({
    data: { schema_id: "synthetic", note_type: "My SOAP" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save new template" }))
  await screen.findByText(
    "Template “My SOAP” created. It is available for future notes.",
  )
  expect(create.mock.calls[0][0].body.note_type).toBe("My SOAP")
  expect(
    create.mock.calls[0][0].body.fields.map(
      (field: TemplateField) => field.heading,
    ),
  ).toEqual(["Subjective", "Objective", "Assessment", "Plan"])
  expect(Object.keys(create.mock.calls[0][0].body)).toEqual([
    "note_type",
    "fields",
  ])
  expect(onCreated).toHaveBeenCalledOnce()
})

it("keeps an uncertain create frozen and retries the identical request without generating another name", async () => {
  render(<CreateNoteTemplate onCreated={vi.fn()} />)
  fireEvent.change(screen.getByLabelText("Starting point"), {
    target: { value: "custom" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Create a template" }))
  expect(
    screen.getByRole("button", { name: "Remove section 1" }),
  ).toBeDisabled()
  fireEvent.change(screen.getByLabelText("Template name"), {
    target: { value: "My custom preset" },
  })
  create.mockRejectedValueOnce(new Error("Synthetic lost response"))
  fireEvent.click(screen.getByRole("button", { name: "Save new template" }))
  await screen.findByRole("alert")
  expect(screen.getByLabelText("Template name")).toHaveValue("My custom preset")
  expect(screen.getByLabelText("Template name")).toBeDisabled()
  expect(
    screen.queryByRole("button", { name: "Discard draft and close" }),
  ).not.toBeInTheDocument()
  create.mockResolvedValue({
    data: { note_type: "My custom preset", schema_id: "same" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Check template creation" }),
  )
  await screen.findByText(
    "Template “My custom preset” created. It is available for future notes.",
  )
  expect(create.mock.calls[1]).toEqual(create.mock.calls[0])
})

it("retains the draft after an explicit name conflict and permits a different name", async () => {
  render(<CreateNoteTemplate onCreated={vi.fn()} />)
  fireEvent.click(screen.getByRole("button", { name: "Create a template" }))
  fireEvent.change(screen.getByLabelText("Template name"), {
    target: { value: "Existing name" },
  })
  create.mockResolvedValue({ response: { status: 409 } })
  fireEvent.click(screen.getByRole("button", { name: "Save new template" }))
  await screen.findByRole("alert")
  expect(screen.getByLabelText("Template name")).toHaveValue("Existing name")
  expect(screen.getByLabelText("Template name")).not.toBeDisabled()
  expect(screen.getByLabelText("Heading 4")).toHaveValue("Plan")
})
