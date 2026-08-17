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
    const digest = createHash("sha256").update(bytes).digest()

    expect(digest.toString("hex")).toBe(SOVEREIGN_PANEL_SHA256)
    expect(`sha256-${digest.toString("base64")}`).toBe(
      SOVEREIGN_PANEL_INTEGRITY,
    )
    expect(SOVEREIGN_PANEL_ASSET_PATH).toContain(SOVEREIGN_PANEL_SHA256)
    expect(SOVEREIGN_PANEL_UPSTREAM_COMMIT).toBe(
      "d8ae83112b0a3e5f1f84bf071c3cb8bb92abe3e0",
    )
    expect(bytes.toString("utf8")).toContain(
      "setRecord: function (id) { return loadThreadFor(id); }",
    )
  })
})
