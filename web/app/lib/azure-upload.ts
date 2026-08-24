import { getUserId } from "~/configuration/clerk"

import { invariant } from "@epic-web/invariant"

export const VISIT_UPLOAD_MAX_BYTES = 2_000 * 1024 * 1024

// generic upload function to be used by an onSubmit handler
// TODO should handle errors better...
export async function uploadFileToAzure(file: File, sasSignature: string) {
  invariant(sasSignature, "sasSignature should always exist")

  const unixTimestamp = Date.now()
  const userId = await getUserId()
  const extensionIndex = file.name.lastIndexOf(".")
  const extension =
    extensionIndex > 0 ? file.name.slice(extensionIndex).toLowerCase() : ""
  // The legacy endpoint requires an owner/timestamp prefix, but it does not
  // need the clinician's original filename (which can contain patient data).
  const stampedFileName = [
    userId,
    unixTimestamp,
    `${crypto.randomUUID()}${extension}`,
  ].join("_")
  const url = new URL(sasSignature)
  url.pathname = `${url.pathname}/${stampedFileName}`
  const finalUrl = url.toString()

  const azureUploadResponse = await fetch(finalUrl, {
    method: "PUT",
    headers: {
      "x-ms-blob-type": "BlockBlob",
      "Content-Type": file.type,
    },
    body: file,
  })

  return {
    stampedFileName,
    azureUploadResponse,
    // TODO add more extensive error information so we can capture it via sentry
    error: !azureUploadResponse.ok,
  }
}

/** Upload a file to the exact create-only blob URL issued by the server. */
export async function uploadToExactAzureUrl(
  file: File,
  uploadUrl: string,
  contentType: string,
) {
  invariant(uploadUrl, "uploadUrl should always exist")
  invariant(contentType, "contentType should always exist")

  const azureUploadResponse = await fetch(uploadUrl, {
    method: "PUT",
    headers: {
      "x-ms-blob-type": "BlockBlob",
      "Content-Type": contentType,
      "If-None-Match": "*",
    },
    body: file,
  })

  return {
    azureUploadResponse,
    error: !azureUploadResponse.ok,
  }
}

/** Upload an example-note PDF to the exact blob URL issued by the server. */
export async function uploadPdfToExactAzureUrl(file: File, uploadUrl: string) {
  return uploadToExactAzureUrl(file, uploadUrl, "application/pdf")
}
