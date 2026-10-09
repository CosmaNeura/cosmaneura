import { useEffect, useState } from "react"
import { href } from "react-router"

import { Eye, ShieldAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  type ViewAsStatus,
  type ViewAsTarget,
  beginViewAs,
  endViewAs,
  getViewAsTargets,
  viewAsDiagnosticCopy,
} from "~/lib/view-as-api"

function accountLabel(account: { doctor_id: string; email: string | null }) {
  return account.email ?? `Account …${account.doctor_id.slice(-8)}`
}

export default function AdminBar({
  initialStatus,
}: {
  initialStatus: ViewAsStatus
}) {
  const status = initialStatus
  const [targets, setTargets] = useState<ViewAsTarget[]>([])
  const [selectedTarget, setSelectedTarget] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!status.can_view_as || status.active) return
    let cancelled = false
    void getViewAsTargets()
      .then((available) => {
        if (!cancelled) setTargets(available)
      })
      .catch(() => {
        if (!cancelled) setError("Support accounts could not be loaded.")
      })
    return () => {
      cancelled = true
    }
  }, [status.active, status.can_view_as])

  if (!status.can_view_as) return null

  async function activate() {
    if (!selectedTarget) return
    setBusy(true)
    setError(null)
    try {
      await beginViewAs(selectedTarget)
      window.location.replace(href("/"))
    } catch {
      setError("View-as could not be activated.")
      setBusy(false)
    }
  }

  async function exit() {
    setBusy(true)
    setError(null)
    try {
      await endViewAs()
      window.location.replace(href("/"))
    } catch {
      setError("View-as could not be ended. Reload and try again.")
      setBusy(false)
    }
  }

  if (status.active && status.actor && status.target) {
    return (
      <div
        className="z-[100003] flex w-full flex-wrap items-center gap-3 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-950 shadow-sm"
        role="status"
        data-view-as-banner
      >
        <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
        <strong>Read-only view-as</strong>
        <span>
          {accountLabel(status.actor)} → {accountLabel(status.target)}
        </span>
        {status.diagnostic_code ? (
          <span className="font-medium">
            {viewAsDiagnosticCopy(status.diagnostic_code)}
          </span>
        ) : null}
        <Button
          onClick={() => void exit()}
          disabled={busy}
          size="sm"
          variant="outline"
          className="ml-auto border-amber-500 bg-white"
        >
          Exit view-as
        </Button>
        {error ? <span role="alert">{error}</span> : null}
      </div>
    )
  }

  return (
    <div className="z-50 flex w-full flex-wrap items-center gap-2 border-b bg-white px-4 py-2 text-xs shadow-sm">
      <Eye className="text-muted-foreground h-4 w-4" aria-hidden="true" />
      <span className="font-medium">Support view · read-only</span>
      <Select value={selectedTarget} onValueChange={setSelectedTarget}>
        <SelectTrigger className="h-8 min-w-64 text-sm">
          <SelectValue placeholder="Select an account to view" />
        </SelectTrigger>
        <SelectContent>
          {status.actor ? <SelectItem value={status.actor.doctor_id} disabled>{accountLabel(status.actor)} — signed in</SelectItem> : null}
          {targets.map((target) => (
            <SelectItem key={target.doctor_id} value={target.doctor_id}>
              {accountLabel(target)}
              {target.diagnostic_code ? " — needs attention" : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        onClick={() => void activate()}
        disabled={!selectedTarget || busy}
        size="sm"
        variant="outline"
      >
        View read-only
      </Button>
      <span className="text-muted-foreground">To work as another account, use the account menu to sign in.</span>
      {error ? <span role="alert">{error}</span> : null}
    </div>
  )
}
