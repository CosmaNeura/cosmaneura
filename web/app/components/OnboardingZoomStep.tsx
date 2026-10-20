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
  allowZoomSkip,
}: {
  onComplete: () => void
  zoomEnabled: boolean
  allowZoomSkip: boolean
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
      <div className="flex flex-col items-center gap-6 text-center">
        <div className="space-y-1">
          <div className="flex items-center justify-center gap-2">
            <h2 className="text-2xl font-semibold">Connect Zoom</h2>
            <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
              Coming Soon
            </span>
          </div>
          <p className="text-muted-foreground text-sm">
            Zoom integration is coming soon. You'll be able to connect your
            account so Cosmas can automatically join your meetings and generate
            visit notes after each session.
          </p>
        </div>
        <Button onClick={onComplete}>Continue</Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Connect Zoom</h2>
        <p className="text-muted-foreground text-sm">
          Connect your Zoom account so Cosmas can automatically join your
          meetings and generate visit notes after each session.
        </p>
      </div>
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
      {allowZoomSkip && (
        <div className="border-border bg-muted/40 w-full space-y-3 rounded-md border p-4">
          <p className="text-muted-foreground text-sm">
            Staging pilot: you can continue now and connect Zoom later from
            Settings.
          </p>
          <Button onClick={onComplete} variant="outline">
            Continue without Zoom
          </Button>
        </div>
      )}
    </div>
  )
}
