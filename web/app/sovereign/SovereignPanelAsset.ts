/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 4d9ccc80f6449ad347f922ec4cd4ce4b36a804b7
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "030b3854b0d9b5a385624ce8f786c7aca4fcc88aa3c2b05e8d6963f85f7ea4a4"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-Aws4VLDZtaOFYkzo94bHrKT8yIqjwrBejWlj+F9+pKQ="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "4d9ccc80f6449ad347f922ec4cd4ce4b36a804b7"
