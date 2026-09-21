const buildTargets = new Set(["production", "staging", "development", "test"])
const targetVariable = "COSMANEURA_BUILD_TARGET"

/** @param {string} target */
function checkedTarget(target) {
  if (!buildTargets.has(target)) {
    throw new Error("Unsupported frontend build target")
  }
  return target
}

/** @param {NodeJS.ProcessEnv} environment */
export function productionBuildEnvironment(environment) {
  // This private child-process field must not override the resolved input.
  if (environment[targetVariable] !== undefined) {
    throw new Error("COSMANEURA_BUILD_TARGET is reserved for the build runner")
  }
  const target = checkedTarget(environment.NODE_ENV ?? "production")
  return {
    ...environment,
    NODE_ENV: "production",
    [targetVariable]: target,
  }
}

/** @param {NodeJS.ProcessEnv} environment */
export function frontendBuildDirectory(environment) {
  const target = environment[targetVariable]
  if (target !== undefined && environment.NODE_ENV !== "production") {
    throw new Error("Frontend build target requires production bundling")
  }
  if (target === undefined && environment.NODE_ENV === "staging") {
    throw new Error("Use pnpm run build for production-bundled staging assets")
  }
  // Direct dev/typegen commands keep their own environment and output path.
  return `build/${checkedTarget(target ?? environment.NODE_ENV ?? "development")}`
}
