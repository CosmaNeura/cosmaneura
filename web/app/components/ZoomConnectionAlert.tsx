import { useEffect, useRef, useState } from "react"

import { AlertCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { zoomAuthUrl, zoomRevokedAck } from "~/configuration/client"
import { settingsDataOptions } from "client/@tanstack/react-query.gen"
import { toast } from "~/hooks/use-toast"
import { isZoomEnabled } from "~/utils/zoom-feature"

import { useUser } from "@clerk/clerk-react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

async function startConnect(onError: () => void) {
  const { data } = await zoomAuthUrl()
  if (data?.url) {
    window.location.href = data.url
  } else {
    onError()
  }
}

export function ZoomConnectionAlert() {
  const { user } = useUser()
  const zoomEnabled = isZoomEnabled(user?.primaryEmailAddress?.emailAddress)
  const queryClient = useQueryClient()
  const { data } = useQuery({
    ...settingsDataOptions(),
    enabled: zoomEnabled,
  })
  const ackedRef = useRef(false)
  const [connecting, setConnecting] = useState(false)

  useEffect(() => {
    if (!data || ackedRef.current) return
    if (!data.zoom_revoked) return
    ackedRef.current = true

    toast({
      title: "Zoom integration disconnected",
      description:
        "Your Zoom OAuth was revoked. Reconnect so Cosmas can keep generating notes for new sessions.",
      variant: "destructive",
      duration: 10000,
    })

    zoomRevokedAck()
      .then(() =>
        queryClient.invalidateQueries({
          queryKey: settingsDataOptions().queryKey,
        }),
      )
      .catch(() => {
        // Swallow — next page load will fall back to the persistent banner.
      })
  }, [data, queryClient])

  if (!zoomEnabled || !data) return null
  if (!data.onboarding_completed) return null
  if (data.zoom_connected) return null

  async function onConnect() {
    setConnecting(true)
    await startConnect(() => {
      toast({
        title: "Failed to start Zoom connection. Please try again.",
        variant: "destructive",
      })
      setConnecting(false)
    })
  }

  return (
    <div
      role="alert"
      className="flex items-center gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-100"
    >
      <AlertCircle className="h-4 w-4 shrink-0" />
      <span className="flex-1">
        Zoom is not connected. Cosmas can&apos;t join your meetings or generate
        notes until you reconnect.
      </span>
      <Button
        size="sm"
        variant="default"
        onClick={onConnect}
        disabled={connecting}
      >
        {connecting ? "Redirecting…" : "Reconnect Zoom"}
      </Button>
    </div>
  )
}

