import type { FC } from "react"

import { Loader2, SendHorizontalIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { MarkdownText } from "~/components/assistant-ui/markdown-text"
import {
  EditComposer,
  ThreadScrollToBottom,
} from "~/components/assistant-ui/thread"

import { useIntakeSend } from "./IntakeRuntimeProvider"
import { IntakeThreadWelcome } from "./IntakeThreadWelcome"
import { ComposerPrimitive, ThreadPrimitive } from "@assistant-ui/react"
import { MessagePrimitive } from "@assistant-ui/react"

export const IntakeUserMessage: FC = () => {
  return (
    <MessagePrimitive.Root className="grid w-full max-w-[var(--thread-max-width)] auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] gap-y-2 py-4 [&:where(>*)]:col-start-2">
      <div className="bg-chat-user text-chat-user-foreground col-start-2 row-start-2 max-w-[calc(var(--thread-max-width)*0.8)] rounded-3xl px-5 py-2.5 break-words">
        <MessagePrimitive.Content />
      </div>
    </MessagePrimitive.Root>
  )
}

export const IntakeAssistantMessage: FC = () => {
  return (
    <MessagePrimitive.Root className="relative grid w-full max-w-[var(--thread-max-width)] grid-cols-[auto_auto_1fr] grid-rows-[auto_1fr] py-4">
      <div className="bg-chat-assistant text-chat-assistant-foreground col-span-2 col-start-2 row-start-1 my-1.5 max-w-[calc(var(--thread-max-width)*0.8)] rounded-3xl px-5 py-2.5 leading-7 break-words">
        <MessagePrimitive.Content components={{ Text: MarkdownText }} />
      </div>
    </MessagePrimitive.Root>
  )
}

export const IntakeTypingIndicator: FC = () => {
  return (
    <div
      role="status"
      aria-label="Preparing the next question"
      className="flex w-full max-w-[var(--thread-max-width)] py-4"
    >
      <div className="bg-chat-assistant flex items-center gap-1.5 rounded-3xl px-5 py-4">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            aria-hidden="true"
            className="bg-muted-foreground size-2 rounded-full opacity-70 motion-safe:animate-bounce"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
    </div>
  )
}

export const IntakeSendError: FC = () => {
  const { failed, retry } = useIntakeSend()
  if (!failed) return null
  return (
    <div
      role="alert"
      className="border-destructive/30 bg-destructive/5 flex w-full max-w-[var(--thread-max-width)] flex-wrap items-center justify-between gap-3 rounded-2xl border px-5 py-3 text-sm"
    >
      <p>That didn&apos;t send. Your answer is still here.</p>
      <Button type="button" size="sm" variant="outline" onClick={retry}>
        Try again
      </Button>
    </div>
  )
}

// Intake answers cannot be cancelled mid-request, so the composer only ever
// offers Send, shown as busy while the next question loads.
const IntakeComposerAction: FC = () => {
  return (
    <>
      <ThreadPrimitive.If running={false}>
        <ComposerPrimitive.Send asChild>
          <Button type="submit" size="sm" className="my-2.5">
            Send
            <SendHorizontalIcon className="ml-1.5 size-4" aria-hidden="true" />
          </Button>
        </ComposerPrimitive.Send>
      </ThreadPrimitive.If>
      <ThreadPrimitive.If running>
        <Button type="button" size="sm" className="my-2.5" disabled>
          <Loader2
            className="mr-1.5 size-4 motion-safe:animate-spin"
            aria-hidden="true"
          />
          Sending
        </Button>
      </ThreadPrimitive.If>
    </>
  )
}

export const IntakeComposer: FC = () => {
  return (
    <div className="w-full">
      <ComposerPrimitive.Root className="focus-within:border-ring/20 flex w-full flex-wrap items-end rounded-lg border bg-inherit px-2.5 shadow-sm transition-colors ease-in">
        <ComposerPrimitive.Input
          minRows={3}
          maxRows={12}
          autoFocus
          aria-label="Your answer"
          aria-describedby="intake-composer-hint"
          placeholder="Share as much or as little as you like."
          submitOnEnter={false}
          className="placeholder:text-muted-foreground flex-grow resize-none border-none bg-transparent px-2 py-4 text-base outline-none focus:ring-0 disabled:cursor-not-allowed sm:text-sm"
        />
        <IntakeComposerAction />
      </ComposerPrimitive.Root>
      <p
        id="intake-composer-hint"
        className="text-muted-foreground mt-2 text-center text-xs"
      >
        Enter adds a new line. Tap Send when you&apos;re done.
      </p>
    </div>
  )
}

export const IntakeThread: FC = () => {
  return (
    <ThreadPrimitive.Root
      className="bg-background box-border flex h-full flex-col overflow-hidden"
      style={{
        ["--thread-max-width" as string]: "42rem",
      }}
    >
      <ThreadPrimitive.Viewport className="flex h-full flex-col items-center overflow-y-scroll scroll-smooth bg-inherit px-4 pt-8">
        <IntakeThreadWelcome />

        <ThreadPrimitive.Messages
          components={{
            UserMessage: IntakeUserMessage,
            EditComposer: EditComposer,
            AssistantMessage: IntakeAssistantMessage,
          }}
        />

        <ThreadPrimitive.If running>
          <IntakeTypingIndicator />
        </ThreadPrimitive.If>
        <IntakeSendError />

        <ThreadPrimitive.If empty={false}>
          <div className="min-h-8 flex-grow" />
        </ThreadPrimitive.If>

        <div className="sticky bottom-0 mt-3 flex w-full max-w-[var(--thread-max-width)] flex-col items-center justify-end rounded-t-lg bg-inherit pb-4">
          <ThreadScrollToBottom />
          <IntakeComposer />
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  )
}
