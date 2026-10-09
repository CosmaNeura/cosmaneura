import { useSyncExternalStore } from "react"

// Visits this browser session uploaded and is waiting on. The note list has
// no in-progress status, so "processing" is only claimed for visits the user
// just uploaded here. Held in memory only: IDs, no PHI, cleared on reload.
type RecentUploads = {
  processing: ReadonlySet<string>
  justReady: ReadonlySet<string>
}

let state: RecentUploads = { processing: new Set(), justReady: new Set() }
const listeners = new Set<() => void>()

function update(next: RecentUploads) {
  state = next
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function markProcessing(appointmentId: string) {
  update({
    ...state,
    processing: new Set(state.processing).add(appointmentId),
  })
}

export const JUST_READY_HIGHLIGHT_MS = 4_000

export function markReady(appointmentId: string) {
  if (!state.processing.has(appointmentId)) return
  const processing = new Set(state.processing)
  processing.delete(appointmentId)
  update({ processing, justReady: new Set(state.justReady).add(appointmentId) })
  setTimeout(() => {
    const justReady = new Set(state.justReady)
    justReady.delete(appointmentId)
    update({ ...state, justReady })
  }, JUST_READY_HIGHLIGHT_MS)
}

export function stopTracking(appointmentId: string) {
  if (!state.processing.has(appointmentId)) return
  const processing = new Set(state.processing)
  processing.delete(appointmentId)
  update({ ...state, processing })
}

export function useRecentUploads() {
  return useSyncExternalStore(subscribe, () => state)
}

export function resetRecentUploadsForTests() {
  update({ processing: new Set(), justReady: new Set() })
}
