import { withReadDeadline } from "~/lib/read-deadline"
import {
  type ViewAsRequestBinding,
  commitViewAsControlTransition,
  observeViewAsStatusBinding,
  rawViewAsControlRequest,
} from "~/lib/view-as-binding"

export type ViewAsAccountSummary = {
  doctor_id: string
  email: string | null
}

export type ViewAsStatus = {
  request_binding: ViewAsRequestBinding
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

export type ViewAsStatusCheck =
  | { state: "ready"; status: ViewAsStatus }
  | { state: "signed_out"; status: null }
  | { state: "unavailable"; status: null }

async function viewAsRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return rawViewAsControlRequest<T>(path, init)
}

export async function getViewAsStatus(signal?: AbortSignal) {
  const status = await withReadDeadline(
    (readSignal) =>
      viewAsRequest<ViewAsStatus>("/status", { signal: readSignal }),
    { signal },
  )
  // A late status response must not establish or change the browser binding.
  return observeViewAsStatusBinding(status)
}

/**
 * Resolve the browser's view-as state without collapsing an unavailable
 * control-plane check into the ordinary inactive state.
 *
 * Callers that can render provider content must treat `unavailable` as a
 * fail-closed interstitial. An injected reader keeps this behavior directly
 * testable without initializing Clerk.
 */
export async function checkViewAsStatus(
  readStatus: () => Promise<ViewAsStatus> = getViewAsStatus,
): Promise<ViewAsStatusCheck> {
  try {
    return { state: "ready", status: await readStatus() }
  } catch {
    return { state: "unavailable", status: null }
  }
}

/** Check view-as only when Clerk already has an authenticated browser user. */
export async function checkAuthenticatedViewAsStatus(
  hasAuthenticatedSession: () => Promise<boolean> = async () => {
    const { getOptionalClient } = await import("~/configuration/clerk")
    return Boolean((await getOptionalClient())?.session)
  },
  readStatus: () => Promise<ViewAsStatus> = getViewAsStatus,
): Promise<ViewAsStatusCheck> {
  try {
    if (!(await hasAuthenticatedSession())) {
      return { state: "signed_out", status: null }
    }
  } catch {
    return { state: "unavailable", status: null }
  }
  return checkViewAsStatus(readStatus)
}

export async function getViewAsTargets() {
  const response = await viewAsRequest<{ targets: ViewAsTarget[] }>("/targets")
  return response.targets
}

export async function beginViewAs(targetDoctorId: string) {
  const status = await viewAsRequest<ViewAsStatus>("/sessions", {
    method: "POST",
    body: JSON.stringify({ target_doctor_id: targetDoctorId }),
  })
  return commitViewAsControlTransition(status)
}

export async function endViewAs() {
  const status = await viewAsRequest<ViewAsStatus>("/sessions/current", {
    method: "DELETE",
  })
  return commitViewAsControlTransition(status)
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
