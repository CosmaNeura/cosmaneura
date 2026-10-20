import { afterEach, describe, expect, it, vi } from "vitest"

import { setSingleKeyShortcutsEnabled } from "~/lib/keyboard-shortcuts"

import { NotesKeyboardShortcuts } from "./KeyboardShortcuts"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const toast = vi.hoisted(() => vi.fn())
vi.mock("~/hooks/use-toast", () => ({ toast }))

afterEach(() => {
  toast.mockReset()
  setSingleKeyShortcutsEnabled(true)
})

describe("Notes keyboard shortcuts", () => {
  it("returns focus to the help button after Escape", async () => {
    render(<NotesKeyboardShortcuts />)
    const trigger = screen.getByRole("button", { name: "Keyboard shortcuts" })
    trigger.focus()
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it("returns focus to the shortcut's originating control", async () => {
    render(
      <>
        <NotesKeyboardShortcuts />
        <a href="#visit">Synthetic visit</a>
      </>,
    )
    const origin = screen.getByRole("link", { name: "Synthetic visit" })
    origin.focus()
    fireEvent.keyDown(origin, { key: "?" })
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    await waitFor(() => expect(origin).toHaveFocus())
  })

  it("opens the shortcut list with ?", () => {
    render(<NotesKeyboardShortcuts />)

    fireEvent.keyDown(document.body, { key: "?" })
    expect(
      screen.getByRole("dialog", { name: "Keyboard shortcuts" }),
    ).toBeVisible()
    expect(screen.getByText("Copy the open note")).toBeVisible()
  })

  it("opens the shortcut list from its button", () => {
    render(<NotesKeyboardShortcuts />)

    fireEvent.click(screen.getByRole("button", { name: "Keyboard shortcuts" }))

    expect(
      screen.getByRole("dialog", { name: "Keyboard shortcuts" }),
    ).toBeVisible()
  })

  it("lets the user turn single-key shortcuts off", () => {
    const copy = vi.fn()
    render(
      <>
        <NotesKeyboardShortcuts />
        <button type="button" data-shortcut="copy-note" onClick={copy}>
          Copy
        </button>
      </>,
    )
    fireEvent.click(screen.getByRole("button", { name: "Keyboard shortcuts" }))
    fireEvent.click(
      screen.getByRole("switch", { name: "Single-key shortcuts" }),
    )
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })

    fireEvent.keyDown(document.body, { key: "c" })
    fireEvent.keyDown(document.body, { key: "?" })

    expect(copy).not.toHaveBeenCalled()
    expect(screen.queryByRole("dialog")).toBeNull()
    expect(window.localStorage.getItem("cosmas.single-key-shortcuts")).toBe(
      "off",
    )
  })

  it("copies the note the user is working in when several are open", () => {
    const first = vi.fn()
    const second = vi.fn()
    render(
      <>
        <NotesKeyboardShortcuts />
        <section data-note-viewer="">
          <button type="button" data-shortcut="copy-note" onClick={first}>
            Copy
          </button>
        </section>
        <section data-note-viewer="">
          <a href="#plan">Plan</a>
          <button type="button" data-shortcut="copy-note" onClick={second}>
            Copy
          </button>
        </section>
      </>,
    )

    screen.getByRole("link", { name: "Plan" }).focus()
    fireEvent.keyDown(document.activeElement!, { key: "c" })

    expect(second).toHaveBeenCalledOnce()
    expect(first).not.toHaveBeenCalled()
  })

  it("copies the note the user last clicked in, even without focus", () => {
    const first = vi.fn()
    const second = vi.fn()
    render(
      <>
        <NotesKeyboardShortcuts />
        <section data-note-viewer="">
          <p>First note text</p>
          <button type="button" data-shortcut="copy-note" onClick={first}>
            Copy
          </button>
        </section>
        <section data-note-viewer="">
          <p>Second note text</p>
          <button type="button" data-shortcut="copy-note" onClick={second}>
            Copy
          </button>
        </section>
      </>,
    )

    fireEvent.pointerDown(screen.getByText("Second note text"))
    fireEvent.keyDown(document.body, { key: "c" })

    expect(second).toHaveBeenCalledOnce()
    expect(first).not.toHaveBeenCalled()
  })

  it("asks rather than guessing when several notes are open", () => {
    const copy = vi.fn()
    render(
      <>
        <NotesKeyboardShortcuts />
        <button type="button" data-shortcut="copy-note" onClick={copy}>
          Copy
        </button>
        <button type="button" data-shortcut="copy-note" onClick={copy}>
          Copy
        </button>
      </>,
    )

    fireEvent.keyDown(document.body, { key: "c" })

    expect(copy).not.toHaveBeenCalled()
    expect(toast).toHaveBeenCalledWith({
      title: "Click into the note you want to copy",
    })
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

  it("ignores c inside a widget that handles its own typing", () => {
    const copy = vi.fn()
    render(
      <>
        <NotesKeyboardShortcuts />
        <div role="listbox" tabIndex={0} aria-label="Accounts" />
        <button type="button" data-shortcut="copy-note" onClick={copy}>
          Copy
        </button>
      </>,
    )

    fireEvent.keyDown(screen.getByRole("listbox"), { key: "c" })

    expect(copy).not.toHaveBeenCalled()
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
