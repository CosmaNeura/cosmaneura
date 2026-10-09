import { useState } from "react"
import { describe, expect, it, vi } from "vitest"

import { ConfirmDialog } from "./ConfirmDialog"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

function openDialog() {
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  return screen.getByRole("alertdialog")
}

function Harness({
  pending = false,
  onConfirm = vi.fn(),
}: {
  pending?: boolean
  onConfirm?: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <output aria-label="open state">{String(open)}</output>
      <ConfirmDialog
        trigger={<button type="button">Cancel</button>}
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
  it("focuses the safe choice, closes on Escape, and returns focus", async () => {
    render(<Harness />)

    const dialog = openDialog()
    expect(dialog).toHaveAccessibleName("Cancel your meeting?")
    expect(dialog).toHaveAccessibleDescription("The link will stop working.")
    expect(screen.getByRole("button", { name: "Keep meeting" })).toHaveFocus()

    fireEvent.keyDown(dialog, { key: "Escape" })

    expect(screen.getByLabelText("open state")).toHaveTextContent("false")
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus(),
    )
  })

  it("runs the confirmed action only from the destructive button", () => {
    const onConfirm = vi.fn()
    render(<Harness onConfirm={onConfirm} />)
    openDialog()

    fireEvent.click(screen.getByRole("button", { name: "Cancel meeting" }))
    expect(onConfirm).toHaveBeenCalledOnce()

    fireEvent.click(screen.getByRole("button", { name: "Keep meeting" }))
    expect(onConfirm).toHaveBeenCalledOnce()
    expect(screen.getByLabelText("open state")).toHaveTextContent("false")
  })

  it("stays open and locked while the action is running", () => {
    const { rerender } = render(<PendingHarness pending={false} />)
    const dialog = openDialog()
    rerender(<PendingHarness pending />)
    expect(screen.getByRole("button", { name: /Cancelling/ })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Keep meeting" })).toBeDisabled()

    fireEvent.keyDown(dialog, { key: "Escape" })
    expect(screen.getByLabelText("open state")).toHaveTextContent("true")

    rerender(<PendingHarness pending={false} />)
    expect(screen.getByRole("button", { name: "Keep meeting" })).toHaveFocus()
  })
})

function PendingHarness({ pending }: { pending: boolean }) {
  return <Harness pending={pending} />
}
