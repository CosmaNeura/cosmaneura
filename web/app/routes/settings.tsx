import { Upload } from "lucide-react"

import { AppSidebar } from "@/components/app-sidebar"
import { Button } from "@/components/ui/button"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

function IntakeUpload() {
  return (
    <section className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-left text-xl font-semibold">Intake</h2>
        <p className="text-left text-sm text-muted-foreground">
          Upload the best intake notes that patients have provided you in the
          past. This will be used to create an intake process for each
          appointment type.
        </p>
      </div>
      <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border p-8">
        <div className="flex flex-col items-center justify-center gap-2">
          <div className="rounded-full bg-muted p-2">
            <Upload className="h-4 w-4" />
          </div>
          <div className="text-center">
            <p className="text-sm font-medium">Drag & drop files here</p>
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
  )
}

function NoteUpload() {
  return (
    <section className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-left text-xl font-semibold">Notetaking</h2>
        <p className="text-left text-sm text-muted-foreground">
          Upload high quality examples of the different types of notes that you
          use. These examples will be used to generate future notes for your
          patients, so make sure they are a great representation of the
          structure and quality of note that you want to see from your
          personalized AI!
        </p>
        <p className="text-left text-sm text-muted-foreground">
          For example: if you use the telehealth SOAP note, pick a high-quality
          note for that note type and upload a PDF version of this note type.
        </p>
      </div>
      <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border p-8">
        <div className="flex flex-col items-center justify-center gap-2">
          <div className="rounded-full bg-muted p-2">
            <Upload className="h-4 w-4" />
          </div>
          <div className="text-center">
            <p className="text-sm font-medium">Drag & drop files here</p>
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
  )
}

export default function SettingsPage() {
  return (
    <>
      <SidebarProvider>
        <AppSidebar isLoading={false} entries={false} />

        <SidebarInset>
          <header className="sticky top-0 flex shrink-0 items-center gap-2 border-b bg-background p-4">
            <h1 className="text-lg font-semibold">Settings</h1>
          </header>
          <div className="flex flex-col gap-8 p-6">
            <IntakeUpload />
            <NoteUpload />
          </div>
        </SidebarInset>
      </SidebarProvider>
    </>
  )
}
