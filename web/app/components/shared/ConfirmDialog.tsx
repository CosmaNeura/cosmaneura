import { useEffect, useRef } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Icons } from "~/components/icons"

// The repo has no AlertDialog primitive, so this composes Dialog with
// alertdialog semantics: the safe choice takes initial focus and Escape,
// and the dialog cannot be dismissed while the confirmed action is running.
// The trigger renders through DialogTrigger so Radix returns focus to it.
export function ConfirmDialog({
  trigger,
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  pendingLabel,
  cancelLabel,
  pending,
  onConfirm,
}: {
  trigger: React.ReactElement
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: React.ReactNode
  confirmLabel: string
  pendingLabel: string
  cancelLabel: string
  pending: boolean
  onConfirm: () => void
}) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const wasPending = useRef(pending)

  // Disabling the focused button while pending drops focus to <body>; after a
  // failed action, give keyboard users a place to continue from.
  useEffect(() => {
    if (wasPending.current && !pending && open) cancelRef.current?.focus()
    wasPending.current = pending
  }, [pending, open])

  function handleOpenChange(nextOpen: boolean) {
    if (pending) return
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        role="alertdialog"
        className="w-[calc(100%-2rem)] rounded-lg sm:max-w-md"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          cancelRef.current?.focus()
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            ref={cancelRef}
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => handleOpenChange(false)}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                {pendingLabel}
              </>
            ) : (
              confirmLabel
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
