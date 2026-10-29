import { afterEach, describe, expect, it, vi } from "vitest"

import { getDeploymentEnvironment } from "./deployment-environment"

afterEach(() => vi.unstubAllEnvs())

describe("deployment environment", () => {
  it.each([
    ["https://staging-app.cosmaneura.com", "staging"],
    ["https://staging-app.cosmaneura.com/", "staging"],
    ["https://STAGING-APP.COSMANEURA.COM:443/", "staging"],
    ["https://app.cosmaneura.com/", "production"],
    [
      "https://app.cosmaneura.com:443/api?next=staging-app.cosmaneura.com",
      "production",
    ],
    [
      "https://staging-app.cosmaneura.com/api?next=app.cosmaneura.com",
      "staging",
    ],
  ])("classifies the actual deployed origin: %s", (url, expected) => {
    vi.stubEnv("VITE_PYTHON_URL", url)
    expect(getDeploymentEnvironment()).toBe(expected)
  })

  it.each([
    "https://api.cosma.localhost/",
    "https://api-test.cosma.localhost/",
    "http://localhost:8000/",
    "https://localhost:8443/",
    "http://127.0.0.1:8000/",
    "http://[::1]:8000/",
  ])("allows only explicit local API hosts: %s", (url) => {
    vi.stubEnv("VITE_PYTHON_URL", url)
    expect(getDeploymentEnvironment()).toBe("local")
  })

  it.each([
    "https://staging-app.cosmaneura.com.evil.invalid/",
    "https://app.cosmaneura.com.evil.invalid/",
    "https://evil.invalid/staging-app.cosmaneura.com",
    "https://evil.invalid/?origin=app.cosmaneura.com",
    "https://evil.invalid/#staging-app.cosmaneura.com",
    "https://staging-app.cosmaneura.com@evil.invalid/",
    "https://user:pass@staging-app.cosmaneura.com/",
    "https://user@app.cosmaneura.com/",
    "https://user@localhost:8000/",
    "http://staging-app.cosmaneura.com/",
    "http://app.cosmaneura.com/",
    "https://staging-app.cosmaneura.com:8443/",
    "https://app.cosmaneura.com:8443/",
    "https://api.cosma.localhost.evil.invalid/",
    "https://unconfigured.localhost/",
    "https://localhost.evil.invalid/",
    "https://127.0.0.2/",
    "https://preview.example.invalid/",
    "ftp://localhost/",
    "javascript:alert(1)",
    "blob:https://app.cosmaneura.com/synthetic-id",
    "//staging-app.cosmaneura.com/",
    "/api",
    "https://",
    "not a URL",
    "",
    undefined,
  ])("fails closed for an unknown or invalid target: %s", (url) => {
    vi.stubEnv("VITE_PYTHON_URL", url)
    expect(getDeploymentEnvironment()).toBe("unknown")
  })
})
