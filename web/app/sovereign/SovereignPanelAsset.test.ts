import { createHash } from "node:crypto"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"

import {
  SOVEREIGN_PANEL_ASSET_PATH,
  SOVEREIGN_PANEL_INTEGRITY,
  SOVEREIGN_PANEL_SHA256,
  SOVEREIGN_PANEL_UPSTREAM_COMMIT,
} from "./SovereignPanelAsset"
import { readFile } from "node:fs/promises"

describe("pinned Sovereign panel asset", () => {
  it("matches its content address, SRI and audited upstream commit", async () => {
    const bytes = await readFile(
      resolve(process.cwd(), "public", SOVEREIGN_PANEL_ASSET_PATH.slice(1)),
    )
    const prettierIgnore = await readFile(
      resolve(process.cwd(), ".prettierignore"),
      "utf8",
    )
    const eslintConfig = await readFile(
      resolve(process.cwd(), "eslint.config.js"),
      "utf8",
    )
    const digest = createHash("sha256").update(bytes).digest()
    const sourceAssetPath = `public${SOVEREIGN_PANEL_ASSET_PATH}`

    expect(digest.toString("hex")).toBe(SOVEREIGN_PANEL_SHA256)
    expect(`sha256-${digest.toString("base64")}`).toBe(
      SOVEREIGN_PANEL_INTEGRITY,
    )
    expect(SOVEREIGN_PANEL_ASSET_PATH).toContain(SOVEREIGN_PANEL_SHA256)
    expect(prettierIgnore.split(/\r?\n/)).toContain(sourceAssetPath)
    expect(eslintConfig).toContain(JSON.stringify(sourceAssetPath))
    expect(SOVEREIGN_PANEL_UPSTREAM_COMMIT).toBe(
      "024655cd5196344982d918dad5e09f92e046b544",
    )
    expect(bytes.toString("utf8")).toContain(
      "setRecord: function (id) { return loadThreadFor(id); }",
    )
    expect(bytes.toString("utf8")).toContain('if (opts.embedVersion !== "1.0")')
    expect(bytes.toString("utf8")).toContain(
      'if (typeof opts.onHumanContact !== "function")',
    )
    const source = bytes.toString("utf8")
    const governedContext = source.slice(
      source.indexOf("function governedContext"),
      source.indexOf("function governedChatMetadata"),
    )
    expect(governedContext).not.toContain("context_snapshot_ref")
    expect(source).toContain('url + "/session-prep"')
    expect(source).toContain("function governedChatMetadata")
    const chatProjection = source.slice(
      source.indexOf("function governedChatMetadata"),
      source.indexOf("function applyWelcomeUX"),
    )
    expect(chatProjection).not.toContain("context_snapshot_ref")
    const prepRequest = source.slice(
      source.indexOf("async function requestSessionPrep"),
      source.indexOf("async function headersForAttempt"),
    )
    expect(prepRequest).not.toContain("context_snapshot_ref")
    for (const state of ["ready", "refreshing", "pending", "unavailable"])
      expect(source).toContain(`Session Prep ${state}`)
    expect(source).toContain("lockNamedBeta();")
    expect(source).not.toContain("attempt.forceRefresh = true")
    expect(source).not.toContain("localStorage")
    expect(source).not.toContain("sessionStorage")
    expect(source).not.toContain("indexedDB")
    expect(source).not.toContain("caches.open")
    expect(source).not.toContain('headers["x-cosmaneura-mount-context"]')
  })
})
