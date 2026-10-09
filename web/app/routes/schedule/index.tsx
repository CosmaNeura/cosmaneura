import { useState } from "react"
import { useRouteLoaderData } from "react-router"

import { Button } from "@/components/ui/button"
import { SidebarInset } from "@/components/ui/sidebar"
import { Icons } from "~/components/icons"
import { PageHeader } from "~/components/shared/PageHeader"
import { getClient } from "~/configuration/clerk"
import { internalApiFetch } from "~/lib/billing-recovery"
import { requireEnv } from "~/utils/environment"
import { useTerminology } from "~/utils/terminology"
import { isZoomEnabled } from "~/utils/zoom-feature"

import { useUser } from "@clerk/clerk-react"

function ZoomConnectPrompt() {
  const t = useTerminology()
  const [loading, setLoading] = useState(false)
  const { user } = useUser()
  const zoomEnabled = isZoomEnabled(user?.primaryEmailAddress?.emailAddress)

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
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center px-6">
      <div className="max-w-xl text-center">
        <h2 className="mb-2 text-xl font-semibold">
          Connect your Zoom account
        </h2>
        <p className="text-muted-foreground mb-4 text-sm">
          Connect Zoom to view your meeting schedule and access AI notes after
          each session.
        </p>
        <div className="text-muted-foreground bg-muted/40 mb-6 rounded-md border p-3 text-left text-xs leading-relaxed">
          <p className="mb-2">
            By connecting Zoom, you authorize Cosmas to join your scheduled Zoom
            meetings on your behalf to record audio, generate transcripts, and
            produce clinical notes. Audio and transcripts are processed by our
            subprocessors (Recall.ai for meeting capture, and Azure/OpenAI for
            transcription and note generation).
          </p>
          <p className="mb-2">
            <strong>
              {t.patientTitle} consent is your responsibility as the provider.
            </strong>{" "}
            The Cosmas AI Note-Taker bot will appear as a participant and
            announce that the meeting is being recorded; you must still obtain
            and document {t.patient} consent according to HIPAA and your state's
            requirements before the visit.
          </p>
          <p>
            See our{" "}
            <a
              href="/privacy-policy"
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              Privacy Policy
            </a>{" "}
            and{" "}
            <a
              href="/terms-of-service"
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              Terms of Service
            </a>
            . You can disconnect Zoom at any time from Settings.
          </p>
        </div>
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
      <PageHeader title="Schedule" isPageTitle />
      {loaderData?.zoom_connected === false ? (
        <ZoomConnectPrompt />
      ) : (
        <EmptySelectionState />
      )}
    </SidebarInset>
  )
}
