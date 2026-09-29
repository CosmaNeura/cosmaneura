/** The server's human-readable `detail`, when a failed response carries one. */
export function errorDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const { detail } = error as { detail: unknown }
    if (typeof detail === "string") return detail
  }
  return null
}
