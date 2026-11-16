import { afterEach, beforeEach, expect, it, vi } from "vitest"

import {
  commitViewAsControlTransition,
  ensureViewAsRequestBinding,
  resetViewAsBindingStateForTests,
} from "~/lib/view-as-binding"

import { DefaultTemplateSetup } from "./DefaultTemplateSetup"
import { DeleteNoteTemplate } from "./DeleteNoteTemplate"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"

vi.hoisted(() => {
  vi.stubEnv("VITE_CLERK_PUBLIC_KEY", "pk_test_synthetic")
  vi.stubEnv("VITE_PYTHON_URL", "https://synthetic.invalid")
})
vi.mock("@clerk/clerk-react", () => ({
  ClerkProvider: () => null,
  useAuth: () => ({
    isLoaded: sdk.loaded,
    userId: sdk.user.id,
    sessionId: sdk.session.id,
  }),
  useClerk: () => sdk,
}))
vi.mock("~/components/ViewAsRouteGuard", () => ({
  useViewAsActive: () => false,
}))
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }))
const listeners = new Set<(resources: unknown) => void>()
const sdk = {
  loaded: true,
  user: { id: "synthetic_user_a" },
  session: {
    id: "synthetic_session_a",
    getToken: vi.fn().mockResolvedValue("synthetic-token-a"),
  },
  load: vi.fn(),
  addListener: (listener: (resources: unknown) => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },
  redirectToSignIn: vi.fn(),
}
const fetch = vi.fn()
function deferred<T>() {
  let resolve!: (value: T) => void
  return {
    promise: new Promise<T>((done) => {
      resolve = done
    }),
    resolve: (value: T) => resolve(value),
  }
}
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}
function switchSession() {
  sdk.session = {
    id: "synthetic_session_b",
    getToken: vi.fn().mockResolvedValue("synthetic-token-b"),
  }
  listeners.forEach((listener) =>
    listener({ user: sdk.user, session: sdk.session }),
  )
}
beforeEach(() => {
  sdk.user = { id: "synthetic_user_a" }
  sdk.session = {
    id: "synthetic_session_a",
    getToken: vi.fn().mockResolvedValue("synthetic-token-a"),
  }
  sdk.loaded = true
  listeners.clear()
  fetch.mockReset()
  vi.stubGlobal("Clerk", sdk)
  vi.stubGlobal("fetch", fetch)
  resetViewAsBindingStateForTests()
  commitViewAsControlTransition({ request_binding: "inactive" })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it.each([
  ["initialize", "session"],
  ["delete", "session"],
  ["initialize", "unmount"],
  ["delete", "unmount"],
])(
  "does not hard-block or reload after a stale %s 409 and %s change",
  async (operation, lifecycle) => {
    const pending = deferred<Response>()
    fetch
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValue(response({ status: "available", created: false }))
    const reload = vi
      .spyOn(window.location, "reload")
      .mockImplementation(() => {})
    const changed = vi.fn()
    const view = render(
      operation === "initialize" ? (
        <DefaultTemplateSetup
          doctorId="synthetic_doctor_a"
          onCreated={changed}
        />
      ) : (
        <DeleteNoteTemplate
          doctorId="synthetic_doctor_a"
          schemaId="owned-synthetic-schema"
          name="SOAP"
          onDeleted={changed}
        />
      ),
    )
    if (operation === "delete") {
      fireEvent.click(screen.getByRole("button", { name: "Delete template" }))
      fireEvent.click(screen.getByRole("button", { name: "Confirm deletion" }))
    }
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    if (lifecycle === "unmount") view.unmount()
    else {
      act(switchSession)
      view.rerender(
        operation === "initialize" ? (
          <DefaultTemplateSetup
            doctorId="synthetic_doctor_a"
            onCreated={changed}
          />
        ) : (
          <DeleteNoteTemplate
            doctorId="synthetic_doctor_a"
            schemaId="owned-synthetic-schema"
            name="SOAP"
            onDeleted={changed}
          />
        ),
      )
    }
    await act(async () => {
      pending.resolve(
        response({ detail: { code: "VIEW_AS_BINDING_STALE" } }, 409),
      )
    })
    expect(reload).not.toHaveBeenCalled()
    await expect(ensureViewAsRequestBinding()).resolves.toBe("inactive")
    if (operation === "delete" && lifecycle === "session") {
      expect(changed).not.toHaveBeenCalled()
      expect(
        screen.getByRole("button", { name: "Delete template" }),
      ).toBeInTheDocument()
    }
  },
)

it("resets deletion confirmation on a same-Doctor session change", () => {
  const props = {
    doctorId: "synthetic_doctor_a",
    schemaId: "owned-synthetic-schema",
    name: "SOAP",
    onDeleted: vi.fn(),
  }
  const view = render(<DeleteNoteTemplate {...props} />)
  fireEvent.click(screen.getByRole("button", { name: "Delete template" }))
  act(switchSession)
  view.rerender(<DeleteNoteTemplate {...props} />)
  expect(
    screen.queryByRole("button", { name: "Confirm deletion" }),
  ).not.toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: "Delete template" }),
  ).toBeInTheDocument()
  expect(fetch).not.toHaveBeenCalled()
})

it("does not dispatch an old deletion after token retrieval changes sessions", async () => {
  const token = deferred<string>()
  sdk.session.getToken.mockReturnValue(token.promise)
  render(
    <DeleteNoteTemplate
      doctorId="synthetic_doctor_a"
      schemaId="owned-synthetic-schema"
      name="SOAP"
      onDeleted={vi.fn()}
    />,
  )
  fireEvent.click(screen.getByRole("button", { name: "Delete template" }))
  fireEvent.click(screen.getByRole("button", { name: "Confirm deletion" }))
  await waitFor(() => expect(sdk.session.getToken).toHaveBeenCalledOnce())
  act(switchSession)
  await act(async () => {
    token.resolve("synthetic-token-a")
  })
  expect(fetch).not.toHaveBeenCalled()
})

it("initializes the newly current owner separately with the captured session token", async () => {
  fetch.mockResolvedValue(response({ status: "available", created: true }))
  sdk.user.id = "synthetic_user_b"
  switchSession()
  const changed = vi.fn()
  render(
    <DefaultTemplateSetup doctorId="synthetic_doctor_b" onCreated={changed} />,
  )
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  const request = fetch.mock.calls[0][0] as Request
  expect(request.url).toBe(
    "https://synthetic.invalid/internal/v1/note-schemas/initialize",
  )
  expect(request.headers.get("Authorization")).toBe("Bearer synthetic-token-b")
  expect(await request.clone().json()).toEqual({})
  expect(fetch).toHaveBeenCalledTimes(1)
})

it("deletes the selected resource once under its unchanged session", async () => {
  fetch.mockResolvedValue(new Response(null, { status: 204 }))
  const changed = vi.fn()
  render(
    <DeleteNoteTemplate
      doctorId="synthetic_doctor_a"
      schemaId="owned-synthetic-schema"
      name="SOAP"
      onDeleted={changed}
    />,
  )
  fireEvent.click(screen.getByRole("button", { name: "Delete template" }))
  fireEvent.click(screen.getByRole("button", { name: "Confirm deletion" }))
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  const request = fetch.mock.calls[0][0] as Request
  expect(request.url).toBe(
    "https://synthetic.invalid/internal/v1/note-schemas/owned-synthetic-schema",
  )
  expect(request.method).toBe("DELETE")
  expect(request.headers.get("Authorization")).toBe("Bearer synthetic-token-a")
})
