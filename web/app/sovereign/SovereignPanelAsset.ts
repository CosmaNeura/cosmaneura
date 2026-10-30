/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: dab1d8e839f0832248017e771cab8800b189ab00
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "1b24151bb15c9d2968a5567a42c6dd94f24087a177b722725bbd881bdbcae59c"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-GyQVG7FcnSlopVZ6QsbdlPJAh6F3tyJyW72IG9vK5Zw="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "dab1d8e839f0832248017e771cab8800b189ab00"
