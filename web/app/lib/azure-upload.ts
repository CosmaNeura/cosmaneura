import { getUserId } from "~/configuration/clerk"

import { invariant } from "@epic-web/invariant"

// generic upload function to be used by an onSubmit handler
// TODO should handle errors better...
export async function uploadFileToAzure(file: File, sasSignature: string) {
  invariant(sasSignature, "sasSignature should always exist")

  const unixTimestamp = Date.now()
  const userId = await getUserId()
  const stampedFileName = [userId, unixTimestamp, file.name].join("_")
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

/** Upload an example-note PDF to the exact blob URL issued by the server. */
export async function uploadPdfToExactAzureUrl(file: File, uploadUrl: string) {
  invariant(uploadUrl, "uploadUrl should always exist")

  const azureUploadResponse = await fetch(uploadUrl, {
    method: "PUT",
    headers: {
      "x-ms-blob-type": "BlockBlob",
      "Content-Type": "application/pdf",
      "If-None-Match": "*",
    },
    body: file,
  })

  return {
    azureUploadResponse,
    error: !azureUploadResponse.ok,
  }
}
