type DeploymentEnvironment = "staging" | "production" | "local" | "unknown"

// These are the configured dev/test API hosts and explicit loopback targets.
// Other preview or custom hosts must not inherit local feature access.
const LOCAL_HOSTS = new Set([
  "api.cosma.localhost",
  "api-test.cosma.localhost",
  "localhost",
  "127.0.0.1",
  "[::1]",
])

export function getDeploymentEnvironment(): DeploymentEnvironment {
  try {
    const url = new URL(import.meta.env.VITE_PYTHON_URL ?? "")
    if (
      (url.protocol !== "http:" && url.protocol !== "https:") ||
      url.username ||
      url.password
    ) {
      return "unknown"
    }

    if (url.origin === "https://staging-app.cosmaneura.com") return "staging"
    if (url.origin === "https://app.cosmaneura.com") return "production"

    if (LOCAL_HOSTS.has(url.hostname)) return "local"
  } catch {
    // Missing, relative and malformed API URLs must not enable features.
  }
  return "unknown"
}
