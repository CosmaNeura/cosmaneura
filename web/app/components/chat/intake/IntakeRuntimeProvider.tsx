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

// TODO need to manually sync with the backend, should really use a
interface IntakeResponse {
  question: string
  status: string
}

export const getNextQuestion = async (
  apiEndpoint: string,
  messageInput: string,
): Promise<IntakeResponse> => {
  const response = await fetch(apiEndpoint, {
    method: "POST",
    // The chat UI is served from a sibling origin. Include the signed platform
    // session when one exists so the server can reject writes during view-as.
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      answer: messageInput,
    }),
  })

  if (!response.ok) {
    throw new Error("Failed to fetch response from backend")
  }

  const intakeResponse = response.json()
  return intakeResponse
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

    const questionResponse = await getNextQuestion(api, latestMessageInput)

    if (questionResponse.status === "completed") {
      setIsFinished(true)
      return
    }

    const assistantMessage: IntakeMessage = {
      role: "assistant",
      content: questionResponse.question,
    }

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
