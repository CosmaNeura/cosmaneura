/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.delivery.js (readable input app/panel.js).
 *
 * Exact Sovereign source candidate: edf8497ddab3ae1aeba4fc4921b285413d46d7be
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "0c04de969fe0a82fc70dbf1cfa520a78c01d5e6f3b33b4be3a7d8b6fd58d7855"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-DATelp/gqC/HDb8c+lIKeMAdXm87M7S+On2Lb9WNeFU="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "edf8497ddab3ae1aeba4fc4921b285413d46d7be"
