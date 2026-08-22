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
      "471f497d1824535cf2a7903f25406c0fa7ae39e9",
    )
    expect(bytes.toString("utf8")).toContain(
      "setRecord: function (id) { return loadThreadFor(id); }",
    )
    expect(bytes.toString("utf8")).toContain('if (opts.embedVersion !== "1.0")')
    expect(bytes.toString("utf8")).toContain(
      'if (typeof opts.onHumanContact !== "function")',
    )
    expect(bytes.toString("utf8")).toContain(
      "if (r.status === 401) attempt.forceRefresh = true",
    )
    expect(bytes.toString("utf8")).toContain("? { forceRefresh: true }")
    expect(bytes.toString("utf8")).not.toContain(
      'headers["x-cosmaneura-mount-context"]',
    )
  })
})
