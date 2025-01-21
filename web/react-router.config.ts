import { transformHtmlWithPlugin } from "./vite-plugins/react-router-vite-plugin-compatibility"
import type { Config } from "@react-router/dev/config"

// TODO is mode passed over here at all?

export default {
  ssr: false,
  buildDirectory: "build/" + process.env.NODE_ENV,
  async buildEnd(args) {
    // if (!args.viteConfig.isProduction) return

    const log = args.viteConfig.logger

    // this plugin list is already filtered by each plugin's `apply` and is not the raw plugin
    // list specified on
    const pluginList = args.viteConfig.plugins

    // first, let's find all plugins which implement transformIndexHtml
    const plugins = pluginList.filter(
      (plugin) => "transformIndexHtml" in plugin,
    )

    if (plugins.length === 0) {
      log.error("No plugins found which implement transformIndexHtml")
      return
    }

    // now let's run their transformation command
    const indexHTMLPath = args.viteConfig.build.outDir + "/index.html"

    for (const plugin of plugins) {
      log.info(`Running transformIndexHtml for ${plugin.name}`)
      await transformHtmlWithPlugin(indexHTMLPath, plugin)
    }
  },
} satisfies Config
