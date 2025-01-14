// https://discord.com/channels/770287896669978684/1326588296025083944
import { ViteDevServer } from "vite"

type TagDescriptor = {
  tag: string
  attrs?: Record<string, string>
  children?: string
  injectTo?: "head" | "body" | "head-prepend" | "body-prepend"
}

type HtmlTransformResult =
  | string
  | TagDescriptor[]
  | { html: string; tags?: TagDescriptor[] }

type TransformContext = {
  server?: ViteDevServer
  bundle?: OutputBundle
  chunk?: OutputChunk
}

export async function transformHtmlWithPlugin(
  indexPath: string,
  plugin: {
    transformIndexHtml?:
      | {
          order?: "pre" | "post"
          handler: (
            html: string,
            ctx: TransformContext,
          ) => Promise<HtmlTransformResult> | HtmlTransformResult
        }
      | ((
          html: string,
          ctx: TransformContext,
        ) => Promise<HtmlTransformResult> | HtmlTransformResult)
  },
): Promise<void> {
  const fs = require("fs/promises")
  const html = await fs.readFile(indexPath, "utf-8")

  if (!plugin.transformIndexHtml) return

  const transform =
    typeof plugin.transformIndexHtml === "function"
      ? plugin.transformIndexHtml
      : plugin.transformIndexHtml.handler

  const ctx: TransformContext = {}
  const result = await transform(html, ctx)

  let transformedHtml = html
  if (typeof result === "string") {
    transformedHtml = result
  } else if ("html" in result) {
    transformedHtml = result.html
    if (result.tags) {
      transformedHtml = injectTags(transformedHtml, result.tags)
    }
  } else if (Array.isArray(result)) {
    transformedHtml = injectTags(transformedHtml, result)
  }

  await fs.writeFile(indexPath, transformedHtml)
}

function injectTags(html: string, tags: TagDescriptor[]): string {
  return tags.reduce((html, tag) => {
    const attrs = tag.attrs
      ? Object.entries(tag.attrs)
          .map(([key, value]) => `${key}="${value}"`)
          .join(" ")
      : ""
    const element = `<${tag.tag} ${attrs}>${tag.children || ""}</${tag.tag}>`

    const injectTo = tag.injectTo || "head-prepend"
    switch (injectTo) {
      case "head":
        return html.replace("</head>", `${element}</head>`)
      case "body":
        return html.replace("</body>", `${element}</body>`)
      case "head-prepend":
        return html.replace("<head>", `<head>${element}`)
      case "body-prepend":
        return html.replace("<body>", `<body>${element}`)
      default:
        return html
    }
  }, html)
}
