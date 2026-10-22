/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: afd6f6bbdcb720f2a066fbb72b7452187eebe25d
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "8dd43796c4f24d781c2302204de64e74e6a2f96e29e603da1aeddc7a1f0c7b17"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-jdQ3lsTyTXgcIwIgTeZOdOai+W4p5gPaGu3ceh8Mexc="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "afd6f6bbdcb720f2a066fbb72b7452187eebe25d"
