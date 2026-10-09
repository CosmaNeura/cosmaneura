// https://github.com/shadcn-ui/ui/discussions/4052
import { useEffect, useRef, useState } from "react"
import TurndownService from "turndown"

import { Check, Copy } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { toast } from "~/hooks/use-toast"

const COPIED_DURATION_MS = 2000

interface CopyButtonProps extends React.ComponentProps<typeof Button> {
  text?: string
  html?: string
  htmlRef?: React.RefObject<HTMLElement | null>
  // Builds the HTML at click time, for content that is not a single element.
  getHtml?: () => string
  label?: string
  iconOnly?: boolean
}

function resolveContent({
  text,
  html,
  htmlRef,
  getHtml,
}: Pick<CopyButtonProps, "text" | "html" | "htmlRef" | "getHtml">) {
  const htmlContent = html || getHtml?.() || htmlRef?.current?.innerHTML || ""
  const textContent =
    text || (htmlContent ? new TurndownService().turndown(htmlContent) : "")
  return { htmlContent, textContent }
}

export function CopyButton({
  text,
  html,
  htmlRef,
  getHtml,
  label = "Copy to Clipboard",
  iconOnly = false,
  className,
  ...props
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const resetTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => () => clearTimeout(resetTimer.current), [])

  const copyToClipboard = async () => {
    try {
      const { htmlContent, textContent } = resolveContent({
        text,
        html,
        htmlRef,
        getHtml,
      })
      if (!textContent) throw new Error("nothing to copy")

      if (htmlContent) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/plain": new Blob([textContent], { type: "text/plain" }),
            "text/html": new Blob([htmlContent], { type: "text/html" }),
          }),
        ])
      } else {
        await navigator.clipboard.writeText(textContent)
      }

      setCopied(true)
      clearTimeout(resetTimer.current)
      resetTimer.current = setTimeout(
        () => setCopied(false),
        COPIED_DURATION_MS,
      )
    } catch {
      toast({
        title: "Couldn't copy",
        description:
          "Your browser blocked clipboard access. Try selecting the text and copying it instead.",
        variant: "destructive",
      })
    }
  }

  const Icon = copied ? Check : Copy

  return (
    <>
      <Button
        variant="outline"
        size={iconOnly ? "icon" : "sm"}
        className={cn(iconOnly && "size-7", className)}
        onClick={copyToClipboard}
        aria-label={iconOnly ? (copied ? "Copied" : label) : undefined}
        {...props}
      >
        <Icon
          className={cn(
            "h-4 w-4",
            !iconOnly && "mr-2",
            copied && "text-success",
          )}
          aria-hidden="true"
        />
        {!iconOnly && (copied ? "Copied" : label)}
      </Button>
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </>
  )
}
