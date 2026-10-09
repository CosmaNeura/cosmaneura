import { useState } from "react"
import { describe, expect, it, vi } from "vitest"

import { ConfirmDialog } from "./ConfirmDialog"
import { fireEvent, render, screen } from "@testing-library/react"

function Harness({
  pending = false,
  onConfirm = vi.fn(),
}: {
  pending?: boolean
  onConfirm?: () => void
}) {
  const [open, setOpen] = useState(true)
  return (
    <>
      <output aria-label="open state">{String(open)}</output>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Cancel your meeting?"
        description="The link will stop working."
        cancelLabel="Keep meeting"
        confirmLabel="Cancel meeting"
        pendingLabel="Cancelling..."
        pending={pending}
        onConfirm={onConfirm}
      />
    </>
  )
}

describe("ConfirmDialog", () => {
  it("focuses the safe choice and closes on Escape", () => {
    render(<Harness />)

    const dialog = screen.getByRole("alertdialog", {
      name: "Cancel your meeting?",
    })
    expect(dialog).toHaveAccessibleDescription("The link will stop working.")
    expect(screen.getByRole("button", { name: "Keep meeting" })).toHaveFocus()

    fireEvent.keyDown(dialog, { key: "Escape" })

    expect(screen.getByLabelText("open state")).toHaveTextContent("false")
  })

  it("runs the confirmed action only from the destructive button", () => {
    const onConfirm = vi.fn()
    render(<Harness onConfirm={onConfirm} />)

    fireEvent.click(screen.getByRole("button", { name: "Cancel meeting" }))
    expect(onConfirm).toHaveBeenCalledOnce()

    fireEvent.click(screen.getByRole("button", { name: "Keep meeting" }))
    expect(onConfirm).toHaveBeenCalledOnce()
    expect(screen.getByLabelText("open state")).toHaveTextContent("false")
  })

  it("stays open and locked while the action is running", () => {
    render(<Harness pending />)

    const dialog = screen.getByRole("alertdialog")
    expect(screen.getByRole("button", { name: /Cancelling/ })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Keep meeting" })).toBeDisabled()

    fireEvent.keyDown(dialog, { key: "Escape" })

    expect(screen.getByLabelText("open state")).toHaveTextContent("true")
  })
})
