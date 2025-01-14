import * as React from "react"
import { useLocation, useNavigate } from "react-router"

import { Command, File, Inbox, Settings } from "lucide-react"

import { Label } from "@/components/ui/label"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInput,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"
import { Status } from "~/components/status-badge"
import { Appointment } from "~/configuration/client"

import { SkeletonMessageList } from "./skeleton-message-list"
import { StatusBadge } from "./status-badge"
import { UserButton } from "@clerk/clerk-react"

// logo icon at the top
const IconSidebarHeader = () => (
  <SidebarHeader>
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" asChild className="md:h-8 md:p-0">
          <a href="#">
            <div className="size-8 flex aspect-square items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
              <Command className="size-4" />
            </div>
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-semibold">Acme Inc</span>
              <span className="truncate text-xs">Enterprise</span>
            </div>
          </a>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  </SidebarHeader>
)

// TODO should use typed routes here
const NAVIGATION_ITEMS = [
  {
    title: "Intake",
    icon: Inbox,
    url: "/intake",
  },
  {
    title: "Notetaking",
    icon: File,
    url: "/notes",
  },
  {
    title: "Settings",
    icon: Settings,
    url: "/settings",
  },
]

const NavigationMenu = () => {
  const location = useLocation()
  const currentUrl = location.pathname
  const navigate = useNavigate()

  return (
    <SidebarMenu>
      {NAVIGATION_ITEMS.map((item) => {
        return (
          <SidebarMenuItem key={item.title}>
            <SidebarMenuButton
              tooltip={{
                children: item.title,
                hidden: false,
              }}
              onClick={() => navigate(item.url)}
              // TODO this is a bit dangerous: there's got to be a better way to decide what section we are on
              isActive={currentUrl.startsWith(item.url)}
              className="px-2.5 md:px-2"
            >
              <item.icon />
              <span>{item.title}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        )
      })}
    </SidebarMenu>
  )
}
export interface SidebarEntry {
  // for react keys
  id: string
  url: string
  title: string
  location: string
  subTitle?: string
  date: string
  status: Status | "none"
  preview: string
}

// TODO add empty state
function SelectionList({
  isLoading,
  entries,
}: {
  isLoading: boolean
  entries: SidebarEntry[]
}) {
  const navigate = useNavigate()

  const location = useLocation()
  const currentUrl = location.pathname

  return (
    <SidebarGroupContent>
      {isLoading ? (
        <SkeletonMessageList />
      ) : (
        entries.map((entry) => (
          <button
            key={entry.id}
            onClick={() => navigate(entry.url)}
            className={cn(
              "w-full flex flex-col items-start gap-2 whitespace-nowrap border-b p-4 text-sm leading-tight last:border-b-0 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              currentUrl === entry.url &&
                true &&
                "bg-sidebar-accent text-sidebar-accent-foreground",
            )}
          >
            <div className="flex w-full items-center justify-between gap-2">
              <span className="font-medium">{entry.title}</span>
              <span className="text-xs text-slate-500">{entry.date}</span>
            </div>
            <div className="flex w-full items-center justify-between gap-2">
              <span className="text-xs text-slate-500">{entry.location}</span>
              {entry.status !== "none" && <StatusBadge status={entry.status} />}
            </div>
            <span className="font-medium">{entry.subTitle}</span>
            <span className="line-clamp-2 w-[260px] whitespace-break-spaces text-left text-xs text-slate-500">
              {entry.preview}
            </span>
          </button>
        ))
      )}
    </SidebarGroupContent>
  )
}

function SelectionSidebar({
  header,
  isLoading,
  entries,
}: {
  header: string
  isLoading: boolean
  entries: SidebarEntry[]
}) {
  return (
    <Sidebar collapsible="none" className="hidden flex-1 md:flex">
      {/* title with filters */}
      <SidebarHeader className="gap-3.5 border-b p-4">
        <div className="flex w-full items-center justify-between">
          <div className="text-base font-medium text-foreground">{header}</div>
          {/* <Label className="flex items-center gap-2 text-sm">
            <span>Today</span>
            <Switch className="shadow-none" />
          </Label> */}
        </div>
        <SidebarInput placeholder="Type to search..." />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup className="px-0">
          <SelectionList isLoading={isLoading} entries={entries} />
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  )
}
export function IconNavigation() {
  return (
    <Sidebar
      collapsible="none"
      className="!w-[calc(var(--sidebar-width-icon)_+_1px)] border-r"
    >
      <IconSidebarHeader />
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent className="px-1.5 md:px-0">
            <NavigationMenu />
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <UserButton />
      </SidebarFooter>
    </Sidebar>
  )
}

export function AppSidebar({
  isLoading,
  entries,
  header,
  ...props
}: {
  isLoading: boolean
  entries: SidebarEntry[]
  header: string
}) {
  const { setOpen } = useSidebar()

  return (
    <Sidebar
      collapsible="icon"
      className="overflow-hidden [&>[data-sidebar=sidebar]]:flex-row"
      {...props}
    >
      {/* the main navigation icon sidebar */}
      <IconNavigation />

      {/* appointment list sidebar */}
      <SelectionSidebar
        header={header}
        isLoading={isLoading}
        entries={entries}
      />
    </Sidebar>
  )
}
