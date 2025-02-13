import { SidebarInset } from "@/components/ui/sidebar"
import { Header } from "~/components/notes/Header"

export const meta = () => {
  return [
    { title: "Notetaking" },
    {
      name: "description",
      content: "Your patient meeting notes",
    },
  ]
}

export function EmptySelectionState() {
  return (
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
      <div className="text-muted-foreground text-center">
        <h2 className="mb-2 text-xl font-semibold">Select a patient</h2>
        <p className="text-sm">
          Choose a patient from the list to view their details
        </p>
      </div>
    </div>
  )
}

export default function NotePage() {
  return (
    <SidebarInset>
      <Header />
      <EmptySelectionState />
    </SidebarInset>
  )
}
