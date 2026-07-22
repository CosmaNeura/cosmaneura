import { SidebarInset } from "@/components/ui/sidebar"
import { Header } from "~/components/notes/Header"
import { useTerminology } from "~/utils/terminology"

export const meta = () => {
  return [
    { title: "Intake" },
    {
      name: "description",
      content: "Your patient intake chats",
    },
  ]
}

export function EmptySelectionState() {
  const t = useTerminology()
  return (
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
      <div className="text-muted-foreground text-center">
        <h2 className="mb-2 text-xl font-semibold">Select a {t.patient}</h2>
        <p className="text-sm">
          Choose a {t.patient} from the list to view their intake details
        </p>
      </div>
    </div>
  )
}

export default function NotePage() {
  // the nested route will fill in the main content, so we can assume empty state here
  return (
    <SidebarInset>
      <Header />
      <EmptySelectionState />
    </SidebarInset>
  )
}
