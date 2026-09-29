import type { SovereignHistory } from "./SovereignHistory"

export function emptyHistory(): SovereignHistory {
  return {
    schema_version: "1.0.0",
    history: {
      thread_ref: "thread_synthetic",
      messages: [],
      next_cursor: null,
    },
    turns: [],
  }
}

export function completedHistory(
  content = "Synthetic recorded answer",
): SovereignHistory {
  const result = emptyHistory()
  const identity = {
    turn_ref: "conversation_turn_synthetic",
    client_turn_id: "10000000-0000-4000-8000-000000000001",
    recorded_at: "2026-09-28T12:00:00Z",
  }
  result.turns.push({ ...identity, state: "completed", outcome: "completed" })
  result.history.messages.push(
    {
      ...identity,
      role: "user",
      content: "Synthetic recorded question",
      presentation: null,
    },
    {
      ...identity,
      role: "assistant",
      content,
      presentation: { mode: "standard", suppress: [], human_contact: null },
    },
  )
  return result
}
