import { useState } from "react"
import { useRouteLoaderData } from "react-router"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SidebarInset } from "@/components/ui/sidebar"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Icons } from "~/components/icons"
import { getClient } from "~/configuration/clerk"
import { requireEnv } from "~/utils/environment"

const ZOOM_COMING_SOON = false

function ZoomConnectPrompt() {
  const [loading, setLoading] = useState(false)

  async function connectZoom() {
    setLoading(true)
    const clerk = await getClient()
    const token = await clerk!.session!.getToken()
    const base = requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
    const res = await fetch(`${base}/internal/v1/zoom-auth-url`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    })
    const { url } = (await res.json()) as { url: string }
    if (url) window.location.href = url
    setLoading(false)
  }

  if (ZOOM_COMING_SOON) {
    return (
      <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
        <div className="text-center">
          <h2 className="mb-2 text-xl font-semibold">
            Zoom integration coming soon
          </h2>
          <p className="text-muted-foreground text-sm">
            You'll be able to connect Zoom to view your meeting schedule and
            access AI notes after each session.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
      <div className="text-center">
        <h2 className="mb-2 text-xl font-semibold">
          Connect your Zoom account
        </h2>
        <p className="text-muted-foreground mb-6 text-sm">
          Connect Zoom to view your meeting schedule and access AI notes after
          each session.
        </p>
        <Button onClick={connectZoom} disabled={loading}>
          {loading ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : (
            "Connect Zoom"
          )}
        </Button>
      </div>
    </div>
  )
}

function EmptySelectionState() {
  return (
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
      <div className="text-muted-foreground text-center">
        <h2 className="mb-2 text-xl font-semibold">Select a meeting</h2>
        <p className="text-sm">
          Choose a meeting from the list to view details
        </p>
      </div>
    </div>
  )
}

export default function ScheduleIndexPage() {
  // loaderData comes from the parent layout (schedule.tsx clientLoader)
  const loaderData = useRouteLoaderData("layouts/schedule") as
    | { zoom_connected: boolean }
    | undefined

  return (
    <SidebarInset>
      <header className="bg-background sticky top-0 flex shrink-0 items-center gap-2 border-b p-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-lg font-semibold">Schedule</h1>
      </header>
      {loaderData?.zoom_connected === false ? (
        <ZoomConnectPrompt />
      ) : (
        <EmptySelectionState />
      )}
    </SidebarInset>
  )
}
