// The provider reference toolkit (medication registry + resource panel, and
// the interaction/lab tools that build on it) is gated to the shared
// per-environment beta allowlist (see beta-access.ts). The server enforces its
// own allowlist in app/services/reference_toolkit_gate.py — keep them in sync.
import { isBetaUserEnabled } from "./beta-access"

export function isReferenceToolkitEnabled(
  email: string | null | undefined,
): boolean {
  return isBetaUserEnabled(email)
}
