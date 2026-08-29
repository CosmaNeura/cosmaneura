import { describe, expect, it } from "vitest"

import { BAA_DOC_ID, BAA_PDF_URL, BAA_VERSION, baaCheckboxLabel } from "./baa"

describe("current BAA identity", () => {
  it("binds onboarding copy and download to v2.3", () => {
    expect(BAA_DOC_ID).toBe("CN-BAA-CUST-CW")
    expect(BAA_VERSION).toBe("2.3")
    expect(BAA_PDF_URL).toBe("https://cosmaneura.com/baa-v2-3.pdf")
    expect(baaCheckboxLabel("Example Practice")).toContain(
      "CN-BAA-CUST-CW v2.3",
    )
  })
})
