# Desktop credential handling

The desktop application never asks a clinician to type a password, API key, or
provider credential into the WebView.

## Clinician sign-in

1. The existing Cosmas provider session selects the appointment and records
   consent.
2. The desktop Rust process creates a PKCE verifier and random state, then opens
   the system browser.
3. The clinician-authenticated provider surface authorizes the launch and
   returns a one-use code through the registered custom callback.
4. Rust exchanges the code, then writes the short-lived bearer and its opaque
   server grant to `com.cosmaneura.capture.credentials`.

That service maps to the macOS Login Keychain and Windows Credential Manager
through `tauri-plugin-keyring-store`. It has no plaintext or in-memory fallback.
The plugin is registered in Rust but has no WebView capability, so JavaScript
cannot read or write its entries.

The PKCE verifier and provider upload capability are zeroized after use. The
stored access token is wrapped in zeroizing memory and is deleted after a
confirmed backend stop. When backend stop coordination is unavailable, the
credential is retained only for bounded reconciliation and expires under the
server's six-hour hard maximum.

## Installation binding

A random 256-bit installation secret is created by Rust and stored under the
same OS credential service. Only its SHA-256 thumbprint leaves the device.
Today the backend records that thumbprint but does not challenge possession.
The field therefore grants no authority, and patient activation remains
blocked until a reviewed proof-of-possession exchange is implemented or the
field is removed.

## Signing and release credentials

- Never commit Apple certificates, App Store Connect private keys, Windows
  code-signing certificates, passwords, or provider SDK credentials.
- Local release work should reference credentials already stored in the OS
  keychain.
- CI release work should read credentials from the protected CI secret store
  only for the signing/notarization job, with environment protection and
  reviewer approval.
- `.env` files are ignored and are not an approved credential store.

The provider adapter remains disabled in all current builds. A production
provider credential is a later deployment dependency, not a desktop-user
credential.
