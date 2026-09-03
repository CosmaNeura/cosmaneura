/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: f521d09df69653761e3b7e05099476e693d31688
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "7e4c4594fa57f77229d57f81544ee218271d1dfb73f77eec7bc4521c69a1411f"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-fkxFlPpX93Ip1X+BVE7iGCcdHftz937se8RSHGmhQR8="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "f521d09df69653761e3b7e05099476e693d31688"
