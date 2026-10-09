// https://www.assistant-ui.com/docs/runtimes/custom/external-store
import { ReactNode, createContext, useContext, useState } from "react"

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

type IntakeSendState = {
  failed: boolean
  retry: () => void
}

const IntakeSendContext = createContext<IntakeSendState>({
  failed: false,
  retry: () => {},
})

export function useIntakeSend() {
  return useContext(IntakeSendContext)
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
  // The unsent answer stays in the thread as the patient's message; a retry
  // resends it rather than appending it again.
  const [failedAnswer, setFailedAnswer] = useState<string | null>(null)

  async function send(answer: string) {
    setFailedAnswer(null)
    setIsRunning(true)
    try {
      const questionResponse = await getNextQuestion(api, answer)

      if (questionResponse.status === "completed") {
        setIsFinished(true)
        return
      }

      setMessages((currentConversation) => [
        ...currentConversation,
        { role: "assistant", content: questionResponse.question },
      ])
    } catch {
      setFailedAnswer(answer)
    } finally {
      setIsRunning(false)
    }
  }

  const onNew = async (message: AppendMessage) => {
    if (message.content[0]?.type !== "text") {
      throw new Error("Only text messages are supported")
    }

    const latestMessageInput = message.content[0].text

    setMessages((currentConversation) => [
      ...currentConversation,
      { role: "user", content: latestMessageInput },
    ])

    await send(latestMessageInput)
  }

  const runtime = useExternalStoreRuntime({
    isRunning,
    // Keep the composer closed while an unsent answer waits for a retry, so a
    // second answer cannot be sent to the wrong question.
    isDisabled: failedAnswer !== null,
    messages,
    convertMessage,
    onNew,
  })

  const sendState: IntakeSendState = {
    failed: failedAnswer !== null,
    retry: () => {
      if (failedAnswer !== null && !isRunning) void send(failedAnswer)
    },
  }

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <IntakeSendContext.Provider value={sendState}>
        {children}
      </IntakeSendContext.Provider>
    </AssistantRuntimeProvider>
  )
}
