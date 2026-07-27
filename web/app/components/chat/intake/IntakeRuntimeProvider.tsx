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
  question: string | null
  status: "processing" | "completed" | "in_progress"
}

const SUBMISSION_ERROR_MESSAGE =
  "Sorry, something went wrong and your answer wasn't saved. " +
  "Please check your connection and send your answer again. " +
  "You can copy the text from your message above."

const getNextQuestion = async (
  apiEndpoint: string,
  messageInput: string,
): Promise<IntakeResponse> => {
  const response = await fetch(apiEndpoint, {
    method: "POST",
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

  return await response.json()
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

  const appendMessage = (message: IntakeMessage) => {
    setMessages((currentConversation) => [...currentConversation, message])
  }

  const onNew = async (message: AppendMessage) => {
    if (message.content[0]?.type !== "text") {
      throw new Error("Only text messages are supported")
    }

    const latestMessageInput = message.content[0].text

    appendMessage({ role: "user", content: latestMessageInput })

    // waiting for backend response
    setIsRunning(true)

    try {
      const questionResponse = await getNextQuestion(api, latestMessageInput)

      if (questionResponse.status === "completed") {
        setIsFinished(true)
        return
      }

      if (!questionResponse.question) {
        throw new Error("Backend did not return a question")
      }

      appendMessage({ role: "assistant", content: questionResponse.question })
    } catch (error) {
      // the answer was not persisted, so the backend will re-ask the same
      // question when the patient sends again
      console.error("failed to submit intake answer", error)
      appendMessage({ role: "assistant", content: SUBMISSION_ERROR_MESSAGE })
    } finally {
      // without this, a failed request leaves the composer disabled forever
      setIsRunning(false)
    }
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
