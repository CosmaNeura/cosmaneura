import {
  generateSignedVisitRecordingUploadUrl,
  uploadVisitAudioFile,
} from "~/configuration/client"
import {
  type UploadProgressHandler,
  VISIT_UPLOAD_MAX_BYTES,
  uploadToExactAzureUrl,
  uploadToExactAzureUrlWithProgress,
} from "~/lib/azure-upload"

export { VISIT_UPLOAD_MAX_BYTES } from "~/lib/azure-upload"

export type VisitAudioUploadInput = {
  appointmentDate: string
  dateOfBirth: string
  file: File
  patientName: string
  onUploadProgress?: UploadProgressHandler
}

export type VisitAudioUploadFailureStage =
  | "selection"
  | "grant"
  | "azure"
  | "registration"

export type VisitAudioUploadResult =
  | { ok: true }
  | { ok: false; stage: VisitAudioUploadFailureStage }

type UploadGrant = {
  content_type: string
  registration_token: string
  upload_url: string
}

type VisitAudioUploadDependencies = {
  issueGrant: (
    fileName: string,
    fileSize: number,
  ) => Promise<{ data?: UploadGrant; error?: unknown }>
  registerUpload: (body: {
    appointment_date: string
    date_of_birth: string
    patient_name: string
    registration_token: string
  }) => Promise<{ error?: unknown }>
  uploadBlob: (
    file: File,
    uploadUrl: string,
    contentType: string,
    onProgress?: UploadProgressHandler,
  ) => Promise<{ error: boolean }>
}

const defaultDependencies: VisitAudioUploadDependencies = {
  issueGrant: async (fileName, fileSize) => {
    const { data, error } = await generateSignedVisitRecordingUploadUrl({
      body: { file_name: fileName, file_size: fileSize },
    })
    return { data, error }
  },
  registerUpload: async (body) => {
    const { error } = await uploadVisitAudioFile({ body })
    return { error }
  },
  uploadBlob: (file, uploadUrl, contentType, onProgress) =>
    onProgress
      ? uploadToExactAzureUrlWithProgress(
          file,
          uploadUrl,
          contentType,
          onProgress,
        )
      : uploadToExactAzureUrl(file, uploadUrl, contentType),
}

/** Complete all three phases without ever treating a partial upload as success. */
export async function submitVisitAudioUpload(
  input: VisitAudioUploadInput,
  dependencies: VisitAudioUploadDependencies = defaultDependencies,
): Promise<VisitAudioUploadResult> {
  if (input.file.size <= 0 || input.file.size > VISIT_UPLOAD_MAX_BYTES) {
    return { ok: false, stage: "selection" }
  }

  let grant: UploadGrant
  try {
    const response = await dependencies.issueGrant(
      input.file.name,
      input.file.size,
    )
    if (response.error || !response.data) {
      return { ok: false, stage: "grant" }
    }
    grant = response.data
  } catch {
    return { ok: false, stage: "grant" }
  }

  try {
    const uploaded = await dependencies.uploadBlob(
      input.file,
      grant.upload_url,
      grant.content_type,
      input.onUploadProgress,
    )
    if (uploaded.error) return { ok: false, stage: "azure" }
  } catch {
    return { ok: false, stage: "azure" }
  }

  try {
    const registered = await dependencies.registerUpload({
      appointment_date: input.appointmentDate,
      date_of_birth: input.dateOfBirth,
      patient_name: input.patientName,
      registration_token: grant.registration_token,
    })
    if (registered.error) return { ok: false, stage: "registration" }
  } catch {
    return { ok: false, stage: "registration" }
  }

  return { ok: true }
}
