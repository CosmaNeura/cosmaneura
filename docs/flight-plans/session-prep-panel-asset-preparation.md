# Reviewed Session Prep panel asset preparation

Tier 2. Copy the exact independently reviewed Sovereign PR79 panel bytes (SHA256 `37f81d02819783a1249f89c988e23984a52f4026be80b430c59d619e436e22fb`) as a dormant content-addressed asset; keep the existing active pin, SRI, tests and all older assets unchanged.

A separate reviewed activation PR must update the pin, SRI and source binding after this preparation merges. Both must merge before one comprehensive staging image rollout; no intermediate deployment, configuration, schema, Scribe or external-contract change.

Recovery removes only this new asset and its two formatter exclusions before activation. Validate exact upstream byte equality and existing asset/host tests; final-push independent review is required. The activation PR supplies the full combined release flight plan.
