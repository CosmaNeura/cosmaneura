import { useViewAsActive } from "~/components/ViewAsRouteGuard"

import { UserButton, useUser } from "@clerk/clerk-react"

export function ViewAsAccountControl() {
  const viewAsActive = useViewAsActive()
  const { user } = useUser()
  const email = user?.primaryEmailAddress?.emailAddress
  const staging = (import.meta.env.VITE_PYTHON_URL ?? "").includes("staging-app.cosmaneura.com")

  return (
    <div className="flex max-w-full flex-wrap items-center justify-end gap-2">
      {staging ? <span className="rounded bg-teal-50 px-2 py-1 text-xs font-medium text-teal-900">Staging</span> : null}
      {email ? <span className="max-w-56 break-all text-xs text-muted-foreground">Signed in as {email}</span> : null}
      {viewAsActive ? (
        <span className="text-muted-foreground px-2 text-center text-xs" data-view-as-account-controls-disabled>
          Account controls disabled
        </span>
      ) : <UserButton />}
    </div>
  )
}
