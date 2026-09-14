import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it, vi } from "vitest"

import {
  frontendBuildDirectory,
  productionBuildEnvironment,
} from "../build-environment.mjs"
import { runProductionBuild } from "./build-production.mjs"

type Run = NonNullable<Parameters<typeof runProductionBuild>[1]>

function result(status: number | null, signal: NodeJS.Signals | null = null) {
  return { status, signal }
}

describe("production frontend build environment", () => {
  it.each(["production", "staging", "development", "test"])(
    "bundles %s as production without changing its output target",
    (target) => {
      const input = {
        NODE_ENV: target,
        VITE_CLERK_PUBLIC_KEY: "pk_test_synthetic",
        VITE_PYTHON_URL: "https://staging-app.cosmaneura.com/",
        VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED: "false",
        VITE_ENABLE_TEST_AUTH: "false",
      }
      const before = { ...input }
      const env = productionBuildEnvironment(input)
      expect(env).toEqual({
        ...input,
        NODE_ENV: "production",
        COSMANEURA_BUILD_TARGET: target,
      })
      expect(frontendBuildDirectory(env)).toBe(`build/${target}`)
      expect(input).toEqual(before)
      expect(env).not.toHaveProperty("SENTRY_AUTH_TOKEN")
    },
  )

  it("defaults an unset build environment to production", () => {
    expect(frontendBuildDirectory(productionBuildEnvironment({}))).toBe(
      "build/production",
    )
  })

  it.each(["", "preview", "../production", "staging/../../public"])(
    "rejects an unsupported or path-like target: %s",
    (NODE_ENV) => {
      expect(() => productionBuildEnvironment({ NODE_ENV })).toThrow(
        "Unsupported frontend build target",
      )
      expect(() => frontendBuildDirectory({ NODE_ENV })).toThrow(
        "Unsupported frontend build target",
      )
    },
  )

  it.each(["", "production", "staging"])(
    "rejects caller override of the private child-process target: %s",
    (COSMANEURA_BUILD_TARGET) => {
      expect(() =>
        productionBuildEnvironment({
          NODE_ENV: "staging",
          COSMANEURA_BUILD_TARGET,
        }),
      ).toThrow("reserved")
    },
  )

  it("refuses direct staging CLI use that would enable Vite DEV branches", () => {
    expect(() => frontendBuildDirectory({ NODE_ENV: "staging" })).toThrow(
      "Use pnpm run build",
    )
  })

  it("requires production NODE_ENV for an internal target", () => {
    expect(() =>
      frontendBuildDirectory({
        NODE_ENV: "development",
        COSMANEURA_BUILD_TARGET: "staging",
      }),
    ).toThrow("requires production bundling")
  })

  it.each(["development", "test", "production"])(
    "preserves direct dev/typegen configuration for %s",
    (NODE_ENV) =>
      expect(frontendBuildDirectory({ NODE_ENV })).toBe(`build/${NODE_ENV}`),
  )
})

describe("production build runner", () => {
  it("runs typegen then production-mode build using the same explicit child environment", () => {
    const run = vi.fn<Run>().mockReturnValue(result(0))
    expect(runProductionBuild({ NODE_ENV: "staging", CI: "true" }, run)).toBe(0)
    expect(run).toHaveBeenCalledTimes(2)
    expect(run.mock.calls[0][1]?.slice(1)).toEqual(["typegen"])
    expect(run.mock.calls[1][1]?.slice(1)).toEqual([
      "build",
      "--mode",
      "production",
    ])
    for (const [executable, argv, options] of run.mock.calls) {
      expect(executable).toBe(process.execPath)
      expect(argv?.[0]).toMatch(/node_modules\/@react-router\/dev\/bin\.js$/)
      expect(options).toMatchObject({
        shell: false,
        stdio: "inherit",
        env: {
          NODE_ENV: "production",
          COSMANEURA_BUILD_TARGET: "staging",
          CI: "true",
        },
      })
    }
  })

  it("stops after a failed typegen without attempting the build", () => {
    const run = vi.fn<Run>().mockReturnValue(result(9))
    expect(runProductionBuild({ NODE_ENV: "staging" }, run)).toBe(9)
    expect(run).toHaveBeenCalledTimes(1)
  })

  it("propagates a failed build", () => {
    const run = vi
      .fn<Run>()
      .mockReturnValueOnce(result(0))
      .mockReturnValueOnce(result(7))
    expect(runProductionBuild({ NODE_ENV: "staging" }, run)).toBe(7)
  })

  it("does not report a signalled child as success", () => {
    const run = vi.fn<Run>().mockReturnValue(result(null, "SIGTERM"))
    expect(runProductionBuild({ NODE_ENV: "staging" }, run)).toBe(1)
  })

  it("is the package build entry point after unchanged OpenAPI generation", () => {
    const pkg = JSON.parse(
      readFileSync(
        resolve(dirname(fileURLToPath(import.meta.url)), "../package.json"),
        "utf8",
      ),
    )
    expect(pkg.scripts.build).toBe(
      "pnpm run openapi && node scripts/build-production.mjs",
    )
  })
})
