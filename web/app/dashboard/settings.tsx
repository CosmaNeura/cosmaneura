import { useOutletContext } from "react-router"

import { Upload } from "lucide-react"

import { AppSidebar } from "@/components/app-sidebar"
import { PageTransition } from "@/components/page-transition"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"

type ContextType = { onNavigate: (page: string) => void; currentPage: string }

export default function SettingsPage() {
  const { onNavigate, currentPage } = useOutletContext<ContextType>()

  return (
    <PageTransition>
      <SidebarProvider
        style={
          {
            "--sidebar-width": "350px",
          } as React.CSSProperties
        }
      >
        <AppSidebar onNavigate={onNavigate} currentPage={currentPage} />
        <SidebarInset>
          <header className="sticky top-0 flex shrink-0 items-center gap-2 border-b bg-background p-4">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-2 h-4" />
            <h1 className="text-lg font-semibold">Settings</h1>
          </header>
          <div className="flex flex-col gap-8 p-6">
            <section className="space-y-4">
              <div className="space-y-2">
                <h2 className="text-xl font-semibold text-left">Intake</h2>
                <p className="text-sm text-muted-foreground text-left">
                  Upload the best intake notes that patients have provided you
                  in the past. This will be used to create an intake process for
                  each appointment type.
                </p>
              </div>
              <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border p-8">
                <div className="flex flex-col items-center justify-center gap-2">
                  <div className="rounded-full bg-muted p-2">
                    <Upload className="h-4 w-4" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium">
                      Drag & drop files here
                    </p>
                    <p className="text-xs text-muted-foreground">
                      or click to browse files
                    </p>
                  </div>
                  <Button size="sm" variant="secondary" className="mt-2">
                    Select Files
                  </Button>
                </div>
              </div>
            </section>

            <section className="space-y-4">
              <div className="space-y-2">
                <h2 className="text-xl font-semibold text-left">Notetaking</h2>
                <p className="text-sm text-muted-foreground text-left">
                  Upload high quality examples of the different types of notes
                  that you use. These examples will be used to generate future
                  notes for your patients, so make sure they are a great
                  representation of the structure and quality of note that you
                  want to see from your personalized AI!
                </p>
                <p className="text-sm text-muted-foreground text-left">
                  For example: if you use the telehealth SOAP note, pick a
                  high-quality note for that note type and upload a PDF version
                  of this note type.
                </p>
              </div>
              <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border p-8">
                <div className="flex flex-col items-center justify-center gap-2">
                  <div className="rounded-full bg-muted p-2">
                    <Upload className="h-4 w-4" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium">
                      Drag & drop files here
                    </p>
                    <p className="text-xs text-muted-foreground">
                      or click to browse files
                    </p>
                  </div>
                  <Button size="sm" variant="secondary" className="mt-2">
                    Select Files
                  </Button>
                </div>
              </div>
            </section>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </PageTransition>
  )
}
