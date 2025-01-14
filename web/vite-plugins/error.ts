// lifted from: https://github.com/BlueManiac/DotnetViteVueTemplate/blob/f6996afb8ddda8d56b36042a63cab8a194e91011/src/Web/Util/Plugins/vite-runtime-error-plugin.ts
import { readFileSync } from "node:fs"
import {
  ErrorPayload,
  ModuleGraph,
  type Plugin,
  type WebSocketClient,
  buildErrorMessage,
} from "vite"

import { TraceMap, originalPositionFor } from "@jridgewell/trace-mapping"

// Based on
// https://github.com/hi-ogawa/unocss-preset-antd/tree/main/packages/vite-runtime-error-overlay

// based on the idea in
// https://github.com/vitejs/vite/pull/6274#issuecomment-1087749460
// https://github.com/vitejs/vite/issues/2076

// frame generation logic is based on
// https://github.com/vitejs/vite/blob/a073ac4493e54a2204b5b816fbc7d600df3b34ce/packages/vite/src/node/ssr/ssrStacktrace.ts#L23
// https://github.com/vitejs/vite/blob/0a76652c335e7c0bd8d223186b5533c0e10cac90/packages/vite/src/node/server/middlewares/error.ts#L45
// https://github.com/vitejs/vite/blob/29a260cb16025408defc2e8186d1fbf17ee099ac/packages/vite/src/node/utils.ts#L486

const packageName = "vite-runtime-error-plugin"

export function viteRuntimeErrorOverlayPlugin(options?: {
  filter?: (error: Error) => boolean
}): Plugin {
  return {
    name: packageName,

    // apply: "serve",

    apply(config, env) {
      return env.command === "serve" && !config.ssr
    },

    // apply(config, { command }) {
    //   // apply only on build but not for SSR
    //   return command === "serve" && !config.ssr
    // },

    transformIndexHtml(html: string) {
      return [
        {
          tag: "script",
          attrs: { type: "module" },
          children: CLIENT_SCRIPT,
        },
      ]
    },

    configureServer(server) {
      server.ws.on(MESSAGE_TYPE, (data: unknown, client: WebSocketClient) => {
        const error = Object.assign(new Error(), data)

        if (options?.filter && !options.filter(error)) {
          return
        }

        const { stack, loc } = rewriteStacktrace(
          cleanStack(error.stack),
          server.moduleGraph,
        )
        const source =
          loc?.file && readFileSync(loc.file, { encoding: "utf8", flag: "r" })

        const err: ErrorPayload["err"] = {
          name: error.name,
          message: error.message,
          stack,
          id: loc?.file,
          frame:
            loc &&
            generateCodeFrame(source, {
              line: loc.line,
              column: loc.column - 1,
            }),
          loc,
          plugin: packageName,
        }

        const msg = buildErrorMessage(err, [`Client error: ${err.message}`])

        server.config.logger.error(msg, {
          clear: true,
          timestamp: true,
          error: err,
        })

        // https://vitejs.dev/guide/api-plugin.html#client-server-communication
        // https://github.com/vitejs/vite/blob/5b58eca05939c0667cf9698e83f4f4849f3296f4/packages/vite/src/node/server/middlewares/error.ts#L54-L57
        client.send({
          type: "error",
          err,
        })
      })
    },
  }
}

const MESSAGE_TYPE = `${packageName}:error`

const CLIENT_SCRIPT = /* js */ `

import { createHotContext } from "/@vite/client";

// dummy file path to instantiate import.meta.hot
const hot = createHotContext("/__dummy__${packageName}");

function sendError(error) {
  if (!(error instanceof Error)) {
    error = new Error("(unknown runtime error)");
  }
  const serialized = {
    message: error.message,
    stack: error.stack,
  };
  hot.send("${MESSAGE_TYPE}", serialized);
}

window.addEventListener("error", (evt) => {
  sendError(evt.error);
});

window.addEventListener("unhandledrejection", (evt) => {
  sendError(evt.reason);
});

`

function cleanStack(stack: string) {
  return stack
    .split(/\n/g)
    .filter((l) => /^\s*at/.test(l))
    .join("\n")
}

function rewriteStacktrace(stack: string, moduleGraph: ModuleGraph) {
  let loc: { line: number; column: number; file: string }

  const rewrittenStack = stack
    .split("\n")
    .map((line) => {
      return line.replace(
        /^ {4}at (?:(\S+?) )?\(?(?:https|http):\/\/[^\/]+(\/[^\s?]+).*:(\d+):(\d+)\)?$/,
        (input, varName, url, line, column) => {
          if (!url) return input

          const module = moduleGraph.urlToModuleMap.get(url)

          if (!module) {
            return input
          }

          const rawSourceMap = module?.transformResult?.map

          if (rawSourceMap) {
            const traced = new TraceMap(rawSourceMap as any)

            const pos = originalPositionFor(traced, {
              line: Number(line),
              // stacktrace's column is 1-indexed, but sourcemap's one is 0-indexed
              column: Number(column) - 1,
            })

            if (pos.source && pos.line >= 0 && pos.column >= 0) {
              line = pos.line
              column = pos.column + 1
            }
          }

          const trimmedVarName = varName?.trim()
          const sourceFile = module.file.replace(/\//g, "\\")
          const source = `${sourceFile}:${line}:${column}`

          loc ??= {
            line: Number(line),
            column: Number(column),
            file: sourceFile,
          }

          if (!trimmedVarName || trimmedVarName === "eval") {
            return `    at ${source}`
          } else {
            return `    at ${trimmedVarName} (${source})`
          }
        },
      )
    })
    .join("\n")

  return {
    stack: rewrittenStack,
    loc,
  }
}

type Pos = {
  /** 1-based */
  line: number
  /** 0-based */
  column: number
}

const splitRE = /\r?\n/g
const range: number = 2

function posToNumber(source: string, pos: number | Pos): number {
  if (typeof pos === "number") return pos
  const lines = source.split(splitRE)
  const { line, column } = pos
  let start = 0
  for (let i = 0; i < line - 1 && i < lines.length; i++) {
    start += lines[i].length + 1
  }
  return start + column
}

function generateCodeFrame(
  source: string,
  start: number | Pos = 0,
  end?: number | Pos,
): string {
  start = Math.max(posToNumber(source, start), 0)
  end = Math.min(
    end !== undefined ? posToNumber(source, end) : start,
    source.length,
  )
  const lines = source.split(splitRE)
  let count = 0
  const res: string[] = []
  for (let i = 0; i < lines.length; i++) {
    count += lines[i].length
    if (count >= start) {
      for (let j = i - range; j <= i + range || end > count; j++) {
        if (j < 0 || j >= lines.length) continue
        const line = j + 1
        res.push(
          `${line}${" ".repeat(Math.max(3 - String(line).length, 0))}|  ${
            lines[j]
          }`,
        )
        const lineLength = lines[j].length
        if (j === i) {
          // push underline
          const pad = Math.max(start - (count - lineLength), 0)
          const length = Math.max(
            1,
            end > count ? lineLength - pad : end - start,
          )
          res.push(`   |  ` + " ".repeat(pad) + "^".repeat(length))
        } else if (j > i) {
          if (end > count) {
            const length = Math.max(Math.min(end - count, lineLength), 1)
            res.push(`   |  ` + "^".repeat(length))
          }
          count += lineLength + 1
        }
      }
      break
    }
    count++
  }
  return res.join("\n")
}
