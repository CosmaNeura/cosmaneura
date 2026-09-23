import { useState } from "react"
import { useRevalidator } from "react-router"

import { Button } from "@/components/ui/button"
import { Icons } from "~/components/icons"
import { toast } from "~/hooks/use-toast"

type ConnectResult = { data?: { url?: string } | null; error?: unknown }
type DisconnectResult = { error?: unknown }

export function SchedulingProviderConnect({
  title,
  description,
  connected,
  onConnect,
  onDisconnect,
}: {
  title: string
  description: string
  connected: boolean
  onConnect: () => Promise<ConnectResult>
  onDisconnect: () => Promise<DisconnectResult>
}) {
  const revalidator = useRevalidator()
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  async function handleConnect() {
    setConnecting(true)
    const { data } = await onConnect()
    if (data?.url) {
      window.location.href = data.url
    } else {
      toast({
        title: `Failed to start ${title} connection. Please try again.`,
        variant: "destructive",
      })
      setConnecting(false)
    }
  }

  async function handleDisconnect() {
    setDisconnecting(true)
    const { error } = await onDisconnect()
    if (error) {
      toast({
        title: "Failed to disconnect. Please try again.",
        variant: "destructive",
      })
      setDisconnecting(false)
      return
    }
    toast({ title: `${title} disconnected` })
    setDisconnecting(false)
    revalidator.revalidate()
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>{title}</h3>
      <p className="text-muted-foreground text-sm">{description}</p>

      {connected ? (
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-green-500" />
            Connected
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleDisconnect}
            disabled={disconnecting}
          >
            {disconnecting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Disconnecting...
              </>
            ) : (
              "Disconnect"
            )}
          </Button>
        </div>
      ) : (
        <Button onClick={handleConnect} disabled={connecting}>
          {connecting ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : (
            `Connect ${title}`
          )}
        </Button>
      )}
    </section>
  )
}
