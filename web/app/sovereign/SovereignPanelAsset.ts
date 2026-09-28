/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 690e31bf0eb64870584e50d8070cd46fa58a1c8b
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "b6e2ec405f5deed72b26b3486e1ee657142d0e7d96f586584b658320c09f9ae7"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-tuLsQF9d7tcrJrNIbh7mVxQtDn2W9YZYS2WDIMCfmuc="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "690e31bf0eb64870584e50d8070cd46fa58a1c8b"
