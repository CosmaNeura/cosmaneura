import type { Route } from "./+types/scribe.setup"

import { ViewAsRouteGuard } from "~/components/ViewAsRouteGuard"
import { BrandLogo } from "~/components/brand/BrandLogo"
import { ScribePracticeSetup } from "~/components/shared/ScribePracticeSetup"
import { getClient } from "~/configuration/clerk"
import { getPractice } from "~/lib/practice-api"
import { checkViewAsStatus } from "~/lib/view-as-api"

export async function clientLoader() {
  if (!(await getClient())) return { kind: "signing-in" as const }
  const check = await checkViewAsStatus()
  if (check.state !== "ready" || check.status.active)
    return { kind: "blocked" as const, check }
  try {
    return { kind: "ready" as const, check, practice: await getPractice() }
  } catch {
    return { kind: "unavailable" as const, check }
  }
}

export default function ScribeSetupPage({ loaderData }: Route.ComponentProps) {
  if (loaderData.kind === "signing-in")
    return <p>Sign in to open Scribe setup.</p>
  return (
    <ViewAsRouteGuard
      check={loaderData.check}
      className="bg-background min-h-screen"
    >
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-3xl items-center px-5">
          <BrandLogo variant="dark" size="sm" />
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-5 py-10">
        {loaderData.kind === "ready" ? (
          <ScribePracticeSetup practice={loaderData.practice} />
        ) : loaderData.kind === "blocked" ? (
          <ScribePracticeSetup practice={null} viewAs />
        ) : (
          <section>
            <h1 className="text-2xl font-semibold">
              Practice setup could not be checked
            </h1>
            <p className="mt-3">
              Check your session and reload this page. No setup changes were
              made.
            </p>
          </section>
        )}
      </main>
    </ViewAsRouteGuard>
  )
}
