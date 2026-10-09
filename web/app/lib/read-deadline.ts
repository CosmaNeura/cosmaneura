export const READ_DEADLINE_MS = 30_000

export class ReadDeadlineError extends Error {
  constructor() {
    super("Read deadline reached")
    this.name = "ReadDeadlineError"
  }
}

/** Bound authentication, transport and parsing, even if a reader ignores abort. */
export async function withReadDeadline<T>(
  read: (signal: AbortSignal) => Promise<T>,
  options: { signal?: AbortSignal; onTimeout?: () => void } = {},
): Promise<T> {
  const controller = new AbortController()
  let timeout: ReturnType<typeof setTimeout> | undefined
  let cancel = () => {}
  try {
    const deadline = new Promise<never>((_, reject) => {
      cancel = () => {
        const reason =
          options.signal?.reason ?? new DOMException("Cancelled", "AbortError")
        reject(reason)
        controller.abort(reason)
      }
      options.signal?.addEventListener("abort", cancel, { once: true })
      if (options.signal?.aborted) {
        cancel()
        return
      }
      timeout = setTimeout(() => {
        const error = new ReadDeadlineError()
        reject(error)
        controller.abort(error)
        options.onTimeout?.()
      }, READ_DEADLINE_MS)
    })
    return await Promise.race([
      deadline,
      options.signal?.aborted
        ? new Promise<never>(() => {})
        : read(controller.signal),
    ])
  } finally {
    clearTimeout(timeout)
    options.signal?.removeEventListener("abort", cancel)
  }
}
