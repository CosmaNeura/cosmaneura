// https://www.assistant-ui.com/docs/runtimes/custom/external-store
import { ReactNode, useState } from "react"

import {
  AppendMessage,
  AssistantRuntimeProvider,
  ThreadMessageLike,
  useExternalStoreRuntime,
} from "@assistant-ui/react"

type IntakeMessage = {
  role: "user" | "assistant"
  content: string
}

const convertMessage = (message: IntakeMessage): ThreadMessageLike => {
  return {
    role: message.role,
    content: [{ type: "text", text: message.content }],
  }
}

const backendApi = async (
  apiEndpoint: string,
  messageInput: string,
): Promise<IntakeMessage> => {
  return { role: "assistant", content: "Hello, world!" }

  const response = await fetch(apiEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      answer: messageInput,
    }),
  })
}

export function IntakeRuntimeProvider({
  api,
  initialMessages,
  children,
  setIsFinished,
}: Readonly<{
  api: string
  initialMessages: IntakeMessage[]
  children: ReactNode
  setIsFinished: (isFinished: boolean) => void
}>) {
  const [isRunning, setIsRunning] = useState(false)
  const [messages, setMessages] = useState<IntakeMessage[]>(initialMessages)

  const onNew = async (message: AppendMessage) => {
    if (message.content[0]?.type !== "text") {
      throw new Error("Only text messages are supported")
    }

    const latestMessageInput = message.content[0].text

    setMessages((currentConversation) => [
      ...currentConversation,
      { role: "user", content: latestMessageInput },
    ])

    // waiting for backend response
    setIsRunning(true)

    const assistantMessage = await backendApi(api, latestMessageInput)

    setMessages((currentConversation) => [
      ...currentConversation,
      assistantMessage,
    ])

    // finished consuming backend response
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
