import { createHash } from "node:crypto"

import { citationFixture } from "./SovereignCitationDisplay.fixture"

export function retainedFixture() {
  const sections = [
    {
      title: "Medications",
      text: "Original reviewed synthetic version. Literal <script> text. Ω",
    },
    { title: "Labs", text: "Original synthetic result." },
  ]
  return {
    schema_version: "1.0.0",
    citation: citationFixture().history.messages[1].reviewed_citations![0],
    sections,
    content_sha256: createHash("sha256")
      .update(JSON.stringify(sections.map((item) => [item.title, item.text])))
      .digest("hex"),
  }
}
