/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 4e2940495e81a57590678c50ec4279b535120d9f
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "b0dbf0057c4943e382c5e81aae793c1a17ac4e38edbb290c6c6233d50fa55e00"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-sNvwBXxJQ+OCxegarnk8GhesTjjtuykMbGIz1Q+lXgA="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "4e2940495e81a57590678c50ec4279b535120d9f"
