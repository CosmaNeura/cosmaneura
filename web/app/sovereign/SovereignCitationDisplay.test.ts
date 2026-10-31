import { afterEach, describe, expect, it, vi } from "vitest"

import {
  citationHref,
  loadCitationDisplay,
  parseCitationDisplay,
} from "./SovereignCitationDisplay"
import {
  citationFixture,
  finalReference,
} from "./SovereignCitationDisplay.fixture"

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
describe("accepted display contract, separate from inference", () => {
  it.each(["1.1.0", "1.2.0"] as const)(
    "keeps identical same-day sections in original ordinal order for %s",
    (version) => {
      const value = citationFixture(version)
      const parsed = parseCitationDisplay(value, version)
      expect(parsed).toEqual(value)
      const assistant = value.history.messages[1]
      assistant.reviewed_citations!.reverse()
      expect(() => parseCitationDisplay(value, version)).toThrow()
    },
  )
  it.each([
    "extra",
    "missing",
    "user",
    "null",
    "gap",
    "prefix",
    "url",
    "refused",
    "stale",
    "wrong_version",
  ])("rejects %s without fallback", (kind) => {
    const value = citationFixture()
    const assistant = value.history.messages[1],
      citations = assistant.reviewed_citations!
    if (kind === "extra") Object.assign(value, { record_ref: "not-authority" })
    if (kind === "missing")
      Reflect.deleteProperty(assistant, "reviewed_citations")
    if (kind === "user") value.history.messages[0].reviewed_citations = []
    if (kind === "null") assistant.reviewed_citations = null
    if (kind === "gap") citations[0].ordinal = 3
    if (kind === "prefix")
      citations[1].citation_ref =
        "conversation_final_phase_01m00000000000000000000001.2"
    if (kind === "url")
      Object.assign(citations[0], { url: "https://other.example/" })
    if (kind === "refused") value.turns[0].outcome = "refused"
    if (kind === "stale") {
      value.turns[0].state = "stale"
      value.turns[0].outcome = null
    }
    if (kind === "wrong_version") value.schema_version = "1.1.0"
    expect(() => parseCitationDisplay(value, "1.2.0")).toThrow()
  })
  it("constructs only a current-origin authenticated path from an accepted opaque reference", () => {
    expect(citationHref(`${finalReference}.2`)).toBe(
      `${window.location.origin}/session-prep/citations/${finalReference}.2`,
    )
    for (const value of [
      "https://other.example/",
      `${finalReference}.65`,
      `${finalReference}.01`,
      `${finalReference}.1?token=x`,
    ])
      expect(() => citationHref(value)).toThrow()
  })
  it.each(["1.1.0", "1.2.0"] as const)(
    "makes exactly one fixed %s read with no browser source selectors",
    async (version) => {
      const fetch = vi.fn(
        async () =>
          new Response(JSON.stringify(citationFixture(version)), {
            headers: {
              "Content-Type": "application/json",
              "X-Cosmaneura-Embed-Version": "1.0",
            },
          }),
      )
      vi.stubGlobal("fetch", fetch)
      await loadCitationDisplay({
        origin: "https://provider.example",
        getToken: async () => "synthetic-mount",
        assertCurrent: () => {},
        options: {
          version,
          mode: "recent",
          clientTurnId: null,
          signal: new AbortController().signal,
        },
      })
      expect(fetch).toHaveBeenCalledTimes(1)
      const [path, init] = fetch.mock.calls[0] as unknown as [
        string,
        RequestInit,
      ]
      expect(path).toBe(
        `https://provider.example/embed/${version === "1.1.0" ? "history-with-citations" : "history-retained"}`,
      )
      expect(init.body).toBe(
        `{"schema_version":"${version}","mode":"recent","client_turn_id":null}`,
      )
      expect(init).toMatchObject({
        cache: "no-store",
        redirect: "error",
        credentials: "omit",
        referrerPolicy: "no-referrer",
      })
    },
  )
  it.each([401, 403, 404, 409, 503])(
    "refuses %s without messages or a second read",
    async (status) => {
      const fetch = vi.fn(async () => new Response("private error", { status }))
      vi.stubGlobal("fetch", fetch)
      await expect(
        loadCitationDisplay({
          origin: "https://provider.example",
          getToken: async () => "synthetic-mount",
          assertCurrent: () => {},
          options: {
            version: "1.2.0",
            mode: "recent",
            clientTurnId: null,
            signal: new AbortController().signal,
          },
        }),
      ).rejects.toThrow(/unavailable/)
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )
})
