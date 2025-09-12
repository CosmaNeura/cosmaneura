import { getUserId } from "~/configuration/clerk"

import { invariant } from "@epic-web/invariant"

// generic upload function to be used by an onSubmit handler
// TODO should handle errors better...
export async function uploadFileToAzure(file: File, sasSignature: string) {
  invariant(sasSignature, "sasSignature should always exist")

  console.log("template upload starting azure upload", {
    fileName: file.name,
    fileSize: file.size,
    fileType: file.type,
  })

  const unixTimestamp = Date.now()
  const userId = await getUserId()
  const stampedFileName = [userId, unixTimestamp, file.name].join("_")
  
  console.log("template upload generated stamped filename", {
    stampedFileName,
    userId,
    unixTimestamp,
    originalFileName: file.name,
  })
  
  const url = new URL(sasSignature)
  url.pathname = `${url.pathname}/${stampedFileName}`
  const finalUrl = url.toString()

  console.log("template upload uploading to azure", {
    finalUrl: finalUrl.replace(/sig=[^&]*/, 'sig=***'), // Hide the signature
    stampedFileName,
  })

  const azureUploadResponse = await fetch(finalUrl, {
    method: "PUT",
    headers: {
      "x-ms-blob-type": "BlockBlob",
      "Content-Type": file.type,
    },
    body: file,
  })

  console.log("template upload azure upload response", {
    status: azureUploadResponse.status,
    statusText: azureUploadResponse.statusText,
    ok: azureUploadResponse.ok,
    stampedFileName,
  })

  return {
    stampedFileName,
    azureUploadResponse,
    // TODO add more extensive error information so we can capture it via sentry
    error: !azureUploadResponse.ok,
  }
}
