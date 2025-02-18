// TODO broken on RR7
// https://discord.com/channels/1251324227668283443/1251324228171726889/1341403438151503994
import { Thread } from "@/components/assistant-ui/thread"

import { useEdgeRuntime } from "@assistant-ui/react"

// import { Thread } from "@assistant-ui/react"
// import { makeMarkdownText } from "@assistant-ui/react-ui"

// const MarkdownText = makeMarkdownText()

export default function Chat() {
  const runtime = useEdgeRuntime({
    api: "http://localhost:8000/api/chat",
    unstable_AISDKInterop: true,
  })

  return (
    <div className="h-full">
      {/* <Thread
        runtime={runtime}
        assistantMessage={{ components: { Text: MarkdownText } }}
      /> */}
    </div>
  )
}
