/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: f4fa7a12a455ce07ec3e39d1063b1370dbc42456
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "37f81d02819783a1249f89c988e23984a52f4026be80b430c59d619e436e22fb"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-N/gdAoGXg6Ekn4nJiOI5hKUvQCa+gLQwxZ1hnkNuIvs="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "f4fa7a12a455ce07ec3e39d1063b1370dbc42456"
