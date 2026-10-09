import { webcrypto } from "node:crypto"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  cabProviderExport,
  changeCabCodes,
  editableCabHeader,
  readCabResponse,
} from "./cab-provider-header"
import {
  expected,
  header,
  op,
  reply,
  source,
} from "./cab-provider-header.test-fixtures"

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto)
})

describe("Core provider header response", () => {
  it("accepts exact projected-note evidence and preserves original source", async () => {
    const response = reply("read", header())
    const before = JSON.stringify(response)
    const read = await readCabResponse(response, "read", op, expected)
    expect(read.view?.header.suggestions[0].code).toBe("Z31.69")
    expect(JSON.stringify(response)).toBe(before)
  })
  it("rejects mismatched source, operation and evidence rather than displaying a replacement", async () => {
    for (const mutate of [
      (r: ReturnType<typeof reply>) => {
        r.sourceOperationId = "77777777-7777-4777-8777-777777777777"
      },
      (r: ReturnType<typeof reply>) => {
        r.source.noteText += " Changed"
      },
      (r: ReturnType<typeof reply>) => {
        if ("view" in r.result)
          r.result.view!.header.evidence[0].text = "Unrelated quote"
      },
    ]) {
      const response = structuredClone(reply("read", header()))
      mutate(response)
      await expect(
        readCabResponse(response, "read", op, expected),
      ).rejects.toThrow()
    }
  })
  it("exports only the context bound to the saved header source", () => {
    const view = header()
    const exported = cabProviderExport(view, source)
    expect(exported).toContain("Service date: 2026-10-02")
    expect(exported).toContain("Note status: Provider-confirmed completed note")
    const wrong = structuredClone(source)
    wrong.headerSource.encounterVersion += 1
    expect(() => cabProviderExport(view, wrong)).toThrow()
  })
  it("copies current proposals with originals and clears diagnosis confirmation after edits", () => {
    const view = header(),
      original = JSON.stringify(view)
    const draft = editableCabHeader(view)
    draft.diagnosisOrderConfirmed = true
    const changed = changeCabCodes(
      draft,
      draft.codes.map((row) => ({ ...row, code: "Z31.41" })),
    )
    expect(changed.diagnosisOrderConfirmed).toBe(false)
    expect(JSON.stringify(view)).toBe(original)
    expect(cabProviderExport(view)).toContain("ORIGINAL ENGINE SUGGESTIONS")
    expect(cabProviderExport(view)).toContain(source.noteText)
  })
})
