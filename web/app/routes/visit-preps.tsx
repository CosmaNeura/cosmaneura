import { AppSidebar } from "~/components/app-sidebar"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { SidebarInset, SidebarProvider } from "~/components/ui/sidebar"
import { VisitPrepCreator } from "~/components/visit-prep/VisitPrepCreator"
import { useVisitPrepAccess } from "~/configuration/visit-prep-access"

import { useUser } from "@clerk/clerk-react"

export const meta = () => [
  { title: "Visit Prep" },
  {
    name: "description",
    content: "Create and manage a patient Visit Prep invitation",
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
          />
        </InnerScrollableContent>
      </SidebarInset>
    </SidebarProvider>
  )
}
