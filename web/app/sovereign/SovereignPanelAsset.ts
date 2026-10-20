/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 26d60d8f6d186a929d8c0d0c088e8ff9e09e83b3
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "0d79b24a21cb5a7d3b7107f9f30685b71bec19dc0cf8050cbe2dd0b51b0d9d73"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-DXmySiHLWn07cQf58waFtxvsGdwM+AUMvi3QtRsNnXM="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "26d60d8f6d186a929d8c0d0c088e8ff9e09e83b3"
