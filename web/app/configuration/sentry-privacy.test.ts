import { describe, expect, it } from "vitest"

import {
  REDACTED_AZURE_SAS_URL,
  REDACTED_INTAKE_CREDENTIAL,
  REDACTED_LEGACY_EXAMPLE_NOTE_URL,
  REDACTED_VISIT_PREP_CREDENTIAL,
  REDACTED_ZOOM_JOIN_URL,
  containsAzureBlobSasUrl,
  containsIntakeBearerCredential,
  containsLegacyExampleNoteQuery,
  containsVisitPrepBearerCredential,
  containsZoomJoinUrl,
  scrubSensitiveSentryPayload,
  scrubSentryBreadcrumb,
  scrubSentryErrorEvent,
  scrubSentrySpan,
  scrubSentryTransaction,
  shouldCreateSentryRequestSpan,
} from "./sentry-privacy"

const azureUploadUrl =
  "https://account.blob.core.windows.net/clinical/random-id.pdf?sv=2026-01-01&se=2026-08-24T12%3A10Z&sp=cw&sig=credential-value"
const intakeId = "intake_01k39j8v3y9s6x4t2q7w5r0mnp"
const intakePageUrl = `https://intake.cosmaneura.test/chat/intake/${intakeId}`
const intakeApiUrl = `https://api.cosmaneura.test/external/chat/intake/${intakeId}/question`
const visitPrepToken = `vp1_${"A".repeat(43)}`
const visitPrepUrl = `https://chat.cosmaneura.test/visit-prep#access_token=${visitPrepToken}`
const zoomJoinUrl =
  "https://example.zoom.us/j/123456789?pwd=private-meeting-password"
const legacyExampleNoteUrl =
  "https://api.cosmaneura.test/internal/v1/notes/upload-example?file_name=patient-name.pdf&note_type=Sensitive%20Label"

describe("Sentry Azure SAS privacy", () => {
  it("identifies Azure blob SAS URLs without suppressing normal requests", () => {
    expect(containsAzureBlobSasUrl(azureUploadUrl)).toBe(true)
    expect(
      containsAzureBlobSasUrl(
        "PUT https://uploads.cosmaneura.test/random-id.wav?sv=1&sig=credential-value",
      ),
    ).toBe(true)
    expect(containsAzureBlobSasUrl("sv=1&sp=cw&sig=credential-value")).toBe(
      true,
    )

    expect(
      containsAzureBlobSasUrl(
        "https://app.cosmaneura.com/internal/v1/settings?sig=display-mode",
      ),
    ).toBe(false)
    expect(
      containsAzureBlobSasUrl(
        "https://account.blob.core.windows.net/public/logo.svg",
      ),
    ).toBe(false)
  })

  it("excludes credential-bearing upload and intake requests from spans", () => {
    expect(shouldCreateSentryRequestSpan(azureUploadUrl)).toBe(false)
    expect(shouldCreateSentryRequestSpan(intakeApiUrl)).toBe(false)
    expect(shouldCreateSentryRequestSpan(zoomJoinUrl)).toBe(false)
    expect(shouldCreateSentryRequestSpan(legacyExampleNoteUrl)).toBe(false)
    expect(
      shouldCreateSentryRequestSpan(
        "https://app.cosmaneura.com/internal/v1/settings",
      ),
    ).toBe(true)
  })

  it("redacts a fetch breadcrumb while retaining useful request metadata", () => {
    const breadcrumb = scrubSentryBreadcrumb({
      category: "fetch",
      data: {
        method: "PUT",
        status_code: 201,
        url: azureUploadUrl,
      },
    })

    expect(breadcrumb).toEqual({
      category: "fetch",
      data: {
        method: "PUT",
        status_code: 201,
        url: REDACTED_AZURE_SAS_URL,
      },
    })
  })

  it("redacts child span descriptions and attributes", () => {
    const span = scrubSentrySpan({
      data: {
        "http.request.method": "PUT",
        "url.full": azureUploadUrl,
      },
      description: `PUT ${azureUploadUrl}`,
      op: "http.client",
      span_id: "0000000000000001",
      start_timestamp: 1,
      trace_id: "00000000000000000000000000000001",
    })

    expect(span.description).toBe(`PUT ${REDACTED_AZURE_SAS_URL}`)
    expect(span.data["url.full"]).toBe(REDACTED_AZURE_SAS_URL)
    expect(span.data["http.request.method"]).toBe("PUT")
  })

  it("removes credentials from transaction, error, and replay-shaped payloads", () => {
    const transaction = scrubSentryTransaction({
      type: "transaction",
      transaction: `upload ${azureUploadUrl}`,
      request: { url: azureUploadUrl },
      breadcrumbs: [{ data: { url: azureUploadUrl } }],
      extra: {
        failure: `Azure rejected ${azureUploadUrl}`,
        normalized_query: { sv: "2026-01-01", sig: "credential-value" },
      },
      spans: [],
    })
    const replayPayload = scrubSensitiveSentryPayload({
      data: {
        tag: "performanceSpan",
        payload: {
          op: "resource.fetch",
          description: azureUploadUrl,
        },
      },
    })
    const errorEvent = scrubSentryErrorEvent({
      message: `Upload failed for ${azureUploadUrl}`,
      type: undefined,
    })

    expect(JSON.stringify(transaction)).not.toContain("credential-value")
    expect(JSON.stringify(transaction)).not.toContain("blob.core.windows.net")
    expect(transaction.extra?.normalized_query).toEqual({
      sv: "2026-01-01",
      sig: "[Filtered Azure SAS credential]",
    })
    expect(JSON.stringify(replayPayload)).not.toContain("credential-value")
    expect(replayPayload.data.payload.description).toBe(REDACTED_AZURE_SAS_URL)
    expect(errorEvent.message).toBe(
      `Upload failed for ${REDACTED_AZURE_SAS_URL}`,
    )
  })

  it("redacts public intake credentials from every telemetry shape", () => {
    expect(containsIntakeBearerCredential(intakePageUrl)).toBe(true)
    expect(containsIntakeBearerCredential(intakeApiUrl)).toBe(true)
    expect(containsIntakeBearerCredential(intakeId)).toBe(true)
    expect(containsIntakeBearerCredential("/chat/intake/")).toBe(false)

    const event = scrubSensitiveSentryPayload({
      transaction: intakePageUrl,
      request: { url: intakeApiUrl },
      contexts: { router: { intake_id: intakeId } },
      breadcrumbs: [{ category: "navigation", data: { from: intakePageUrl } }],
      replay: {
        payload: {
          description: `GET ${intakeApiUrl}`,
        },
      },
    })
    const serialized = JSON.stringify(event)

    expect(serialized).not.toContain(intakeId)
    expect(serialized).toContain(REDACTED_INTAKE_CREDENTIAL)
    expect(event.transaction).toBe(
      `https://intake.cosmaneura.test/chat/intake/${REDACTED_INTAKE_CREDENTIAL}`,
    )
    expect(event.request.url).toBe(
      `https://api.cosmaneura.test/external/chat/intake/${REDACTED_INTAKE_CREDENTIAL}/question`,
    )
    expect(event.contexts.router.intake_id).toBe(REDACTED_INTAKE_CREDENTIAL)
  })

  it("redacts Visit Prep bearer credentials and normalized access URLs", () => {
    expect(containsVisitPrepBearerCredential(visitPrepToken)).toBe(true)
    expect(containsVisitPrepBearerCredential(visitPrepUrl)).toBe(true)
    expect(containsVisitPrepBearerCredential("vp1_too-short")).toBe(false)
    expect(containsVisitPrepBearerCredential(`${visitPrepToken}-extra`)).toBe(
      false,
    )

    const event = scrubSensitiveSentryPayload({
      request: { url: visitPrepUrl },
      response: { access_url: visitPrepUrl },
      message: `Visit Prep link ${visitPrepToken}`,
    })
    const serialized = JSON.stringify(event)

    expect(serialized).not.toContain(visitPrepToken)
    expect(event.request.url).toBe(
      `https://chat.cosmaneura.test/visit-prep#access_token=${REDACTED_VISIT_PREP_CREDENTIAL}`,
    )
    expect(event.response.access_url).toBe("[Filtered sensitive value]")
    expect(shouldCreateSentryRequestSpan(visitPrepUrl)).toBe(false)
    expect(
      scrubSentryBreadcrumb({ data: { url: visitPrepUrl } }).data.url,
    ).not.toContain(visitPrepToken)
    expect(
      scrubSentrySpan({ description: visitPrepUrl }).description,
    ).not.toContain(visitPrepToken)
    expect(
      scrubSentryErrorEvent({ message: visitPrepUrl }).message,
    ).not.toContain(visitPrepToken)
    expect(
      scrubSentryTransaction({ transaction: visitPrepUrl }).transaction,
    ).not.toContain(visitPrepToken)
  })

  it("redacts Zoom join credentials without hiding ordinary Zoom links", () => {
    expect(containsZoomJoinUrl(zoomJoinUrl)).toBe(true)
    expect(containsZoomJoinUrl(`GET ${zoomJoinUrl}`)).toBe(true)
    expect(containsZoomJoinUrl("https://zoom.us/oauth/authorize")).toBe(false)
    expect(containsZoomJoinUrl("https://support.zoom.us/docs")).toBe(false)

    const event = scrubSensitiveSentryPayload({
      breadcrumb: { data: { href: zoomJoinUrl } },
      description: `GET ${zoomJoinUrl}`,
    })
    expect(JSON.stringify(event)).not.toContain("private-meeting-password")
    expect(event.breadcrumb.data.href).toBe(REDACTED_ZOOM_JOIN_URL)
    expect(event.description).toBe(`GET ${REDACTED_ZOOM_JOIN_URL}`)
  })

  it("redacts the legacy example-note query and normalized secret fields", () => {
    expect(containsLegacyExampleNoteQuery(legacyExampleNoteUrl)).toBe(true)
    expect(
      containsLegacyExampleNoteQuery(
        "/internal/v1/notes/upload-example?status=ready",
      ),
    ).toBe(false)

    const event = scrubSensitiveSentryPayload({
      request: { url: legacyExampleNoteUrl },
      extra: {
        registration_token: "registration-secret",
        contextToken: "mctx_context-secret",
        authorization: "Bearer authorization-secret",
        join_code: "JOIN-SECRET",
        access_code: "ACCESS-SECRET",
        upload_url: azureUploadUrl,
        join_url: zoomJoinUrl,
        authorization_basis: "organization_roster",
        method: "POST",
        status: 200,
      },
      message: "Request used Bearer header-secret and mctx_inline-secret-value",
    })
    const serialized = JSON.stringify(event)

    for (const secret of [
      "patient-name.pdf",
      "Sensitive%20Label",
      "registration-secret",
      "context-secret",
      "authorization-secret",
      "JOIN-SECRET",
      "ACCESS-SECRET",
      "credential-value",
      "private-meeting-password",
      "header-secret",
      "inline-secret-value",
    ]) {
      expect(serialized).not.toContain(secret)
    }
    expect(event.request.url).toBe(REDACTED_LEGACY_EXAMPLE_NOTE_URL)
    expect(event.extra.authorization_basis).toBe("organization_roster")
    expect(event.extra.method).toBe("POST")
    expect(event.extra.status).toBe(200)
  })
})
