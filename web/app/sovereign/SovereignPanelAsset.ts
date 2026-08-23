/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Merged-main source commit: 471f497d1824535cf2a7903f25406c0fa7ae39e9
 * Reviewed source parent: f01fa5e27f7122481b974bdefc585cd384f64251
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "955365d6d82912b10f78dabbb534e28b8f3fb73afdead77135972ff403d47ca6"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-lVNl1tgpErEPeNq7tTTii48/tzr96tdxNZcv9APUfKY="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "471f497d1824535cf2a7903f25406c0fa7ae39e9"
