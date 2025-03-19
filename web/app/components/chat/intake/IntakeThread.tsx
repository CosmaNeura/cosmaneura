import type { FC } from "react"

import {
  AssistantActionBar,
  AssistantMessage,
  BranchPicker,
  Composer,
  EditComposer,
  ThreadScrollToBottom,
  ThreadWelcome,
  UserActionBar,
  UserMessage,
} from "~/components/assistant-ui/thread"

import { IntakeThreadWelcome } from "./IntakeThreadWelcome"
import { ThreadPrimitive } from "@assistant-ui/react"
import { MessagePrimitive } from "@assistant-ui/react"
import { UserMessage } from '~/components/assistant-ui/thread';
import { MarkdownText } from "~/components/assistant-ui/markdown-text"

export const IntakeUserMessage: FC = () => {
  return (
    <MessagePrimitive.Root className="grid w-full max-w-[var(--thread-max-width)] auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] gap-y-2 py-4 [&:where(>*)]:col-start-2">
      <div className="bg-chat-user text-chat-user-foreground col-start-2 row-start-2 max-w-[calc(var(--thread-max-width)*0.8)] rounded-3xl px-5 py-2.5 break-words">
        <MessagePrimitive.Content />
      </div>

      <BranchPicker className="col-span-full col-start-1 row-start-3 -mr-1 justify-end" />
    </MessagePrimitive.Root>
  )
}

export const IntakeAssistantMessage: FC = () => {
  return (
    <MessagePrimitive.Root className="relative grid w-full max-w-[var(--thread-max-width)] grid-cols-[auto_auto_1fr] grid-rows-[auto_1fr] py-4">
      <div className="bg-chat-assistant text-chat-assistant-foreground rounded-3xl px-5 py-2.5 col-span-2 col-start-2 row-start-1 my-1.5 max-w-[calc(var(--thread-max-width)*0.8)] leading-7 break-words">
        <MessagePrimitive.Content components={{ Text: MarkdownText }} />
      </div>

      <BranchPicker className="col-start-2 row-start-2 mr-2 -ml-2" />
    </MessagePrimitive.Root>
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

        <ThreadPrimitive.If empty={false}>
          <div className="min-h-8 flex-grow" />
        </ThreadPrimitive.If>

        <div className="sticky bottom-0 mt-3 flex w-full max-w-[var(--thread-max-width)] flex-col items-center justify-end rounded-t-lg bg-inherit pb-4">
          <ThreadScrollToBottom />
          <Composer />
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  )
}
