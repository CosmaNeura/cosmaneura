// Single-key shortcuts must never fire while the user is typing, holding a
// modifier, or working inside a dialog.
export function shouldIgnoreShortcut(event: KeyboardEvent) {
  const target = event.target
  const typing =
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  return (
    typing ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey ||
    event.defaultPrevented ||
    document.querySelector('[role="dialog"], [role="alertdialog"]') !== null
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
