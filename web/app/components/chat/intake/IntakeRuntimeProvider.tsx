// https://www.assistant-ui.com/docs/runtimes/custom/external-store
import { ReactNode, useState } from "react"

import {
  AppendMessage,
  AssistantRuntimeProvider,
  ThreadMessageLike,
  useExternalStoreRuntime,
} from "@assistant-ui/react"

const convertMessage = (message: MyMessage): ThreadMessageLike => {
  return {
    role: message.role,
    content: [{ type: "text", text: message.content }],
  }
}

export function MyRuntimeProvider({
  children,
}: Readonly<{
  children: ReactNode
}>) {
  const [isRunning, setIsRunning] = useState(false)
  const [messages, setMessages] = useState<MyMessage[]>([])

  const onNew = async (message: AppendMessage) => {
    if (message.content[0]?.type !== "text")
      throw new Error("Only text messages are supported")

    const input = message.content[0].text
    setMessages((currentConversation) => [
      ...currentConversation,
      { role: "user", content: input },
    ])

    setIsRunning(true)
    const assistantMessage = await backendApi(input)
    setMessages((currentConversation) => [
      ...currentConversation,
      assistantMessage,
    ])
    setIsRunning(false)
  }

  const runtime = useExternalStoreRuntime({
    isRunning,
    messages,
    convertMessage,
    onNew,
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  )
}
