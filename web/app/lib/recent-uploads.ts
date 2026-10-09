import { useSyncExternalStore } from "react"

// Visits this browser session uploaded and is waiting on. The note list has
// no in-progress status, so "processing" is only claimed for visits the user
// just uploaded here, and only for a bounded time. Held in memory only: IDs
// and start times, no PHI, cleared on reload.
export const NOTE_PROCESSING_TIMEOUT_MS = 15 * 60_000
export const JUST_READY_HIGHLIGHT_MS = 4_000

type RecentUploads = {
  startedAt: ReadonlyMap<string, number>
  justReady: ReadonlySet<string>
}

let state: RecentUploads = { startedAt: new Map(), justReady: new Set() }
const listeners = new Set<() => void>()
const highlightTimers = new Set<ReturnType<typeof setTimeout>>()

function update(next: RecentUploads) {
  state = next
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function isProcessing(
  uploads: RecentUploads,
  appointmentId: string,
  now: number = Date.now(),
) {
  const startedAt = uploads.startedAt.get(appointmentId)
  return startedAt !== undefined && now - startedAt < NOTE_PROCESSING_TIMEOUT_MS
}

export function markProcessing(appointmentId: string, now = Date.now()) {
  update({
    ...state,
    startedAt: new Map(state.startedAt).set(appointmentId, now),
  })
}

function withoutStart(appointmentId: string) {
  const startedAt = new Map(state.startedAt)
  startedAt.delete(appointmentId)
  return startedAt
}

/** Returns whether the visit was being tracked, so callers announce once. */
export function markReady(appointmentId: string) {
  if (!state.startedAt.has(appointmentId)) return false
  update({
    startedAt: withoutStart(appointmentId),
    justReady: new Set(state.justReady).add(appointmentId),
  })
  const timer = setTimeout(() => {
    highlightTimers.delete(timer)
    const justReady = new Set(state.justReady)
    justReady.delete(appointmentId)
    update({ ...state, justReady })
  }, JUST_READY_HIGHLIGHT_MS)
  highlightTimers.add(timer)
  return true
}

export function stopTracking(appointmentId: string) {
  if (!state.startedAt.has(appointmentId)) return
  update({ ...state, startedAt: withoutStart(appointmentId) })
}

export function useRecentUploads() {
  return useSyncExternalStore(subscribe, () => state)
}

export function resetRecentUploadsForTests() {
  highlightTimers.forEach(clearTimeout)
  highlightTimers.clear()
  update({ startedAt: new Map(), justReady: new Set() })
}
