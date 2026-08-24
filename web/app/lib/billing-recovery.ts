import {
  bindInternalApiRequest,
  enforceViewAsBindingResponse,
  isViewAsControlRequest,
} from "~/lib/view-as-binding"

const DEFAULT_BILLING_RETURN_TO = "/notes"
const SETTINGS_PATH = "/settings"

let pendingBillingRedirect: string | null = null

export class BillingRecoveryRedirectError extends Error {
  readonly redirectTo: string

  constructor(redirectTo: string) {
    super("Redirecting to restore practice billing access.")
    this.name = "BillingRecoveryRedirectError"
    this.redirectTo = redirectTo
  }
}

export function isBillingRecoveryRedirectError(
  error: unknown,
): error is BillingRecoveryRedirectError {
  return (
    error instanceof BillingRecoveryRedirectError ||
    Boolean(
      error &&
        typeof error === "object" &&
        "name" in error &&
        error.name === "BillingRecoveryRedirectError",
    )
  )
}

export function safeBillingReturnTo(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return DEFAULT_BILLING_RETURN_TO
  }

  const appOrigin = "https://app.cosmaneura.invalid"
  const target = new URL(value, appOrigin)
  if (target.origin !== appOrigin) return DEFAULT_BILLING_RETURN_TO
  return `${target.pathname}${target.search}${target.hash}`
}

export function billingRecoveryLocation(requestUrl: string) {
  const current = new URL(requestUrl)
  const params = new URLSearchParams({
    billing: "required",
    returnTo: safeBillingReturnTo(
      `${current.pathname}${current.search}${current.hash}`,
    ),
  })
  return `${SETTINGS_PATH}?${params.toString()}`
}

export function restrictSettingsToBillingRecovery(
  recoveryRequired: boolean,
  practiceEntitled: boolean | null | undefined,
) {
  return recoveryRequired && practiceEntitled !== true
}

function isSettingsLocation(requestUrl: string) {
  const pathname = new URL(requestUrl).pathname.replace(/\/+$/, "") || "/"
  return pathname === SETTINGS_PATH
}

export function isInternalApiRequest(
  input: RequestInfo | URL,
  currentUrl: string,
) {
  try {
    const rawUrl = input instanceof Request ? input.url : input.toString()
    const url = new URL(rawUrl, currentUrl)
    return (
      url.pathname === "/internal/v1" ||
      url.pathname.startsWith("/internal/v1/")
    )
  } catch {
    return false
  }
}

export function handleInternalApiResponse(
  response: Response,
  currentUrl: string,
  navigate: (target: string) => void,
) {
  if (response.status !== 402 || isSettingsLocation(currentUrl)) {
    return response
  }

  const target = billingRecoveryLocation(currentUrl)
  if (pendingBillingRedirect === null) {
    pendingBillingRedirect = target
    navigate(target)
  }
  throw new BillingRecoveryRedirectError(pendingBillingRedirect)
}

/** Fetch for authenticated internal APIs only; external/public calls stay untouched. */
export const internalApiFetch: typeof globalThis.fetch = async (
  input,
  init,
) => {
  if (typeof window === "undefined") return globalThis.fetch(input, init)

  const currentUrl = window.location.href
  if (!isInternalApiRequest(input, currentUrl)) {
    return globalThis.fetch(input, init)
  }

  const requestInit = isViewAsControlRequest(input, currentUrl)
    ? init
    : await bindInternalApiRequest(input, init)
  const response = await globalThis.fetch(input, requestInit)
  await enforceViewAsBindingResponse(response)

  return handleInternalApiResponse(response, currentUrl, (target) =>
    window.location.replace(target),
  )
}

export function resetBillingRecoveryRedirectState() {
  pendingBillingRedirect = null
}
