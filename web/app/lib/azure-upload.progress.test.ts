import { afterEach, describe, expect, it, vi } from "vitest"

import { uploadToExactAzureUrlWithProgress } from "./azure-upload"

vi.mock("~/configuration/clerk", () => ({ getUserId: vi.fn() }))

class FakeXhr {
  static last: FakeXhr
  status = 0
  headers: Record<string, string> = {}
  method = ""
  url = ""
  body: unknown
  upload: { onprogress?: (event: ProgressEvent) => void } = {}
  onload?: () => void
  onerror?: () => void
  onabort?: () => void
  ontimeout?: () => void
  constructor() {
    FakeXhr.last = this
  }
  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }
  send(body: unknown) {
    this.body = body
  }
  progress(loaded: number, total: number) {
    this.upload.onprogress?.({
      lengthComputable: true,
      loaded,
      total,
    } as ProgressEvent)
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

const URL_ = "https://uploads.example/container/visit_opaque.wav?sig=secret"
const file = new File(["audio"], "patient-name.wav", { type: "audio/wav" })

describe("upload with progress", () => {
  it("sends the same create-only request and reports progress", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr)
    const onProgress = vi.fn()

    const pending = uploadToExactAzureUrlWithProgress(
      file,
      URL_,
      "audio/wav",
      onProgress,
    )
    const xhr = FakeXhr.last
    expect(xhr.method).toBe("PUT")
    expect(xhr.url).toBe(URL_)
    expect(xhr.headers).toEqual({
      "x-ms-blob-type": "BlockBlob",
      "Content-Type": "audio/wav",
      "If-None-Match": "*",
    })
    expect(xhr.body).toBe(file)

    xhr.progress(25, 100)
    xhr.progress(75, 100)
    xhr.status = 201
    xhr.onload?.()

    await expect(pending).resolves.toEqual({ error: false })
    expect(onProgress.mock.calls.map(([value]) => value)).toEqual([
      0.25, 0.75, 1,
    ])
  })

  it("only reports completion once the blob service accepts the upload", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr)
    const onProgress = vi.fn()
    const pending = uploadToExactAzureUrlWithProgress(
      file,
      URL_,
      "audio/wav",
      onProgress,
    )
    FakeXhr.last.progress(100, 100)
    FakeXhr.last.upload.onprogress?.({
      lengthComputable: false,
    } as ProgressEvent)
    FakeXhr.last.status = 412
    FakeXhr.last.onload?.()

    await expect(pending).resolves.toEqual({ error: true })
    expect(onProgress.mock.calls.map(([value]) => value)).toEqual([0.999, 0.01])
  })

  it("reports a rejected upload as an error", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr)
    const pending = uploadToExactAzureUrlWithProgress(
      file,
      URL_,
      "audio/wav",
      vi.fn(),
    )
    FakeXhr.last.status = 412
    FakeXhr.last.onload?.()

    await expect(pending).resolves.toEqual({ error: true })
  })

  it("rejects on a network failure, as fetch does", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr)
    const pending = uploadToExactAzureUrlWithProgress(
      file,
      URL_,
      "audio/wav",
      vi.fn(),
    )
    FakeXhr.last.onerror?.()

    await expect(pending).rejects.toThrow()
  })

  it.each(["onabort", "ontimeout"] as const)(
    "rejects when the request ends via %s",
    async (handler) => {
      vi.stubGlobal("XMLHttpRequest", FakeXhr)
      const pending = uploadToExactAzureUrlWithProgress(
        file,
        URL_,
        "audio/wav",
        vi.fn(),
      )
      FakeXhr.last[handler]?.()

      await expect(pending).rejects.toThrow()
    },
  )
})
