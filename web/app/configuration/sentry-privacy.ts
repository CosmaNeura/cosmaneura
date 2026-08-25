export const REDACTED_AZURE_SAS_URL = "[Filtered Azure Blob SAS URL]"
export const REDACTED_INTAKE_CREDENTIAL = "[Filtered-intake-credential]"
export const REDACTED_VISIT_PREP_CREDENTIAL = "[Filtered Visit Prep credential]"
export const REDACTED_LEGACY_EXAMPLE_NOTE_URL =
  "[Filtered legacy example-note request]"
export const REDACTED_ZOOM_JOIN_URL = "[Filtered Zoom join URL]"
const REDACTED_SAS_VALUE = "[Filtered Azure SAS credential]"
const REDACTED_SENSITIVE_VALUE = "[Filtered sensitive value]"

const HTTP_URL_PATTERN = /https?:\/\/[^\s"'<>]+/giu
const SAS_SIGNATURE_PATTERN = /(?:^|[?&]|&amp;)sig=/iu
const SAS_VERSION_PATTERN = /(?:^|[?&]|&amp;)sv=/iu
const INTAKE_BEARER_PATH_PATTERN =
  /(\/(?:external\/)?chat\/intake\/)[^/?#\s"'<>]+/giu
const INTAKE_TYPEID_PATTERN = /\bintake_[0-9a-hjkmnp-tv-z]{26}\b/giu
const VISIT_PREP_TOKEN_PATTERN = /vp1_[A-Za-z0-9_-]+/gu
const LEGACY_EXAMPLE_NOTE_PATH_PATTERN =
  /\/internal\/v1\/notes\/upload-example\b/iu
const LEGACY_EXAMPLE_NOTE_QUERY_PATTERN =
  /(?:[?&]|&amp;)(?:file_name|note_type)=/iu
const BEARER_CREDENTIAL_PATTERN = /\bbearer\s+[a-z0-9._~+/=-]+/giu
const MOUNT_CONTEXT_TOKEN_PATTERN = /\bmctx_[a-z0-9._~-]+\b/giu
const SENSITIVE_NORMALIZED_KEYS = new Set([
  "accesstoken",
  "accesscode",
  "authorization",
  "accessurl",
  "contexttoken",
  "intakeid",
  "joincode",
  "joinurl",
  "registrationtoken",
  "uploadurl",
])

function isAzureBlobHostname(hostname: string): boolean {
  const normalizedHostname = hostname.toLowerCase()

  return (
    normalizedHostname.endsWith(".blob.core.windows.net") ||
    normalizedHostname.endsWith(".blob.core.usgovcloudapi.net") ||
    normalizedHostname.endsWith(".blob.core.chinacloudapi.cn") ||
    normalizedHostname.endsWith(".blob.core.cloudapi.de")
  )
}

function hasAzureSasCredential(candidate: string): boolean {
  if (!SAS_SIGNATURE_PATTERN.test(candidate)) return false

  try {
    const url = new URL(candidate)
    const parameterNames = new Set(
      [...url.searchParams.keys()].map((key) => key.toLowerCase()),
    )

    // Azure's public blob host plus `sig` is definitive. Requiring the `sv`
    // marker as well supports reviewed custom-domain endpoints without treating
    // an unrelated service's generic `sig` parameter as an Azure credential.
    return isAzureBlobHostname(url.hostname)
      ? parameterNames.has("sig") || SAS_SIGNATURE_PATTERN.test(candidate)
      : (parameterNames.has("sig") && parameterNames.has("sv")) ||
          SAS_VERSION_PATTERN.test(candidate)
  } catch {
    // Sentry span descriptions may prefix the URL with a method or otherwise
    // contain only the query fragment. The paired Azure SAS markers are still
    // specific enough to fail closed for telemetry.
    return SAS_VERSION_PATTERN.test(candidate)
  }
}

/** True when a telemetry string contains a credential-bearing Azure blob URL. */
export function containsAzureBlobSasUrl(value: string): boolean {
  HTTP_URL_PATTERN.lastIndex = 0
  for (const match of value.matchAll(HTTP_URL_PATTERN)) {
    if (hasAzureSasCredential(match[0])) return true
  }

  return hasAzureSasCredential(value)
}

function redactAzureBlobSasUrls(value: string): string {
  HTTP_URL_PATTERN.lastIndex = 0
  const redacted = value.replace(HTTP_URL_PATTERN, (candidate) =>
    hasAzureSasCredential(candidate) ? REDACTED_AZURE_SAS_URL : candidate,
  )

  if (redacted !== value) return redacted

  return hasAzureSasCredential(value) ? REDACTED_AZURE_SAS_URL : value
}

/** True when a string contains a public intake capability URL or TypeID. */
export function containsIntakeBearerCredential(value: string): boolean {
  INTAKE_BEARER_PATH_PATTERN.lastIndex = 0
  if (INTAKE_BEARER_PATH_PATTERN.test(value)) return true

  INTAKE_TYPEID_PATTERN.lastIndex = 0
  return INTAKE_TYPEID_PATTERN.test(value)
}

/** True when a telemetry string contains a Visit Prep bearer credential. */
export function containsVisitPrepBearerCredential(value: string): boolean {
  VISIT_PREP_TOKEN_PATTERN.lastIndex = 0
  return VISIT_PREP_TOKEN_PATTERN.test(value)
}

function redactVisitPrepBearerCredentials(value: string): string {
  VISIT_PREP_TOKEN_PATTERN.lastIndex = 0
  return value.replace(VISIT_PREP_TOKEN_PATTERN, REDACTED_VISIT_PREP_CREDENTIAL)
}

function redactIntakeBearerCredentials(value: string): string {
  INTAKE_BEARER_PATH_PATTERN.lastIndex = 0
  const pathRedacted = value.replace(
    INTAKE_BEARER_PATH_PATTERN,
    (_candidate, prefix: string) => `${prefix}${REDACTED_INTAKE_CREDENTIAL}`,
  )
  INTAKE_TYPEID_PATTERN.lastIndex = 0
  return pathRedacted.replace(INTAKE_TYPEID_PATTERN, REDACTED_INTAKE_CREDENTIAL)
}

function isZoomJoinUrl(candidate: string): boolean {
  try {
    const url = new URL(candidate)
    const hostname = url.hostname.toLowerCase()
    if (hostname !== "zoom.us" && !hostname.endsWith(".zoom.us")) {
      return false
    }
    return /^\/(?:j|s)\/[^/?#]+(?:\/|$)/u.test(url.pathname)
  } catch {
    return false
  }
}

/** True when a telemetry string contains a credential-bearing Zoom join URL. */
export function containsZoomJoinUrl(value: string): boolean {
  HTTP_URL_PATTERN.lastIndex = 0
  for (const match of value.matchAll(HTTP_URL_PATTERN)) {
    if (isZoomJoinUrl(match[0])) return true
  }
  return isZoomJoinUrl(value)
}

function redactZoomJoinUrls(value: string): string {
  HTTP_URL_PATTERN.lastIndex = 0
  const redacted = value.replace(HTTP_URL_PATTERN, (candidate) =>
    isZoomJoinUrl(candidate) ? REDACTED_ZOOM_JOIN_URL : candidate,
  )
  if (redacted !== value) return redacted
  return isZoomJoinUrl(value) ? REDACTED_ZOOM_JOIN_URL : value
}

/** True for the retired example-note URL whose query can contain a filename. */
export function containsLegacyExampleNoteQuery(value: string): boolean {
  return (
    LEGACY_EXAMPLE_NOTE_PATH_PATTERN.test(value) &&
    LEGACY_EXAMPLE_NOTE_QUERY_PATTERN.test(value)
  )
}

function redactLegacyExampleNoteQuery(value: string): string {
  return containsLegacyExampleNoteQuery(value)
    ? REDACTED_LEGACY_EXAMPLE_NOTE_URL
    : value
}

function redactSensitiveTelemetryString(value: string): string {
  const urlRedacted = redactLegacyExampleNoteQuery(
    redactZoomJoinUrls(
      redactVisitPrepBearerCredentials(
        redactIntakeBearerCredentials(redactAzureBlobSasUrls(value)),
      ),
    ),
  )
  BEARER_CREDENTIAL_PATTERN.lastIndex = 0
  const bearerRedacted = urlRedacted.replace(
    BEARER_CREDENTIAL_PATTERN,
    `Bearer ${REDACTED_SENSITIVE_VALUE}`,
  )
  MOUNT_CONTEXT_TOKEN_PATTERN.lastIndex = 0
  return bearerRedacted.replace(
    MOUNT_CONTEXT_TOKEN_PATTERN,
    REDACTED_SENSITIVE_VALUE,
  )
}

/**
 * Redact browser-held bearer credentials throughout a Sentry payload.
 *
 * Sentry can place fetch URLs in several shapes (breadcrumb data, span
 * descriptions/attributes, request data, exception messages, or replay custom
 * events), so a recursive final guard is safer than relying on one SDK field.
 */
export function scrubSensitiveSentryPayload<T>(payload: T): T {
  const visited = new WeakSet<object>()

  function scrub(value: unknown): unknown {
    if (typeof value === "string") return redactSensitiveTelemetryString(value)
    if (typeof value !== "object" || value === null) return value
    if (visited.has(value)) return value

    visited.add(value)

    if (Array.isArray(value)) {
      for (let index = 0; index < value.length; index += 1) {
        value[index] = scrub(value[index])
      }
      return value
    }

    const record = value as Record<string, unknown>
    for (const [key, child] of Object.entries(record)) {
      // SDK processors can normalize credentials into maps rather than retain
      // their source URL or header. Match only reviewed exact field names so
      // useful fields such as `authorization_basis` remain intact.
      const normalizedKey = key.toLowerCase().replaceAll(/[_-]/g, "")
      record[key] =
        key.toLowerCase() === "sig"
          ? REDACTED_SAS_VALUE
          : SENSITIVE_NORMALIZED_KEYS.has(normalizedKey)
            ? normalizedKey === "intakeid"
              ? REDACTED_INTAKE_CREDENTIAL
              : REDACTED_SENSITIVE_VALUE
            : scrub(child)
    }

    return value
  }

  return scrub(payload) as T
}

/** Prevent creation of an upload span before Sentry stores the credential URL. */
export function shouldCreateSentryRequestSpan(url: string): boolean {
  return (
    !containsAzureBlobSasUrl(url) &&
    !containsIntakeBearerCredential(url) &&
    !containsVisitPrepBearerCredential(url) &&
    !containsZoomJoinUrl(url) &&
    !containsLegacyExampleNoteQuery(url)
  )
}

export function scrubSentryBreadcrumb<T>(breadcrumb: T): T {
  return scrubSensitiveSentryPayload(breadcrumb)
}

export function scrubSentryErrorEvent<T>(event: T): T {
  return scrubSensitiveSentryPayload(event)
}

export function scrubSentryTransaction<T>(event: T): T {
  return scrubSensitiveSentryPayload(event)
}

export function scrubSentrySpan<T>(span: T): T {
  return scrubSensitiveSentryPayload(span)
}
