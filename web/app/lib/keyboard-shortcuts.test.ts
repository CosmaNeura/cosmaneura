import { afterEach, describe, expect, it } from "vitest"

import { adjacentUrl, shouldIgnoreShortcut } from "./keyboard-shortcuts"

afterEach(() => {
  document.body.innerHTML = ""
})

function keyOn(target: EventTarget, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent("keydown", { key: "j", ...init })
  Object.defineProperty(event, "target", { value: target })
  return event
}

describe("shortcut guard", () => {
  it("ignores keys while typing, with modifiers, or inside a dialog", () => {
    const input = document.createElement("input")
    expect(shouldIgnoreShortcut(keyOn(input))).toBe(true)
    expect(shouldIgnoreShortcut(keyOn(document.body, { metaKey: true }))).toBe(
      true,
    )

    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    document.body.append(dialog)
    expect(shouldIgnoreShortcut(keyOn(document.body))).toBe(true)
  })

  it("allows a plain key on the page", () => {
    expect(shouldIgnoreShortcut(keyOn(document.body))).toBe(false)
  })
})

describe("adjacent list entry", () => {
  const urls = ["/notes/a", "/notes/b", "/notes/c"]

  it.each([
    ["/notes/b", 1, "/notes/c"],
    ["/notes/b", -1, "/notes/a"],
    ["/notes/c", 1, null],
    ["/notes/a", -1, null],
    ["/notes", 1, "/notes/a"],
    ["/notes", -1, "/notes/c"],
  ] as const)("from %s moving %s goes to %s", (current, direction, next) => {
    expect(adjacentUrl(urls, current, direction)).toBe(next)
  })

  it("does nothing with an empty list", () => {
    expect(adjacentUrl([], "/notes", 1)).toBeNull()
  })
})
