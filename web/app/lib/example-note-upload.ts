import {
  generateSignedExampleNoteUploadUrl,
  generateSignedExampleNoteUploadUrlV2,
  uploadExampleNote,
  uploadExampleNoteV2,
} from "~/configuration/client"
import { uploadFileToAzure, uploadPdfToExactAzureUrl } from "~/lib/azure-upload"

export type ExampleNoteUploadFailureStage =
  | "preparation"
  | "upload"
  | "registration"

export type ExampleNoteUploadResult =
  | { ok: true }
  | { ok: false; stage: ExampleNoteUploadFailureStage }

export function exampleNoteUploadErrorCopy(
  stage: ExampleNoteUploadFailureStage,
): { title: string; description?: string } {
  if (stage === "preparation") {
    return { title: "Failed to prepare note upload. Please try again." }
  }
  if (stage === "upload") {
    return { title: "Failed to upload note. Please try again." }
  }
  return {
    title: "Example note was not accepted",
    description: "Please choose a valid PDF under 10MB and try again.",
  }
}

type ExampleNoteUploadGrant = {
  upload_url: string
  registration_token: string
}

function isExampleNoteUploadGrant(
  value: unknown,
): value is ExampleNoteUploadGrant {
  if (typeof value !== "object" || value === null) return false

  const candidate = value as Partial<ExampleNoteUploadGrant>
  return (
    typeof candidate.upload_url === "string" &&
    candidate.upload_url.length > 0 &&
    typeof candidate.registration_token === "string" &&
    candidate.registration_token.length > 0
  )
}

function isMissingV2Endpoint(status: number | undefined): boolean {
  return status === 404 || status === 405
}

async function submitLegacyExampleNote(
  file: File,
  noteType: string,
): Promise<ExampleNoteUploadResult> {
  let stage: ExampleNoteUploadFailureStage = "preparation"

  try {
    const { data: sasSignature, error: grantError } =
      await generateSignedExampleNoteUploadUrl()
    if (
      grantError ||
      typeof sasSignature !== "string" ||
      sasSignature.length === 0
    ) {
      return { ok: false, stage }
    }

    stage = "upload"
    const { stampedFileName, error: uploadError } = await uploadFileToAzure(
      file,
      sasSignature,
    )
    if (uploadError) return { ok: false, stage }

    stage = "registration"
    const { error: registrationError } = await uploadExampleNote({
      query: {
        file_name: stampedFileName,
        note_type: noteType,
      },
    })
    if (registrationError) return { ok: false, stage }

    return { ok: true }
  } catch {
    return { ok: false, stage }
  }
}

/**
 * Complete the example-note upload handshake without exposing blob naming to
 * the browser. The server-issued registration token binds the uploaded blob
 * to the authenticated provider.
 */
export async function submitExampleNote(
  file: File,
  noteType: string,
): Promise<ExampleNoteUploadResult> {
  let stage: ExampleNoteUploadFailureStage = "preparation"

  try {
    const {
      data: grant,
      error: grantError,
      response,
    } = await generateSignedExampleNoteUploadUrlV2()
    if (grantError || !isExampleNoteUploadGrant(grant)) {
      // A newly cached browser bundle can briefly reach the previous API
      // revision during a rolling deployment. Fall back only when that API
      // explicitly lacks v2; never downgrade around v2 validation or outages.
      if (isMissingV2Endpoint(response?.status)) {
        return submitLegacyExampleNote(file, noteType)
      }
      return { ok: false, stage }
    }

    stage = "upload"
    const { error: uploadError } = await uploadPdfToExactAzureUrl(
      file,
      grant.upload_url,
    )
    if (uploadError) return { ok: false, stage }

    stage = "registration"
    const { error: registrationError } = await uploadExampleNoteV2({
      body: {
        registration_token: grant.registration_token,
        note_type: noteType,
      },
    })
    if (registrationError) return { ok: false, stage }

    return { ok: true }
  } catch {
    return { ok: false, stage }
  }
}
