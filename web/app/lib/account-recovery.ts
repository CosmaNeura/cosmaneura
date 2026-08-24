const ACCOUNT_RECOVERY_DETAIL =
  "This email is already linked to a previous sign-in."

function errorDetail(error: unknown): string | null {
  if (!error || typeof error !== "object" || !("detail" in error)) return null
  return typeof error.detail === "string" ? error.detail : null
}

/** Recognize only the server's explicit, non-sensitive recovery response. */
export function isAccountRecoveryRequired(
  error: unknown,
  status?: number,
): boolean {
  if (status !== undefined && status !== 409) return false
  return errorDetail(error)?.startsWith(ACCOUNT_RECOVERY_DETAIL) ?? false
}
