import { useEffect, useRef, useState } from "react"

import { AlertCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { zoomAuthUrl, zoomRevokedAck } from "~/configuration/client"
import { SETTINGS_STALE_TIME } from "~/configuration/tanstack-query"
import { toast } from "~/hooks/use-toast"
import { isZoomEnabled } from "~/utils/zoom-feature"

import { useUser } from "@clerk/clerk-react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { settingsDataOptions } from "client/@tanstack/react-query.gen"

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
    // Match the authenticated-layout loader's staleTime so this reads the
    // prefetched settings from cache instead of firing a second request.
    staleTime: SETTINGS_STALE_TIME,
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
        "The Cosmas meeting bot was disconnected. Reconnect to use the bot; Scribe app recording remains available.",
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
      className="border-warning/40 bg-warning/15 text-foreground flex items-center gap-3 border-b px-4 py-2 text-sm"
    >
      <AlertCircle className="h-4 w-4 shrink-0" />
      <span className="flex-1">
        The Cosmas meeting bot is not connected to Zoom. You can still record
        your meeting and prepare notes with the Scribe desktop app.
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
