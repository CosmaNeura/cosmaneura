import { completedHistory } from "./SovereignHistory.fixture"

export const finalReference =
  "conversation_final_phase_01m00000000000000000000000"
export const excerptAnswer =
  "Selected reviewed-note excerpts for your question:\n\n" +
  "[1] 2026-10-01 — Medications\n\n> Synthetic identical recorded text.\n\n" +
  "[2] 2026-10-01 — Medications\n\n> Synthetic identical recorded text.\n\n" +
  "2 selected reviewed-note versions; not the entire chart.\n\nSynthetic provider disclaimer."

export function citationFixture(
  version: "1.1.0" | "1.2.0" = "1.2.0",
  answer = excerptAnswer,
) {
  const legacy = completedHistory(answer)
  const map = [1, 2].map((ordinal) => ({
    citation_ref: `${finalReference}.${ordinal}`,
    ordinal,
    section: "Medications",
    source_at: ordinal === 1 ? "2026-10-01T09:00:00Z" : "2026-10-01T17:00:00Z",
    reviewed_at:
      ordinal === 1 ? "2026-10-02T10:00:00Z" : "2026-10-02T11:00:00Z",
  }))
  return {
    ...legacy,
    schema_version: version,
    history: {
      ...legacy.history,
      messages: legacy.history.messages.map((message) => ({
        ...message,
        reviewed_citations:
          message.role === "user" ? (version === "1.2.0" ? null : []) : map,
        ...(version === "1.2.0"
          ? { display_state: message.role === "user" ? null : "superseded" }
          : {}),
      })),
    },
  }
}
