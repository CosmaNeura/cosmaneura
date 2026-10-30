/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 287c64c6327998cef4907825f2c48e3b3e29af4e
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "2c82702a8afc3911a8cc75be6476038d85aba99c4bb8cde29f8ad97e15ae1c94"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-LIJwKor8ORGozHW+ZHYDjYWrqZxLuM3in4rZfhWuHJQ="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "287c64c6327998cef4907825f2c48e3b3e29af4e"
