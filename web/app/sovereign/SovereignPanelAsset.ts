/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: 78fd74277037d4233878e89546d81aeb2d4ada98
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "c9ac2270d55c76e020df46d7a988cc734e00b2c9ef43df4864f557f3714953b9"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-yawicNVcduAg30bXqYjMc04AssnvQ99IZPVX83FJU7k="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "78fd74277037d4233878e89546d81aeb2d4ada98"
