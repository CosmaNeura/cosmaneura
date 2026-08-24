import { getClient } from "~/configuration/clerk"
import { internalApiFetch } from "~/lib/billing-recovery"
import { requireEnv } from "~/utils/environment"

export type ViewAsAccountSummary = {
  doctor_id: string
  email: string | null
}

export type ViewAsStatus = {
  can_view_as: boolean
  active: boolean
  read_only: boolean
  diagnostic_code: string | null
  expires_at: string | null
  actor: ViewAsAccountSummary | null
  target: ViewAsAccountSummary | null
}

export type ViewAsTarget = ViewAsAccountSummary & {
  diagnostic_code: string | null
}

function apiBase() {
  return requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
}

async function authHeaders(): Promise<HeadersInit> {
  const clerk = await getClient()
  const token = await clerk?.session?.getToken()
  if (!token) throw new Error("Provider session unavailable")
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  }
}

async function viewAsRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await internalApiFetch(
    `${apiBase()}/internal/v1/view-as${path}`,
    {
      ...init,
      credentials: "include",
      cache: "no-store",
      headers: { ...(await authHeaders()), ...(init?.headers ?? {}) },
    },
  )
  if (!response.ok) throw new Error("View-as is unavailable")
  return (await response.json()) as T
}

export function getViewAsStatus() {
  return viewAsRequest<ViewAsStatus>("/status")
}

export async function getViewAsTargets() {
  const response = await viewAsRequest<{ targets: ViewAsTarget[] }>("/targets")
  return response.targets
}

export function beginViewAs(targetDoctorId: string) {
  return viewAsRequest<ViewAsStatus>("/sessions", {
    method: "POST",
    body: JSON.stringify({ target_doctor_id: targetDoctorId }),
  })
}

export function endViewAs() {
  return viewAsRequest<ViewAsStatus>("/sessions/current", {
    method: "DELETE",
  })
}

export function viewAsDiagnosticCopy(code: string | null) {
  switch (code) {
    case "target_disabled":
    case "target_access_revoked":
      return "This account is disabled or its access has been revoked."
    case "target_identity_unready":
    case "target_identity_ambiguous":
      return "This account's identity needs administrative reconciliation."
    default:
      return "This account is not ready for interactive access yet."
  }
}
