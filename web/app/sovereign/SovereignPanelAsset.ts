/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Merged-main source commit: 8020b745673a13875aecf32941a6711d22926045
 * Independently reviewed source: a6bde339056cbaf4dea6d51e7196a4a37f8ab452
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "0293b507400050a59ee3fe030bd1403a07b3ba59492a69a72ea303a5da61ad72"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-ApO1B0AAUKWe4/4DC9FAOgezullJKmmnLqMDpdphrXI="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "8020b745673a13875aecf32941a6711d22926045"
