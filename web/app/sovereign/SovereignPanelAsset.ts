/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: f7f75c63a5459d441f27ef063183346fa89914a2
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "b892f8516c9c08270b0296be41b0601ef9e42636aa7c2ce68b0fdd82bb73695f"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-uJL4UWycCCcLApa+QbBgHvnkJjaqfCzmiw/dgrtzaV8="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "f7f75c63a5459d441f27ef063183346fa89914a2"
