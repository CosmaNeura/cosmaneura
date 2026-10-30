/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.delivery.js (readable input app/panel.js).
 *
 * Exact Sovereign source candidate: 5fa0b611d293e3b53df62aa2a442e21698ec78cc
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "29af2070faec8a9c5ad65f9eeeec1a0a8fba6d178ad165cef502dae71e842694"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-Ka8gcPrsipxa1l+e7uwaCo+6bReK0WXO9QLa5x6EJpQ="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "5fa0b611d293e3b53df62aa2a442e21698ec78cc"
