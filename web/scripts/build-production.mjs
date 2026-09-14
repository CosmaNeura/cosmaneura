import { spawnSync } from "node:child_process"
import { dirname, resolve } from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

import { productionBuildEnvironment } from "../build-environment.mjs"

const scriptFile = fileURLToPath(import.meta.url)
const webDirectory = resolve(dirname(scriptFile), "..")
const routerCli = resolve(webDirectory, "node_modules/@react-router/dev/bin.js")

/**
 * @param {NodeJS.ProcessEnv} environment
 * @param {(command: string, args: string[], options: import("node:child_process").SpawnSyncOptions) => Pick<import("node:child_process").SpawnSyncReturns<Buffer>, "status" | "signal" | "error">} run
 */
export function runProductionBuild(environment = process.env, run = spawnSync) {
  const env = productionBuildEnvironment(environment)
  for (const args of [["typegen"], ["build", "--mode", "production"]]) {
    const result = run(process.execPath, [routerCli, ...args], {
      cwd: webDirectory,
      env,
      stdio: "inherit",
      shell: false,
    })
    if (result.error || result.signal || result.status !== 0) {
      return typeof result.status === "number" && result.status !== 0
        ? result.status
        : 1
    }
  }
  return 0
}

if (process.argv[1] && resolve(process.argv[1]) === scriptFile) {
  try {
    if (process.argv.length !== 2) {
      throw new Error(
        "The production build runner does not accept extra arguments",
      )
    }
    process.exitCode = runProductionBuild()
  } catch (error) {
    console.error(
      error instanceof Error ? error.message : "Frontend build failed",
    )
    process.exitCode = 1
  }
}
