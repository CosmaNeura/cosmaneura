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

async function viewAsRequest<T>(
  path: string,
  init?: RequestInit,
  sessionToken?: string,
): Promise<T> {
  return rawViewAsControlRequest<T>(
    path,
    init,
    sessionToken ? { sessionToken, redirectOnUnauthorized: false } : undefined,
  )
}

export async function getViewAsStatus(sessionToken?: string) {
  const status = await viewAsRequest<ViewAsStatus>(
    "/status",
    undefined,
    sessionToken,
  )
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
  readStatus: (
    sessionToken?: string,
  ) => Promise<ViewAsStatus> = getViewAsStatus,
): Promise<ViewAsStatusCheck> {
  try {
    return { state: "ready", status: await readStatus() }
  } catch {
    return { state: "unavailable", status: null }
  }
}

/** Check view-as only when Clerk can issue a current token, without redirecting. */
export async function checkAuthenticatedViewAsStatus(
  readSessionToken: () => Promise<string | null> = async () => {
    const { getOptionalSessionToken } = await import("~/configuration/clerk")
    return getOptionalSessionToken()
  },
  readStatus: (sessionToken: string) => Promise<ViewAsStatus> = getViewAsStatus,
): Promise<ViewAsStatusCheck> {
  try {
    const sessionToken = await readSessionToken()
    if (!sessionToken) {
      return { state: "signed_out", status: null }
    }
    return { state: "ready", status: await readStatus(sessionToken) }
  } catch {
    // A backend 401 with a non-null Clerk token does not prove the browser is
    // signed out, and exposing account controls could create a redirect loop.
    return { state: "unavailable", status: null }
  }
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
