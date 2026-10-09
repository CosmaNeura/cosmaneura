import { afterEach, describe, expect, it, vi } from "vitest"

import { NotesKeyboardShortcuts } from "./KeyboardShortcuts"
import { fireEvent, render, screen } from "@testing-library/react"

const toast = vi.hoisted(() => vi.fn())
vi.mock("~/hooks/use-toast", () => ({ toast }))

afterEach(() => toast.mockReset())

describe("Notes keyboard shortcuts", () => {
  it("opens the shortcut list with ? and from its button", () => {
    render(<NotesKeyboardShortcuts />)

    fireEvent.keyDown(document.body, { key: "?" })
    expect(
      screen.getByRole("dialog", { name: "Keyboard shortcuts" }),
    ).toBeVisible()
    expect(screen.getByText("Copy the open note")).toBeVisible()
  })

  it("copies the open note with c", () => {
    const copy = vi.fn()
    render(
      <>
        <NotesKeyboardShortcuts />
        <button type="button" data-shortcut="copy-note" onClick={copy}>
          Copy to Clipboard
        </button>
      </>,
    )

    fireEvent.keyDown(document.body, { key: "c" })

    expect(copy).toHaveBeenCalledOnce()
  })

  it("explains when no note is open to copy", () => {
    render(<NotesKeyboardShortcuts />)

    fireEvent.keyDown(document.body, { key: "c" })

    expect(toast).toHaveBeenCalledWith({ title: "Open a note to copy it" })
  })

  it("ignores c while typing", () => {
    const copy = vi.fn()
    render(
      <>
        <NotesKeyboardShortcuts />
        <input aria-label="Field" />
        <button type="button" data-shortcut="copy-note" onClick={copy}>
          Copy
        </button>
      </>,
    )

    fireEvent.keyDown(screen.getByLabelText("Field"), { key: "c" })

    expect(copy).not.toHaveBeenCalled()
  })
})
