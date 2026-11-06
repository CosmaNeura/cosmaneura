import { AppSidebar } from "@/components/app-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { VisitPrepWorkspace } from "~/components/visit-prep/VisitPrepWorkspace"

export default function VisitPrepsRoute() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset><VisitPrepWorkspace /></SidebarInset>
    </SidebarProvider>
  )
}
