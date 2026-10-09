import { useSyncExternalStore } from "react"

// WCAG 2.1.4: single-character shortcuts must be possible to turn off. The
// choice is a per-browser convenience, so it lives in localStorage, and the
// shortcuts stay on if storage is unavailable.
const STORAGE_KEY = "cosmas.single-key-shortcuts"
const listeners = new Set<() => void>()

function readEnabled() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "off"
  } catch {
    return true
  }
}

let enabled = typeof window === "undefined" ? true : readEnabled()

export function setSingleKeyShortcutsEnabled(next: boolean) {
  enabled = next
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? "on" : "off")
  } catch {
    // Keep the in-memory choice for this page even if it cannot persist.
  }
  listeners.forEach((listener) => listener())
}

export function useSingleKeyShortcutsEnabled() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => enabled,
    () => true,
  )
}

// Widgets that consume typed characters themselves (typeahead, editing).
const TYPING_ROLES =
  '[role="listbox"],[role="menu"],[role="menubar"],[role="combobox"],[role="textbox"],[role="slider"],[role="spinbutton"]'

// Single-key shortcuts must never fire when turned off, while the user is
// typing or composing, with a modifier held, or inside a dialog or a widget
// that handles its own keys.
export function shouldIgnoreShortcut(event: KeyboardEvent) {
  const target = event.target
  const typing =
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
      target.closest(TYPING_ROLES) !== null)
  return (
    !enabled ||
    typing ||
    event.isComposing ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey ||
    event.defaultPrevented ||
    document.querySelector(
      '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]',
    ) !== null
  )
}

/** The entry to open next with j (down) or k (up), wrapping at neither end. */
export function adjacentUrl(
  urls: string[],
  currentUrl: string,
  direction: 1 | -1,
): string | null {
  if (urls.length === 0) return null
  const index = urls.indexOf(currentUrl)
  if (index === -1) return direction === 1 ? urls[0] : urls[urls.length - 1]
  return urls[index + direction] ?? null
}

/**
 * The note copy control "c" should use: the one in the note holding focus,
 * else the only open note's. Null when that would be a guess.
 */
export function noteCopyButtonFor(active: Element | null) {
  const buttons = Array.from(
    document.querySelectorAll<HTMLButtonElement>('[data-shortcut="copy-note"]'),
  )
  const focusedNote = active?.closest("[data-note-viewer]")
  if (focusedNote) {
    const inFocused = buttons.find((button) => focusedNote.contains(button))
    if (inFocused) return inFocused
  }
  return buttons.length === 1 ? buttons[0] : null
}
