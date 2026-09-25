import { Link, useRevalidator } from "react-router"

import { Button } from "@/components/ui/button"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import type { NoteLoadFailure } from "~/lib/note-detail-load"

const messages = {
  unavailable: {
    title: "Visit unavailable",
    detail:
      "This visit is unavailable or you don’t have access to it. Check the link and the account you’re signed in with.",
  },
  session: {
    title: "Your session needs verification",
    detail:
      "Try again to verify your session, or return to your visits and sign in if prompted.",
  },
  temporary: {
    title: "Couldn’t load this visit",
    detail:
      "Check your connection and try again. Your saved notes have not changed.",
  },
}

/** Keep the notes sidebar available; never mount a record or assistant on failure. */
export function NoteDetailRecovery({
  kind,
  visits = false,
}: {
  kind: NoteLoadFailure
  visits?: boolean
}) {
  const revalidator = useRevalidator()
  const pending = revalidator.state === "loading"
  const message = visits
    ? { ...messages.temporary, title: "Couldn’t load your visits" }
    : messages[kind]
  return (
    <SidebarInset>
      <header className="flex items-center gap-2 border-b p-4">
        <SidebarTrigger />
        <span>Notes</span>
      </header>
      <main className="mx-auto w-full max-w-lg px-6 py-12" role="alert">
        <h1 className="text-xl font-semibold">{message.title}</h1>
        <p className="text-muted-foreground mt-3">{message.detail}</p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button
            type="button"
            disabled={pending}
            onClick={() => void revalidator.revalidate()}
          >
            {pending ? "Trying again…" : "Try again"}
          </Button>
          <Button asChild variant="outline">
            <Link to="/notes">Return to visits</Link>
          </Button>
        </div>
      </main>
    </SidebarInset>
  )
}
