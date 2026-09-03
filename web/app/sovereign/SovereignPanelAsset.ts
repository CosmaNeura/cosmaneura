/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 8f3b1562cb0296222278d84e09504f3252e96bbf
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "e02a761ba1943b865bb9edeb5434ffe8095f12b865f1f9b4ea402039a16c5bcb"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-4Cp2G6GUO4Zbue3rVDT/6AlfErhl8fm06kAgOaFsW8s="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "8f3b1562cb0296222278d84e09504f3252e96bbf"
