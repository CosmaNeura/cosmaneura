/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: aedd242413c06045586c720cd1c7b132a2441423
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "c51971eccfab76e86224dbd3c335141833cc643ce59c2b615016bac21a269eb6"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-xRlx7M+rduhiJNvTwzUUGDPMZDzlnCthUBa6whomnrY="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "aedd242413c06045586c720cd1c7b132a2441423"
