# Cosmas desktop companion

The P2 desktop companion is a Tauri 2 application for macOS and Windows. It is
separate from the responsive web/PWA and React Native mobile clients under
architecture ADR 0012.

This scaffold is deliberately dormant:

- no capture provider is linked into a production binary;
- no credential, launch ticket, authorization code, capability, patient
  identifier, or appointment identifier is exposed to the WebView;
- bearer material and the installation secret are read and written only by
  Rust through the OS credential store (macOS Login Keychain or Windows
  Credential Manager);
- a local stop is always attempted and finalized before the backend stop
  receipt is requested; and
- the optional non-PHI preview uses synthetic labels and does not call an API.

## Contract

`pnpm run api:generate` consumes the checked-in
`../contracts/desktop-v1.openapi.json` contract. Generated files are ignored
and must never be edited. Rust owns network and secret handling; the renderer
receives only the redacted `DesktopSnapshot` IPC shape.

## Local setup

1. Install the [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/)
   for the host OS, including Rust.
2. For local backend testing only, export the non-secret
   `COSMAS_PLATFORM_ORIGIN` as an HTTPS URL or a localhost HTTP URL before
   starting Tauri. Release builds pin the origin at compile time and do not
   accept a runtime override.
3. Run `pnpm install --frozen-lockfile` once a lockfile has been generated and
   reviewed, then `pnpm run check`.
4. Run `pnpm run tauri dev` for a local non-patient build.

OAuth tokens are created by browser OIDC/PKCE and stored by the Rust process.
There is no credentials-entry screen. Signing/notarization credentials belong
in the CI secret store or OS keychain, never in this repository or a `.env`
file.

## Browser handoff gate

The backend already exposes the authenticated launch/authorization POSTs and
the `/desktop/v1` token/lifecycle APIs. A clinician-authenticated provider-web
handoff page is still required to complete the system-browser step. That page
must accept the desktop PKCE challenge from a URL fragment, POST through the
existing browser session, and return only the short-lived authorization code
and state through `cosmaneura-capture://oauth/callback`.

Until that surface and installation-key possession proof are reviewed, this
application must not be enabled for patient traffic.
