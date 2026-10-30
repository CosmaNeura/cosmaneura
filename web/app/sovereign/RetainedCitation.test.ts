import { describe, expect, it } from "vitest"

import { citationCoordinates, parseRetainedCitation } from "./RetainedCitation"
import { retainedFixture } from "./RetainedCitation.fixture"
import {
  citationFixture,
  finalReference,
} from "./SovereignCitationDisplay.fixture"

describe("exact retained citation bytes", () => {
  it("checks the original ordered Unicode sections and accepted ordinal", async () => {
    const value = retainedFixture()
    expect(await parseRetainedCitation(value, `${finalReference}.1`)).toEqual(
      value,
    )
    expect(citationCoordinates(`${finalReference}.1`)).toEqual({
      final_ref: finalReference,
      ordinal: 1,
    })
  })
  it.each([
    "correction",
    "order",
    "reference",
    "duplicate",
    "missing",
    "hash",
    "extra",
    "oversize",
  ])("refuses %s rather than current-note substitution", async (kind) => {
    const value = retainedFixture()
    if (kind === "correction")
      value.sections[0].text = "New corrected current source."
    if (kind === "order") value.sections.reverse()
    if (kind === "reference")
      value.citation.citation_ref = `${finalReference}.2`
    if (kind === "duplicate") value.sections.push(value.sections[0])
    if (kind === "missing") value.sections = [value.sections[1]]
    if (kind === "hash") value.content_sha256 = "0".repeat(64)
    if (kind === "extra")
      Object.assign(value, { current_note_url: "https://other.example/" })
    if (kind === "oversize") value.sections[0].text = "x".repeat(18000)
    await expect(
      parseRetainedCitation(value, `${finalReference}.1`),
    ).rejects.toThrow("Reviewed source is unavailable")
  })
})
