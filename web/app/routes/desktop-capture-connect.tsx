import { useState } from "react"

import { Button } from "~/components/ui/button"
import { takeDesktopCaptureHandoff } from "~/configuration/bootstrap-desktop-capture"
import { authorizeDesktopCaptureLaunch } from "~/configuration/client"
import {
  type DesktopCaptureHandoff,
  buildDesktopAuthorizationCallback,
  parseDesktopCaptureHandoff,
} from "~/lib/desktop-capture-handoff"

type ConnectPhase = "ready" | "authorizing" | "opening" | "error"

export async function exchangeDesktopCaptureHandoff(
  handoff: DesktopCaptureHandoff,
  authorize = authorizeDesktopCaptureLaunch,
): Promise<string | null> {
  const { data, error } = await authorize({
    body: {
      launch_ticket: handoff.launchTicket,
      code_challenge: handoff.codeChallenge,
      code_challenge_method: "S256",
      redirect_uri: handoff.redirectUri,
      installation_thumbprint: handoff.installationThumbprint,
    },
  })
  if (error || !data) return null
  return buildDesktopAuthorizationCallback(
    data.authorization_code,
    data.redirect_uri,
    handoff.state,
  )
}

export default function DesktopCaptureConnectPage() {
  const [handoff] = useState(() =>
    parseDesktopCaptureHandoff(takeDesktopCaptureHandoff()),
  )
  const [phase, setPhase] = useState<ConnectPhase>(handoff ? "ready" : "error")
  const [callback, setCallback] = useState<string | null>(null)

  async function authorizeAndOpen() {
    if (!handoff || phase === "authorizing") return
    setPhase("authorizing")
    try {
      const nextCallback = await exchangeDesktopCaptureHandoff(handoff)
      if (!nextCallback) {
        setPhase("error")
        return
      }
      setCallback(nextCallback)
      setPhase("opening")
      window.location.assign(nextCallback)
    } catch {
      setPhase("error")
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-xl items-center px-6 py-12">
      <section className="w-full rounded-2xl border bg-white p-7 shadow-sm">
        <p className="text-primary text-sm font-semibold tracking-wide uppercase">
          Cosmas Capture
        </p>
        <h1 className="mt-2 text-2xl font-semibold">
          Connect the desktop companion
        </h1>
        <p className="text-muted-foreground mt-3 text-sm leading-6">
          Cosmas will verify this one appointment and its current consent
          record. Patient details and account credentials are not sent to the
          desktop window.
        </p>

        {phase === "error" ? (
          <div className="mt-6 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
            This secure handoff is invalid or expired. Return to Cosmas Capture
            and start again.
          </div>
        ) : (
          <div className="mt-6 flex flex-wrap gap-3">
            <Button
              disabled={phase === "authorizing"}
              type="button"
              onClick={() =>
                callback
                  ? window.location.assign(callback)
                  : void authorizeAndOpen()
              }
            >
              {phase === "authorizing"
                ? "Verifying…"
                : phase === "opening"
                  ? "Open Cosmas Capture again"
                  : "Verify and open Cosmas Capture"}
            </Button>
            {phase === "opening" && callback ? (
              <Button
                variant="outline"
                type="button"
                onClick={() => window.location.assign(callback)}
              >
                Retry opening the app
              </Button>
            ) : null}
          </div>
        )}
      </section>
    </main>
  )
}
