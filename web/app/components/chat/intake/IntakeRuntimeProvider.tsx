// https://www.assistant-ui.com/docs/runtimes/custom/external-store
import { ReactNode, createContext, useContext, useRef, useState } from "react"

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

export class IntakeRequestError extends Error {
  constructor(readonly status: number) {
    super(`intake request failed with status ${status}`)
  }
}

// A revoked or unknown link, or a request refused outright, will not succeed
// on retry; everything else (network loss, 5xx) might.
function isRetryable(error: unknown) {
  return !(
    error instanceof IntakeRequestError &&
    error.status >= 400 &&
    error.status < 500
  )
}

async function getCurrentQuestion(
  apiEndpoint: string,
): Promise<IntakeResponse> {
  const response = await fetch(apiEndpoint, { credentials: "include" })
  if (!response.ok) throw new IntakeRequestError(response.status)
  return response.json()
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
    throw new IntakeRequestError(response.status)
  }

  const intakeResponse = response.json()
  return intakeResponse
}

type IntakeSendState = {
  failure: "retryable" | "fatal" | null
  retry: () => void
}

const IntakeSendContext = createContext<IntakeSendState>({
  failure: null,
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
  const [fatal, setFatal] = useState(false)
  const lastAsked = useRef<string | undefined>(undefined)

  function accept(response: IntakeResponse) {
    if (response.status === "completed") {
      setIsFinished(true)
      return
    }
    setMessages((currentConversation) => [
      ...currentConversation,
      { role: "assistant", content: response.question },
    ])
  }

  async function send(answer: string, askedQuestion: string | undefined) {
    lastAsked.current = askedQuestion
    setFailedAnswer(null)
    setIsRunning(true)
    try {
      accept(await getNextQuestion(api, answer))
    } catch (error) {
      setFailedAnswer(answer)
      setFatal(!isRetryable(error))
    } finally {
      setIsRunning(false)
    }
  }

  // The server records an answer against its next unanswered question, so a
  // request that committed but lost its response must not be resent: that
  // would file the same answer under the following question. Ask the server
  // where the intake stands first, and resend only if it is still waiting on
  // the question this answer was written for.
  async function retry(answer: string) {
    setFailedAnswer(null)
    setIsRunning(true)
    try {
      const current = await getCurrentQuestion(api)
      if (current.status === "completed") {
        accept(current)
      } else if (!current.question) {
        // No question to compare against yet; leave the answer unsent.
        setFailedAnswer(answer)
      } else if (current.question !== lastAsked.current) {
        // The earlier request was recorded; show where the intake moved on.
        accept(current)
      } else {
        accept(await getNextQuestion(api, answer))
      }
    } catch (error) {
      setFailedAnswer(answer)
      setFatal(!isRetryable(error))
    } finally {
      setIsRunning(false)
    }
  }

  const onNew = async (message: AppendMessage) => {
    if (message.content[0]?.type !== "text") {
      throw new Error("Only text messages are supported")
    }

    const latestMessageInput = message.content[0].text
    const askedQuestion = [...messages]
      .reverse()
      .find((entry) => entry.role === "assistant")?.content

    setMessages((currentConversation) => [
      ...currentConversation,
      { role: "user", content: latestMessageInput },
    ])

    await send(latestMessageInput, askedQuestion)
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
    failure: failedAnswer === null ? null : fatal ? "fatal" : "retryable",
    retry: () => {
      if (failedAnswer !== null && !fatal && !isRunning)
        void retry(failedAnswer)
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
