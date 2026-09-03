/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 024655cd5196344982d918dad5e09f92e046b544
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "ab64c6d2135d7b217834363791728ab04fb9d86524f1643cb72f88cfeb4e0d5c"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-q2TG0hNdeyF4NDY3kXKKsE+52GUk8WQ8ty+Iz+tODVw="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "024655cd5196344982d918dad5e09f92e046b544"
