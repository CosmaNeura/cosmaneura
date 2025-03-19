// TODO broken on RR7
// https://discord.com/channels/1251324227668283443/1251324228171726889/1341403438151503994
import { Thread } from "@/components/assistant-ui/thread"

import { AssistantRuntimeProvider, useEdgeRuntime } from "@assistant-ui/react"

export default function Chat() {
  const runtime = useEdgeRuntime({
    api: "http://localhost:8000/api/chat",
    unstable_AISDKInterop: true,
  })

  return (
    <div className="h-full">
      <AssistantRuntimeProvider runtime={runtime}>
        <Thread />
      </AssistantRuntimeProvider>
    </div>
  )
}
