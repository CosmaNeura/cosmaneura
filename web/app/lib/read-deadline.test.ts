import { afterEach, expect, it, vi } from "vitest"

import {
  READ_DEADLINE_MS,
  ReadDeadlineError,
  withReadDeadline,
} from "./read-deadline"

afterEach(() => vi.useRealTimers())

it("stops a cancelled loader wait without cancelling another consumer's shared query", async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  const onTimeout = vi.fn()
  const promise = withReadDeadline(() => new Promise(() => {}), {
    signal: controller.signal,
    onTimeout,
  })
  const reason = new DOMException("Navigation cancelled", "AbortError")
  const rejected = expect(promise).rejects.toBe(reason)
  controller.abort(reason)
  await rejected
  await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS * 2)
  expect(onTimeout).not.toHaveBeenCalled()
})

it("aborts the transport and invokes shared-query cancellation on deadline", async () => {
  vi.useFakeTimers()
  let signal!: AbortSignal
  const onTimeout = vi.fn()
  const promise = withReadDeadline(
    (value) => {
      signal = value
      return new Promise(() => {})
    },
    { onTimeout },
  )
  const rejected = expect(promise).rejects.toBeInstanceOf(ReadDeadlineError)
  await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS)
  await rejected
  expect(signal.aborted).toBe(true)
  expect(onTimeout).toHaveBeenCalledOnce()
})
