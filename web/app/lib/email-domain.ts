/** Network domain for MyCatholicDoctor practices. */
export const MCD_EMAIL_DOMAIN = "mycatholicdoctor.com"

export function extractEmailDomain(
  email: string | null | undefined,
): string | null {
  if (!email) return null
  const at = email.lastIndexOf("@")
  if (at <= 0 || at === email.length - 1) return null
  return email
    .slice(at + 1)
    .trim()
    .toLowerCase()
}

/**
 * Exact domain or a subdomain of it.
 * `np@nashua.mycatholicdoctor.com` matches `mycatholicdoctor.com`.
 * `attacker@evil-mycatholicdoctor.com` does not.
 */
export function emailMatchesDomain(
  email: string | null | undefined,
  requiredDomain: string | null | undefined,
): boolean {
  const got = extractEmailDomain(email)
  const required = requiredDomain?.trim().toLowerCase() ?? ""
  if (!got || !required) return false
  return got === required || got.endsWith(`.${required}`)
}

export function isMcdEmail(email: string | null | undefined): boolean {
  return emailMatchesDomain(email, MCD_EMAIL_DOMAIN)
}
