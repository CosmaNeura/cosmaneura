const ZOOM_BETA_EMAILS = new Set([
  "dev@cosmaneura.com",
  "zoom-reviewer@cosmaneura.com",
  "ortegaoscar14@gmail.com",
])

export function isZoomEnabled(email: string | null | undefined): boolean {
  return !!email && ZOOM_BETA_EMAILS.has(email.toLowerCase())
}
