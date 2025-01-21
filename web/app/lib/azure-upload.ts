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
    error: !azureUploadResponse.ok,
  }
}
