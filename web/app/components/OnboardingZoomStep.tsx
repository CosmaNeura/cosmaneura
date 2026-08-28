import { useState } from "react"

import { Icons } from "~/components/icons"
import { Button } from "~/components/ui/button"
import { getClient } from "~/configuration/clerk"
import { internalApiFetch } from "~/lib/billing-recovery"
import { requireEnv } from "~/utils/environment"

import { SiZoom } from "react-icons/si"

export function OnboardingZoomStep({
  onComplete,
  zoomEnabled,
}: {
  onComplete: () => void
  zoomEnabled: boolean
}) {
  const [loading, setLoading] = useState(false)

  async function connectZoom() {
    setLoading(true)
    const clerk = await getClient()
    const token = await clerk!.session!.getToken()
    const base = requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
    const res = await internalApiFetch(`${base}/internal/v1/zoom-auth-url`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    })
    const { url } = (await res.json()) as { url: string }
    if (url) window.location.href = url
    setLoading(false)
  }

  if (!zoomEnabled) {
    return (
      <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
        <div className="space-y-2">
          <h2 className="text-2xl font-semibold">
            Start with a visit recording
          </h2>
          <p className="text-muted-foreground text-sm">
            You can start today in Notes: upload a visit recording, then review
            and edit the generated note before pasting it into your chart.
          </p>
          <p className="text-muted-foreground text-sm">
            The Zoom meeting bot is an optional beta and is coming soon for most
            accounts.
          </p>
        </div>
        <div className="flex items-center justify-center gap-2">
          <h3 className="font-semibold">Zoom meeting bot</h3>
          <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
            Coming Soon
          </span>
        </div>
        <Button onClick={onComplete}>Continue with recording uploads</Button>
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold">Start with a visit recording</h2>
        <p className="text-muted-foreground text-sm">
          You can start today in Notes: upload a visit recording, then review
          and edit the generated note before pasting it into your chart.
        </p>
        <p className="text-muted-foreground text-sm">
          The Zoom meeting bot is an optional beta and is coming soon for most
          accounts.
        </p>
      </div>
      <div className="border-border bg-muted/40 w-full space-y-3 rounded-md border p-4">
        <div className="flex items-center justify-center gap-2">
          <h3 className="font-semibold">Zoom meeting bot</h3>
          <span className="bg-background text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
            Beta access
          </span>
        </div>
        <p className="text-muted-foreground text-sm">
          This account has beta access. Connecting Zoom is optional; you can
          continue with recording uploads instead.
        </p>
        <Button onClick={connectZoom} disabled={loading} size="lg">
          {loading ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : (
            <>
              <span className="mr-2 flex h-6 w-6 items-center justify-center rounded-sm border border-white">
                <SiZoom className="size-4 text-white" />
              </span>
              Connect Zoom
            </>
          )}
        </Button>
      </div>
      <Button onClick={onComplete} variant="outline">
        Continue with recording uploads
      </Button>
    </div>
  )
}
