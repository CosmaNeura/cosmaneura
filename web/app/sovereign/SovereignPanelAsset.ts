/**
 * Byte-pinned copy of CosmaNeura/cosmaneura-sovereign app/panel.js.
 *
 * Exact Sovereign source candidate: f4e47585c2aaae81da2e2207490853eec03fc010
 * Updating the asset requires changing the digest, integrity, and immutable
 * source commit together and passing SovereignPanelAsset.test.ts.
 */
export const SOVEREIGN_PANEL_SHA256 =
  "6e76aed8811dc662ed2a84a108f9247d6213d5670af7bb059f3eed2f07e345fe"

export const SOVEREIGN_PANEL_ASSET_PATH = `/vendor/cosmaneura-panel.v1.${SOVEREIGN_PANEL_SHA256}.js`

export const SOVEREIGN_PANEL_INTEGRITY =
  "sha256-bnau2IEdxmLtKoShCPkkfWIT1WcK97sFnz7tLwfjRf4="

export const SOVEREIGN_PANEL_UPSTREAM_COMMIT =
  "f4e47585c2aaae81da2e2207490853eec03fc010"
