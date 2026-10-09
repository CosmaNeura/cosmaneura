import { useEffect, useRef, useState } from "react"

import { Keyboard } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { toast } from "~/hooks/use-toast"
import {
  noteCopyButtonFor,
  setSingleKeyShortcutsEnabled,
  shouldIgnoreShortcut,
  useSingleKeyShortcutsEnabled,
} from "~/lib/keyboard-shortcuts"

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
  const shortcutsEnabled = useSingleKeyShortcutsEnabled()
  // Clicking note text does not move focus (and Safari does not focus clicked
  // buttons), so remember the note the user last pointed at or focused.
  const lastNote = useRef<Element | null>(null)
  const shortcutOrigin = useRef<HTMLElement | null>(null)

  useEffect(() => {
    function remember(event: Event) {
      const note =
        event.target instanceof Element
          ? event.target.closest("[data-note-viewer]")
          : null
      if (note) lastNote.current = note
    }
    document.addEventListener("pointerdown", remember, true)
    document.addEventListener("focusin", remember, true)
    return () => {
      document.removeEventListener("pointerdown", remember, true)
      document.removeEventListener("focusin", remember, true)
    }
  }, [])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "?" && event.key !== "c") return
      if (shouldIgnoreShortcut(event)) return
      event.preventDefault()
      if (event.key === "?") {
        shortcutOrigin.current =
          document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null
        setOpen(true)
        return
      }
      const active = document.activeElement
      const copy = noteCopyButtonFor(
        active?.closest("[data-note-viewer]")
          ? active
          : lastNote.current?.isConnected
            ? lastNote.current
            : null,
      )
      if (copy) {
        copy.click()
      } else if (document.querySelector('[data-shortcut="copy-note"]')) {
        toast({ title: "Click into the note you want to copy" })
      } else {
        toast({ title: "Open a note to copy it" })
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-on-navy-muted hover:text-sidebar-foreground size-7"
          aria-label="Keyboard shortcuts"
          aria-keyshortcuts="?"
          onClick={() => {
            shortcutOrigin.current = null
          }}
        >
          <Keyboard className="size-4" aria-hidden="true" />
        </Button>
      </DialogTrigger>
      <DialogContent
        className="w-[calc(100%-2rem)] rounded-lg sm:max-w-sm"
        onCloseAutoFocus={(event) => {
          const origin = shortcutOrigin.current
          shortcutOrigin.current = null
          if (origin?.isConnected) {
            event.preventDefault()
            origin.focus()
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Available in Notes while you are not typing.
          </DialogDescription>
        </DialogHeader>
        <Label className="flex items-center justify-between gap-4 text-sm font-normal">
          <span>Single-key shortcuts</span>
          <Switch
            checked={shortcutsEnabled}
            onCheckedChange={setSingleKeyShortcutsEnabled}
          />
        </Label>
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
  )
}
