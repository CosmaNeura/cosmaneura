import { readFile } from "node:fs/promises"

const contractUrl = new URL(
  "../../contracts/desktop-v1.openapi.json",
  import.meta.url,
)
const contract = JSON.parse(await readFile(contractUrl, "utf8"))

const expectedPaths = new Set([
  "/desktop/v1/oauth/token",
  "/desktop/v1/capture-sessions/{capture_session_id}/segments/start-authorizations",
  "/desktop/v1/capture-sessions/{capture_session_id}/heartbeats",
  "/desktop/v1/capture-sessions/{capture_session_id}/stop-requests",
])
const expectedOperations = new Map([
  ["/desktop/v1/oauth/token", "exchange_desktop_capture_code"],
  [
    "/desktop/v1/capture-sessions/{capture_session_id}/segments/start-authorizations",
    "authorize_desktop_capture_segment_start",
  ],
  [
    "/desktop/v1/capture-sessions/{capture_session_id}/heartbeats",
    "record_desktop_capture_heartbeat",
  ],
  [
    "/desktop/v1/capture-sessions/{capture_session_id}/stop-requests",
    "request_desktop_capture_stop",
  ],
])
const actualPaths = new Set(Object.keys(contract.paths ?? {}))

if (contract.openapi !== "3.1.0") {
  throw new Error("desktop contract must remain OpenAPI 3.1.0")
}
if (
  expectedPaths.size !== actualPaths.size ||
  [...expectedPaths].some((path) => !actualPaths.has(path))
) {
  throw new Error("desktop contract path boundary changed; review required")
}
if ([...actualPaths].some((path) => !path.startsWith("/desktop/v1"))) {
  throw new Error("browser or internal routes leaked into the desktop contract")
}

for (const [path, operationId] of expectedOperations) {
  const operation = contract.paths[path]?.post
  if (operation?.operationId !== operationId) {
    throw new Error(`desktop operation changed: ${path}`)
  }
  const isTokenExchange = path.endsWith("/oauth/token")
  const bearerProtected =
    operation.security?.length === 1 &&
    Object.hasOwn(operation.security[0], "desktopBearer")
  if (isTokenExchange === bearerProtected) {
    throw new Error(`desktop bearer boundary changed: ${path}`)
  }
  if (!isTokenExchange) {
    const requiredParameters = new Set(
      (operation.parameters ?? [])
        .filter((parameter) => parameter.required)
        .map((parameter) => parameter.name),
    )
    for (const required of ["capture_session_id", "Idempotency-Key"]) {
      if (!requiredParameters.has(required)) {
        throw new Error(`required lifecycle parameter changed: ${required}`)
      }
    }
  }
}

const bearer = contract.components?.securitySchemes?.desktopBearer
if (bearer?.type !== "http" || bearer?.scheme !== "bearer") {
  throw new Error("desktop bearer security scheme changed")
}

const expectedRequiredFields = {
  AuthorizationCodeExchange: [
    "code",
    "code_verifier",
    "grant_type",
    "redirect_uri",
  ],
  BackendCaptureGrantResponse: [
    "actorId",
    "captureSessionId",
    "consentRecordId",
    "consentStatus",
    "sessionStatus",
    "source",
    "sourceLeaseId",
  ],
  CaptureCapabilityResponse: ["capabilityId", "expiresAt", "secret"],
  CaptureLifecycleDecisionResponse: [
    "decision",
    "schemaVersion",
    "sessionStatus",
  ],
  CaptureLifecycleRequest: ["capabilityId"],
  StartAuthorityDecisionResponse: [
    "actorId",
    "captureCapability",
    "captureSessionId",
    "consentRecordId",
    "consentStatus",
    "decision",
    "expiresAt",
    "issuedAt",
    "schemaVersion",
    "sessionStatus",
    "source",
    "sourceLeaseId",
  ],
}

for (const [schemaName, expected] of Object.entries(expectedRequiredFields)) {
  const actual = [...(contract.components?.schemas?.[schemaName]?.required ?? [])].sort()
  if (JSON.stringify(actual) !== JSON.stringify([...expected].sort())) {
    throw new Error(`desktop response shape changed: ${schemaName}`)
  }
}

const documentText = JSON.stringify(contract)
for (const forbidden of [
  "patientName",
  "patient_name",
  "tenantId",
  "tenant_id",
  "providerMetadata",
  "provider_metadata",
]) {
  if (documentText.includes(forbidden)) {
    throw new Error(`desktop contract exposes forbidden authority field: ${forbidden}`)
  }
}
