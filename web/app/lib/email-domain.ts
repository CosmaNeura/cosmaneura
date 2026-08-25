/** Network domain for MyCatholicDoctor practices. */
export const MCD_EMAIL_DOMAIN = "mycatholicdoctor.com"

// Keep this explicit list aligned with app/utils/email_domain.py. A custom
// domain hosted by one of these providers is still a practice-owned domain.
export const PUBLIC_EMAIL_DOMAINS = new Set([
  "aol.com",
  "fastmail.com",
  "gmail.com",
  "gmx.com",
  "gmx.us",
  "googlemail.com",
  "hey.com",
  "hotmail.com",
  "icloud.com",
  "live.com",
  "mac.com",
  "mail.com",
  "me.com",
  "msn.com",
  "outlook.com",
  "pm.me",
  "proton.me",
  "protonmail.com",
  "yahoo.com",
  "ymail.com",
  "zoho.com",
])

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

export function isPublicEmailDomain(
  domain: string | null | undefined,
): boolean {
  const normalized = domain?.trim().toLowerCase().replace(/^@/, "") ?? ""
  return PUBLIC_EMAIL_DOMAINS.has(normalized)
}
