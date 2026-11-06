import { beforeEach, expect, it, vi } from "vitest"

import { DeleteNoteTemplate } from "./DeleteNoteTemplate"
import { fireEvent, render, screen } from "@testing-library/react"

const state = vi.hoisted(() => ({ remove: vi.fn(), viewAs: false }))
vi.mock("~/configuration/client", () => ({ deleteNoteTemplate: state.remove }))
vi.mock("~/components/ViewAsRouteGuard", () => ({
  useViewAsActive: () => state.viewAs,
}))
beforeEach(() => {
  vi.resetAllMocks()
  state.viewAs = false
})

it("requires confirmation, offers keeping the template, and explains saved-note retention", () => {
  render(
    <DeleteNoteTemplate
      schemaId="owned-synthetic"
      name="SOAP"
      onDeleted={vi.fn()}
    />,
  )
  fireEvent.click(screen.getByRole("button", { name: "Delete template" }))
  expect(screen.getByText(/Saved notes remain available/)).toBeInTheDocument()
  expect(state.remove).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Keep template" }))
  expect(
    screen.getByRole("button", { name: "Delete template" }),
  ).toBeInTheDocument()
  expect(state.remove).not.toHaveBeenCalled()
})

it("retries only the same deletion after an unconfirmed response", async () => {
  const onDeleted = vi.fn()
  render(
    <DeleteNoteTemplate
      schemaId="owned-synthetic"
      name="SOAP"
      onDeleted={onDeleted}
    />,
  )
  state.remove.mockRejectedValueOnce(new Error("Synthetic lost response"))
  fireEvent.click(screen.getByRole("button", { name: "Delete template" }))
  fireEvent.click(screen.getByRole("button", { name: "Confirm deletion" }))
  await screen.findByRole("alert")
  expect(onDeleted).not.toHaveBeenCalled()
  state.remove.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  })
  fireEvent.click(screen.getByRole("button", { name: "Check deletion" }))
  await screen.findByText("Template deleted.")
  expect(state.remove).toHaveBeenNthCalledWith(1, {
    path: { schema_id: "owned-synthetic" },
  })
  expect(state.remove.mock.calls[1]).toEqual(state.remove.mock.calls[0])
  expect(onDeleted).toHaveBeenCalledOnce()
})

it("suppresses deletion controls during read-only view-as", () => {
  state.viewAs = true
  render(
    <DeleteNoteTemplate
      schemaId="owned-synthetic"
      name="SOAP"
      onDeleted={vi.fn()}
    />,
  )
  expect(screen.queryByRole("button")).not.toBeInTheDocument()
  expect(state.remove).not.toHaveBeenCalled()
})
