/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.delivery.js (readable input app/panel.js).
 *
 * Published upstream: ab750355e9cb6d900ac0b30ef7d898f61a91c9c1 (merged provider PR107).
 * Pins the compiled delivery used by the paired production release.
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "d7521739e60854303ea5916b83e15921c62b9f16261b8ae3cb9bd8ac8c220877"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-11IXOeYIVDA+pZFrg+FZIcYrnxYmG4rjy5vYrIwiCHc="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "ab750355e9cb6d900ac0b30ef7d898f61a91c9c1"
