import { useState } from "react"
import TurndownService from "turndown"

import { Copy } from "lucide-react"

import { Button } from "@/components/ui/button"

interface CopyButtonProps {
  text?: string
  html?: string
  htmlRef?: React.RefObject<HTMLElement | null>
  className?: string
}

export function CopyButton({
  text,
  html,
  htmlRef,
  className,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const copyToClipboard = async () => {
    try {
      if (!text && !html && !htmlRef?.current) {
        console.error("No text, HTML, or HTML reference to copy")
        return
      }

      const htmlContent = html || htmlRef?.current?.innerHTML || ""
      let textContent = text

      if (!textContent && htmlContent) {
        var turndownService = new TurndownService()
        textContent = turndownService.turndown(htmlContent)
      } else {
        textContent = ""
      }

      if (htmlContent) {
        const clipboardData = new ClipboardItem({
          "text/plain": new Blob([textContent], { type: "text/plain" }),
          "text/html": new Blob([htmlContent], { type: "text/html" }),
        })
        await navigator.clipboard.write([clipboardData])
      } else {
        await navigator.clipboard.writeText(textContent)
      }

      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error("Failed to copy text:", err)
    }
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className={className}
      onClick={copyToClipboard}
    >
      <Copy className="mr-2 h-4 w-4" />
      {copied ? "Copied!" : "Copy to Clipboard"}
    </Button>
  )
}
