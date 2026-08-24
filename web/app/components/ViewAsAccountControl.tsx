import { useViewAsActive } from "~/components/ViewAsRouteGuard"

import { UserButton } from "@clerk/clerk-react"

export function ViewAsAccountControl() {
  const viewAsActive = useViewAsActive()

  if (viewAsActive) {
    return (
      <span
        className="text-muted-foreground px-2 text-center text-xs"
        data-view-as-account-controls-disabled
      >
        Account controls disabled
      </span>
    )
  }

  return <UserButton />
}
