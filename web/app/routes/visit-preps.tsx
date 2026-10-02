import { AppSidebar } from "~/components/app-sidebar"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { SidebarInset, SidebarProvider } from "~/components/ui/sidebar"
import { VisitPrepCreator } from "~/components/visit-prep/VisitPrepCreator"
import { VisitPrepEmailPolicy } from "~/components/visit-prep/VisitPrepEmailPolicy"
import { VisitPrepPackages } from "~/components/visit-prep/VisitPrepPackages"
import { useVisitPrepAccess } from "~/configuration/visit-prep-access"

import { useUser } from "@clerk/clerk-react"

export const meta = () => [
  { title: "Visit Prep" },
  {
    name: "description",
    content: "Create invitations and review patient Visit Prep contributions",
  },
]

export default function VisitPrepsPage() {
  const access = useVisitPrepAccess()
  const { user } = useUser()

  return (
    <SidebarProvider
      style={{ "--sidebar-width": "3em" } as React.CSSProperties}
    >
      <AppSidebar isLoading={false} entries={false} />
      <SidebarInset>
        <header className="bg-background sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b p-4">
          <h1 className="text-lg font-semibold">Visit Prep</h1>
        </header>
        <InnerScrollableContent>
          <VisitPrepCreator
            access={access}
            physicianDisplayName={user?.fullName ?? ""}
            scopeKey={user?.id ?? ""}
          />
          <VisitPrepPackages access={access} scopeKey={user?.id ?? ""} />
          <VisitPrepEmailPolicy access={access} scopeKey={user?.id ?? ""} />
        </InnerScrollableContent>
      </SidebarInset>
    </SidebarProvider>
  )
}
