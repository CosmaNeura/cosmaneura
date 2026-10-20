import { afterEach, describe, expect, it, vi } from "vitest"

import {
  JUST_READY_HIGHLIGHT_MS,
  NOTE_PROCESSING_TIMEOUT_MS,
  isProcessing,
  markProcessing,
  markReady,
  resetRecentUploadsForTests,
  stopTracking,
  useRecentUploads,
} from "./recent-uploads"
import { act, renderHook } from "@testing-library/react"

afterEach(() => {
  vi.useRealTimers()
  resetRecentUploadsForTests()
})

describe("recent uploads", () => {
  it("tracks an upload until its note is ready, then highlights it briefly", () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useRecentUploads())

    act(() => markProcessing("appointment_new"))
    expect(isProcessing(result.current, "appointment_new")).toBe(true)

    let announced = false
    act(() => {
      announced = markReady("appointment_new")
    })
    expect(announced).toBe(true)
    expect(isProcessing(result.current, "appointment_new")).toBe(false)
    expect(result.current.justReady.has("appointment_new")).toBe(true)

    act(() => vi.advanceTimersByTime(JUST_READY_HIGHLIGHT_MS))
    expect(result.current.justReady.has("appointment_new")).toBe(false)
  })

  it("announces a ready note only once", () => {
    markProcessing("appointment_new")
    expect(markReady("appointment_new")).toBe(true)
    expect(markReady("appointment_new")).toBe(false)
  })

  it("ignores visits it was not tracking", () => {
    const { result } = renderHook(() => useRecentUploads())

    expect(markReady("appointment_other")).toBe(false)
    expect(result.current.justReady.size).toBe(0)
  })

  it("expires an upload after the timeout, per upload rather than per page", () => {
    const start = 1_000_000
    markProcessing("appointment_new", start)
    const { result } = renderHook(() => useRecentUploads())

    expect(
      isProcessing(result.current, "appointment_new", start + 60_000),
    ).toBe(true)
    expect(
      isProcessing(
        result.current,
        "appointment_new",
        start + NOTE_PROCESSING_TIMEOUT_MS,
      ),
    ).toBe(false)
  })

  it("stops tracking without announcing anything", () => {
    const { result } = renderHook(() => useRecentUploads())

    act(() => markProcessing("appointment_new"))
    act(() => stopTracking("appointment_new"))

    expect(result.current.startedAt.size).toBe(0)
    expect(result.current.justReady.size).toBe(0)
  })
})
