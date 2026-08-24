import {
  generateSignedExampleNoteUploadUrl,
  uploadExampleNote,
} from "~/configuration/client"
import { uploadPdfToExactAzureUrl } from "~/lib/azure-upload"

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
    const { data: grant, error: grantError } =
      await generateSignedExampleNoteUploadUrl()
    if (grantError || !isExampleNoteUploadGrant(grant)) {
      return { ok: false, stage }
    }

    stage = "upload"
    const { error: uploadError } = await uploadPdfToExactAzureUrl(
      file,
      grant.upload_url,
    )
    if (uploadError) return { ok: false, stage }

    stage = "registration"
    const { error: registrationError } = await uploadExampleNote({
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
