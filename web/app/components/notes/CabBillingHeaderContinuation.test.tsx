import { webcrypto } from "node:crypto"
import { RouterProvider, createMemoryRouter } from "react-router"
import { afterEach, expect, it, vi } from "vitest"

import {
  expected,
  header,
  op,
  reply,
} from "~/lib/cab-provider-header.test-fixtures"

import { CabBillingHeaderPanel } from "./CabBillingHeaderPanel"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const hash = "a".repeat(64)
afterEach(() => vi.restoreAllMocks())
function mount(disconnected = false, enabled = true) {
  vi.stubGlobal("crypto", webcrypto)
  let now = Date.parse("2026-10-07T12:00:00Z"),
    generationId = "",
    polls = 0
  const start = now,
    originalTimeout = globalThis.setTimeout
  const timers = new Map<number, { at: number; callback: () => void }>()
  let timerId = 1
  const advance = (milliseconds: number) => {
    now += milliseconds
    for (const [id, timer] of [...timers])
      if (timer.at <= now) {
        timers.delete(id)
        queueMicrotask(timer.callback)
      }
  }
  vi.spyOn(globalThis, "clearTimeout").mockImplementation(((id: number) =>
    timers.delete(id)) as never)
  vi.spyOn(Date, "now").mockImplementation(() => now)
  vi.spyOn(globalThis, "setTimeout").mockImplementation(((
    callback: () => void,
    milliseconds?: number,
    ...args: unknown[]
  ) => {
    if (milliseconds === 2000) {
      queueMicrotask(callback)
      return 0 as never
    }
    if (milliseconds === 10_000) {
      const id = timerId++
      timers.set(id, { at: now + milliseconds, callback })
      return id as never
    }
    return originalTimeout(callback, milliseconds, ...args)
  }) as typeof setTimeout)
  const receipt = (authorized: number) => ({
    schemaVersion: "core-cab-generation-continuation.v1",
    purpose: "header.generation-continue",
    sourceOperationId: op,
    operationId: generationId,
    requestSha256: hash,
    generationExpiresAt: new Date(start + 360_000).toISOString(),
    generationAuthorizedUntil: new Date(authorized).toISOString(),
  })
  const request = vi.fn(
    async (
      action: string,
      command: { operationId?: string; continuationConsent?: boolean },
    ) => {
      if (!["generate", "generation-status"].includes(action))
        return reply(action)
      if (action === "generate") generationId = command.operationId!
      else {
        advance(2000)
        polls++
      }
      const status =
        polls < 35 ? "running" : disconnected ? "unknown" : "succeeded"
      return {
        ...reply(action),
        requestSha256: hash,
        result: {
          generation: { operationId: generationId, status },
          view: status === "succeeded" ? header() : null,
          created: status === "succeeded",
          ...(action === "generate" && command.continuationConsent
            ? { continuation: receipt(start + 60_000) }
            : {}),
        },
      }
    },
  )
  const continuing = vi.fn(
    async (command: { operationId?: string; requestSha256: string }) => {
      expect(command.operationId).toBe(generationId)
      expect(command.requestSha256).toBe(hash)
      if (disconnected) throw new Error("Disconnected")
      return receipt(now + 60_000)
    },
  )
  render(
    <RouterProvider
      router={createMemoryRouter([
        {
          path: "/",
          element: (
            <CabBillingHeaderPanel
              operationId={op}
              expectedSource={expected}
              request={request}
              onLocked={vi.fn()}
              asyncEnabled
              continuationEnabled={enabled}
              continueGeneration={continuing}
            />
          ),
        },
      ])}
    />,
  )
  return { request, continuing, elapsed: () => now - start }
}
it("continues one explicitly consented generation beyond an ordinary 60-second token", async () => {
  const { request, continuing, elapsed } = mount()
  const generate = await screen.findByRole("button", {
    name: "Generate billing header",
  })
  const consent = screen.getByRole("checkbox", {
    name: /Keep this generation authorized/,
  })
  expect(consent).not.toBeChecked()
  fireEvent.click(consent)
  fireEvent.click(generate)
  expect(
    await screen.findByRole("button", { name: "Edit header" }),
  ).toBeEnabled()
  expect(elapsed()).toBe(70_000)
  expect(continuing.mock.calls.length).toBeGreaterThan(1)
  expect(
    request.mock.calls.filter(([action]) => action === "generate"),
  ).toHaveLength(1)
  expect(
    request.mock.calls.find(([action]) => action === "generate")?.[1]
      .continuationConsent,
  ).toBe(true)
  expect(
    new Set(
      request.mock.calls
        .filter(([action]) => action === "generation-status")
        .map(([, c]) => c.operationId),
    ).size,
  ).toBe(1)
})
it("stops continuation after disconnect, keeps status read-only and never submits another Generate", async () => {
  const { request, continuing } = mount(true)
  const generate = await screen.findByRole("button", {
    name: "Generate billing header",
  })
  fireEvent.click(
    screen.getByRole("checkbox", { name: /Keep this generation authorized/ }),
  )
  fireEvent.click(generate)
  await screen.findByRole("button", { name: "Resume status" })
  expect(continuing).toHaveBeenCalledTimes(1)
  expect(
    request.mock.calls.filter(([action]) => action === "generate"),
  ).toHaveLength(1)
  fireEvent.click(screen.getByRole("button", { name: "Resume status" }))
  await waitFor(() =>
    expect(
      request.mock.calls.filter(([action]) => action === "generation-status")
        .length,
    ).toBeGreaterThan(35),
  )
  expect(continuing).toHaveBeenCalledTimes(1)
  expect(
    screen.getByRole("button", { name: "Generate billing header" }),
  ).toBeDisabled()
})
it("keeps continuation opt-in absent for old async clients and capabilities", async () => {
  const { request, continuing } = mount(false, false)
  const generate = await screen.findByRole("button", {
    name: "Generate billing header",
  })
  expect(
    screen.queryByRole("checkbox", { name: /Keep this generation authorized/ }),
  ).toBeNull()
  fireEvent.click(generate)
  await screen.findByRole("button", { name: "Edit header" })
  expect(continuing).not.toHaveBeenCalled()
  expect(
    request.mock.calls.find(([action]) => action === "generate")?.[1],
  ).not.toHaveProperty("continuationConsent")
})

async function settleNativeReact(check: () => boolean) {
  const deadline = performance.now() + 2000
  while (!check()) {
    if (performance.now() >= deadline)
      throw new Error("Native asynchronous React state did not settle")
    await act(async () => {
      await new Promise<void>((resolve) => setImmediate(resolve))
    })
  }
}

async function delayedStatusFixture() {
  vi.stubGlobal("crypto", webcrypto)
  let id = "",
    finishStatus: (value: unknown) => void = () => {}
  const continuation = (authorized: number) => ({
    schemaVersion: "core-cab-generation-continuation.v1",
    purpose: "header.generation-continue",
    sourceOperationId: op,
    operationId: id,
    requestSha256: hash,
    generationExpiresAt: new Date(Date.now() + 360_000).toISOString(),
    generationAuthorizedUntil: new Date(authorized).toISOString(),
  })
  let originalDeadline = ""
  const receipt = () => ({
    ...continuation(Date.now() + 55_000),
    generationExpiresAt: originalDeadline,
  })
  const request = vi.fn(
    async (action: string, command: { operationId?: string }) => {
      if (action === "generate") {
        id = command.operationId!
        originalDeadline = new Date(Date.now() + 360_000).toISOString()
        return {
          ...reply(action),
          requestSha256: hash,
          result: {
            generation: { operationId: id, status: "running" },
            view: null,
            created: false,
            continuation: receipt(),
          },
        }
      }
      if (action === "generation-status")
        return new Promise((resolve) => {
          finishStatus = resolve
        })
      return reply(action)
    },
  )
  const continuing = vi.fn(async (_command: unknown, _signal?: AbortSignal) =>
    receipt(),
  )
  const props = {
    operationId: op,
    expectedSource: expected,
    request,
    onLocked: vi.fn(),
    asyncEnabled: true,
    continuationEnabled: true,
    continueGeneration: continuing,
  }
  const container = render(
    <RouterProvider
      router={createMemoryRouter([
        { path: "/", element: <CabBillingHeaderPanel {...props} /> },
      ])}
    />,
  )
  const generate = await screen.findByRole("button", {
    name: "Generate billing header",
  })
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] })
  fireEvent.click(
    screen.getByRole("checkbox", { name: /Keep this generation authorized/ }),
  )
  await act(async () => {
    fireEvent.click(generate)
  })
  // Real WebCrypto completion can require more than a fixed number of turns.
  // Wait for readiness without moving the virtual generation clock.
  await settleNativeReact(
    () =>
      screen.queryByRole("button", {
        name: "Stop renewing generation authorization",
      }) !== null,
  )
  expect(
    screen.getByRole("button", {
      name: "Stop renewing generation authorization",
    }),
  ).toBeVisible()
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(
    request.mock.calls.filter(([action]) => action === "generation-status"),
  ).toHaveLength(1)
  return {
    request,
    continuing,
    container,
    finish: () =>
      finishStatus({
        ...reply("generation-status"),
        result: {
          generation: { operationId: id, status: "succeeded" },
          view: header(),
          created: true,
        },
      }),
  }
}

it("renews independently while a status request waits through a simulated70-second inference", async () => {
  try {
    const f = await delayedStatusFixture()
    await act(async () => vi.advanceTimersByTimeAsync(68_000))
    expect(f.continuing.mock.calls.length).toBeGreaterThan(5)
    expect(
      f.request.mock.calls.filter(([action]) => action === "generation-status"),
    ).toHaveLength(1)
    await act(async () => {
      f.finish()
    })
    await settleNativeReact(
      () =>
        screen.queryByRole<HTMLButtonElement>("button", { name: "Edit header" })
          ?.disabled === false,
    )
    expect(screen.getByRole("button", { name: "Edit header" })).toBeEnabled()
    expect(
      f.request.mock.calls.filter(([action]) => action === "generate"),
    ).toHaveLength(1)
    const previous = f.continuing.mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(30_000))
    expect(f.continuing).toHaveBeenCalledTimes(previous)
  } finally {
    vi.useRealTimers()
  }
})

it("aborts an owned continuation when stopped and ignores its late receipt", async () => {
  try {
    const f = await delayedStatusFixture()
    let resolve: (value: unknown) => void = () => {},
      signal: AbortSignal | undefined
    f.continuing.mockImplementationOnce((_body, activeSignal) => {
      signal = activeSignal
      return new Promise((done) => {
        resolve = done
      }) as never
    })
    await act(async () => vi.advanceTimersByTimeAsync(8000))
    expect(f.continuing).toHaveBeenCalledTimes(1)
    fireEvent.click(
      screen.getByRole("button", {
        name: "Stop renewing generation authorization",
      }),
    )
    expect(signal?.aborted).toBe(true)
    await act(async () => {
      resolve({})
      await vi.advanceTimersByTimeAsync(40_000)
    })
    expect(f.continuing).toHaveBeenCalledTimes(1)
    f.container.unmount()
  } finally {
    vi.useRealTimers()
  }
})
