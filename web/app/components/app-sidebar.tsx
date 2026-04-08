import * as React from "react"
import { href, useLocation, useNavigate } from "react-router"

import { Calendar, Command, File, Inbox, Settings } from "lucide-react"

import { StatusBadgeOptions } from "@/components/shared/StatusBadge"
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
import { StatusBadge } from "~/components/shared/StatusBadge"

import { SkeletonMessageList } from "./skeleton-message-list"
import { UserButton } from "@clerk/clerk-react"
import { motion } from "motion/react"

// logo icon at the top
const IconSidebarHeader = () => (
  <SidebarHeader>
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" asChild className="md:h-8 md:p-0">
          <a href="#">
            <div className="bg-sidebar-primary text-sidebar-primary-foreground flex aspect-square size-8 items-center justify-center rounded-lg">
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

const NAVIGATION_ITEMS = [
  {
    title: "Intake",
    icon: Inbox,
    url: href("/intake"),
  },
  {
    title: "Notetaking",
    icon: File,
    url: href("/notes"),
  },
  {
    title: "Schedule",
    icon: Calendar,
    url: href("/schedule"),
  },
  {
    title: "Settings",
    icon: Settings,
    url: href("/settings"),
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
  status: StatusBadgeOptions | "none"
  preview: string
}

// not rendered unless entries has contents
function SelectionList({
  isLoading,
  entries,
}: {
  isLoading: boolean
  entries: SidebarEntry[]
}) {
  // TODO loading state is not used here, consider
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
            onClick={() => currentUrl !== entry.url && navigate(entry.url)}
            className={cn(
              "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex w-[var(--sidebar-width)] flex-col items-start gap-2 border-b p-4 text-sm leading-tight",
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
              <span className="text-left text-xs text-slate-500">
                {entry.preview}
              </span>
              {entry.status !== "none" && <StatusBadge status={entry.status} />}
            </div>
            {/* <span className="font-medium">{entry.subTitle}</span>
            <span className="line-clamp-2 w-[260px] whitespace-break-spaces text-left text-xs text-slate-500">
              {entry.preview}
            </span> */}
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
  header?: string
  isLoading: boolean
  entries: SidebarEntry[]
}) {
  const [searchTerm, setSearchTerm] = React.useState("")
  const [showThisWeek, setShowThisWeek] = React.useState(false)

  const filteredEntries = entries.filter((entry) => {
    const matchesSearch = entry.title
      .toLowerCase()
      .includes(searchTerm.toLowerCase())

    if (showThisWeek) {
      const entryDate = new Date(entry.date)
      const today = new Date()
      const startOfWeek = new Date(today)
      startOfWeek.setDate(today.getDate() - today.getDay())
      const endOfWeek = new Date(today)
      endOfWeek.setDate(today.getDate() + (6 - today.getDay()))

      const isThisWeek = entryDate >= startOfWeek && entryDate <= endOfWeek
      return matchesSearch && isThisWeek
    }

    return matchesSearch
  })

  const { open } = useSidebar()

  return (
    <motion.div
      animate={{ width: open ? "var(--sidebar-width)" : 0 }}
      transition={{ ease: [0.25, 0.46, 0.45, 0.94], duration: 0.2 }}
      className="hidden shrink-0 overflow-hidden md:block"
    >
      <Sidebar collapsible="none" className="hidden flex-1 border-r md:flex">
        {/* title with filters */}
        <SidebarHeader className="gap-3.5 border-b p-4">
          <div className="flex w-full items-center justify-between">
            <div className="text-foreground text-base font-medium">
              {header ?? "Appointments"}
            </div>
            <Label className="flex items-center gap-2 text-sm">
              <span>This Week</span>
              <Switch
                className="shadow-none"
                checked={showThisWeek}
                onCheckedChange={setShowThisWeek}
              />
            </Label>
          </div>
          <SidebarInput
            placeholder="Type to search..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup className="px-0">
            {entries.length === 0 && (
              <div className="p-4 text-center text-sm text-slate-500">
                <p>No Appointments</p>
                <p className="mt-2">Upload a visit recording to get started.</p>
              </div>
            )}
            {entries.length > 0 && filteredEntries.length === 0 ? (
              <div className="p-4 text-center text-sm text-slate-500">
                <p>No results found.</p>
                <p className="mt-2">Try adjusting your filters.</p>
              </div>
            ) : (
              <SelectionList isLoading={isLoading} entries={filteredEntries} />
            )}
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>
    </motion.div>
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
  entries: SidebarEntry[] | false
  header?: string
}) {
  const showSidebar = entries !== false

  return (
    <div className="flex overflow-hidden" {...props}>
      {/* the main navigation icon sidebar */}
      <IconNavigation />

      {/* appointment list sidebar */}
      {showSidebar && (
        <SelectionSidebar
          header={header}
          isLoading={isLoading}
          entries={entries}
        />
      )}
    </div>
  )
}
