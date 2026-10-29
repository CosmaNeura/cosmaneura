import { useViewAsActive } from "~/components/ViewAsRouteGuard"
import { getDeploymentEnvironment } from "~/utils/deployment-environment"

import { UserButton, useUser } from "@clerk/clerk-react"

export function ViewAsAccountControl({
  compact = false,
}: {
  compact?: boolean
}) {
  const viewAsActive = useViewAsActive()
  const { user } = useUser()
  const email = user?.primaryEmailAddress?.emailAddress
  const staging = getDeploymentEnvironment() === "staging"

  return (
    <div
      title={compact && email ? `Signed in as ${email}` : undefined}
      className={
        compact
          ? "flex max-w-full flex-col items-center gap-2"
          : "flex max-w-full flex-wrap items-center justify-end gap-2"
      }
    >
      {staging ? (
        <span
          className={
            compact
              ? "sr-only"
              : "rounded bg-teal-50 px-2 py-1 text-xs font-medium text-teal-900"
          }
        >
          Staging
        </span>
      ) : null}
      {staging && compact ? (
        <span
          aria-hidden="true"
          title="Staging"
          className="h-2 w-2 shrink-0 rounded-full bg-teal-400"
        />
      ) : null}
      {email ? (
        <span
          className={
            compact
              ? "sr-only"
              : "text-muted-foreground max-w-56 text-xs break-all"
          }
        >
          Signed in as {email}
        </span>
      ) : null}
      {viewAsActive ? (
        <span
          className={
            compact
              ? "sr-only"
              : "text-muted-foreground px-2 text-center text-xs"
          }
          data-view-as-account-controls-disabled
        >
          Account controls disabled
        </span>
      ) : (
        <UserButton />
      )}
    </div>
  )
}
