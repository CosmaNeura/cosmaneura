import { useEffect, useState } from "react"

import { Keyboard } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "~/hooks/use-toast"
import { shouldIgnoreShortcut } from "~/lib/keyboard-shortcuts"

const SHORTCUTS = [
  ["j", "Next visit"],
  ["k", "Previous visit"],
  ["/", "Search visits"],
  ["c", "Copy the open note"],
  ["?", "Show these shortcuts"],
] as const

// Owns the Notes-wide "c" and "?" keys and the help dialog; j, k and / live
// with the list they move through.
export function NotesKeyboardShortcuts() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (shouldIgnoreShortcut(event)) return
      if (event.key === "?") {
        event.preventDefault()
        setOpen(true)
      } else if (event.key === "c") {
        const copy = document.querySelector<HTMLButtonElement>(
          '[data-shortcut="copy-note"]',
        )
        event.preventDefault()
        if (copy) copy.click()
        else toast({ title: "Open a note to copy it" })
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="text-on-navy-muted hover:text-sidebar-foreground size-7"
        aria-label="Keyboard shortcuts"
        aria-keyshortcuts="?"
        onClick={() => setOpen(true)}
      >
        <Keyboard className="size-4" aria-hidden="true" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="w-[calc(100%-2rem)] rounded-lg sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Keyboard shortcuts</DialogTitle>
            <DialogDescription>
              Available in Notes while you are not typing.
            </DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
            {SHORTCUTS.map(([key, action]) => (
              <div key={key} className="contents">
                <dt>
                  <kbd className="bg-muted rounded border px-2 py-0.5 font-mono text-xs">
                    {key}
                  </kbd>
                </dt>
                <dd>{action}</dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
    </>
  )
}
