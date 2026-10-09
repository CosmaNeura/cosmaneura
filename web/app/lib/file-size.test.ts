import { describe, expect, it } from "vitest"

import { VISIT_UPLOAD_MAX_BYTES } from "./azure-upload"
import { formatFileSize } from "./file-size"

describe("formatFileSize", () => {
  it.each([
    [0, "0 bytes"],
    [512, "512 bytes"],
    [1536, "1.5 KB"],
    [12.34 * 1024 * 1024, "12.3 MB"],
    [VISIT_UPLOAD_MAX_BYTES, "2 GB"],
  ])("formats %d bytes as %s", (bytes, label) => {
    expect(formatFileSize(bytes)).toBe(label)
  })

  it("never overstates a limit", () => {
    expect(formatFileSize(VISIT_UPLOAD_MAX_BYTES, "down")).toBe("1.9 GB")
  })
})
