import * as React from "react"
import { Link, NavLink, href, matchPath, useLocation } from "react-router"

import { Calendar, File, Inbox, Settings } from "lucide-react"

import { StatusBadgeOptions } from "@/components/shared/StatusBadge"
import { Label } from "@/components/ui/label"
import { SheetDescription, SheetTitle } from "@/components/ui/sheet"
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
import { ViewAsAccountControl } from "~/components/ViewAsAccountControl"
import { BrandLogo } from "~/components/brand/BrandLogo"
import { StatusBadge } from "~/components/shared/StatusBadge"
import { isInCurrentWeek } from "~/lib/date"

import { SkeletonMessageList } from "./skeleton-message-list"
import { motion } from "motion/react"

// logo icon at the top
const IconSidebarHeader = () => (
  <SidebarHeader>
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" asChild className="md:h-8 md:p-0">
          <Link to="/" aria-label="Cosmas home">
            <BrandLogo variant="light" size="sm" />
          </Link>
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
  const { pathname } = useLocation()

  return (
    <SidebarMenu>
      {NAVIGATION_ITEMS.map((item) => {
        return (
          <SidebarMenuItem key={item.title}>
            <SidebarMenuButton
              asChild
              tooltip={{
                children: item.title,
                hidden: false,
              }}
              // Segment-aware, so /notes matches /notes/:id but not /notesfoo.
              isActive={!!matchPath({ path: item.url, end: false }, pathname)}
              className="px-2.5 md:px-2"
            >
              <NavLink to={item.url}>
                <item.icon />
                <span>{item.title}</span>
              </NavLink>
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
  // display label; isoDate is the machine-readable value for filtering
  date: string
  isoDate: string
  status: StatusBadgeOptions | "none"
  statusLabel?: string
  preview: string
  // consecutive entries with the same group render under one subheading
  group?: string
  // briefly draws attention to an entry that just changed, e.g. a note landing
  highlight?: boolean
}

export interface SidebarEmptyState {
  title: string
  description: string
  action?: React.ReactNode
}

const DEFAULT_EMPTY_STATE: SidebarEmptyState = {
  title: "Nothing here yet",
  description: "New items will appear here.",
}

function SidebarEmptyMessage({
  title,
  description,
  action,
}: SidebarEmptyState) {
  return (
    <div className="p-4 text-center text-sm">
      <p className="text-sidebar-foreground font-medium">{title}</p>
      <p className="text-on-navy-muted mt-2">{description}</p>
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  )
}

function SelectionEntry({ entry }: { entry: SidebarEntry }) {
  const { isMobile, setOpenMobile } = useSidebar()
  const { pathname } = useLocation()
  const isCurrent = pathname === entry.url

  return (
    <Link
      to={entry.url}
      aria-current={isCurrent ? "page" : undefined}
      onClick={(event) => {
        // Re-selecting the open entry should not refetch it, but modifier
        // clicks still open it elsewhere.
        const modified =
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
        if (isCurrent && !modified) event.preventDefault()
        if (isMobile) setOpenMobile(false)
      }}
      className={cn(
        "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground text-sidebar-foreground focus-visible:ring-sidebar-ring flex w-[var(--sidebar-width)] flex-col items-start gap-2 border-b border-[var(--brand-navy-mid)] p-4 text-sm leading-tight focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset",
        isCurrent && "bg-sidebar-accent text-sidebar-accent-foreground",
        entry.highlight &&
          "bg-sidebar-primary/25 transition-colors duration-700",
      )}
    >
      <div className="flex w-full items-center justify-between gap-2">
        <span className="font-medium">{entry.title}</span>
        <span className="text-on-navy-muted shrink-0 text-xs">
          {entry.date}
        </span>
      </div>
      <div className="flex w-full items-center justify-between gap-2">
        <span className="text-on-navy-muted text-left text-xs">
          {entry.preview}
        </span>
        {entry.status !== "none" && (
          <StatusBadge
            status={entry.status}
            label={entry.statusLabel}
            surface="navy"
          />
        )}
      </div>
    </Link>
  )
}

function SelectionList({
  isLoading,
  entries,
}: {
  isLoading: boolean
  entries: SidebarEntry[]
}) {
  if (isLoading) {
    return (
      <SidebarGroupContent>
        <SkeletonMessageList />
      </SidebarGroupContent>
    )
  }

  return (
    <SidebarGroupContent>
      {entries.map((entry, index) => (
        <React.Fragment key={entry.id}>
          {entry.group && entry.group !== entries[index - 1]?.group && (
            <h3 className="text-on-navy-muted px-4 pt-4 pb-2 text-xs font-semibold tracking-wide uppercase">
              {entry.group}
            </h3>
          )}
          <SelectionEntry entry={entry} />
        </React.Fragment>
      ))}
    </SidebarGroupContent>
  )
}

function matchesSearch(entry: SidebarEntry, term: string) {
  const needle = term.trim().toLowerCase()
  if (!needle) return true
  return [entry.title, entry.preview, entry.date, entry.statusLabel]
    .filter(Boolean)
    .some((field) => field!.toLowerCase().includes(needle))
}

function isTypingTarget(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  )
}

// Focus the list search with "/" unless the user is already typing, a dialog
// owns focus, or the list panel is collapsed (its input stays mounted).
function useSlashToFocus(
  inputRef: React.RefObject<HTMLInputElement | null>,
  enabled: boolean,
) {
  React.useEffect(() => {
    if (!enabled) return
    function onKeyDown(event: KeyboardEvent) {
      if (
        event.key !== "/" ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.defaultPrevented ||
        isTypingTarget(event.target) ||
        document.querySelector('[role="dialog"], [role="alertdialog"]') ||
        !inputRef.current
      ) {
        return
      }
      event.preventDefault()
      inputRef.current.focus()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [inputRef, enabled])
}

function SelectionSidebar({
  header,
  isLoading,
  entries,
  emptyState = DEFAULT_EMPTY_STATE,
  searchPlaceholder = "Search…",
}: {
  header?: string
  isLoading: boolean
  entries: SidebarEntry[]
  emptyState?: SidebarEmptyState
  searchPlaceholder?: string
}) {
  const { isMobile, open } = useSidebar()

  const [searchTerm, setSearchTerm] = React.useState("")
  const [showThisWeek, setShowThisWeek] = React.useState(false)
  const searchRef = React.useRef<HTMLInputElement>(null)
  useSlashToFocus(searchRef, isMobile || open)

  const filteredEntries = entries.filter(
    (entry) =>
      matchesSearch(entry, searchTerm) &&
      (!showThisWeek || isInCurrentWeek(new Date(entry.isoDate))),
  )

  const content = (
    <>
      {/* title with filters */}
      <SidebarHeader className="gap-3.5 border-b p-4">
        <div className="flex w-full items-center justify-between">
          <h2 className="text-sidebar-foreground text-base font-medium">
            {header ?? "Appointments"}
          </h2>
          <Label className="text-on-navy-muted flex items-center gap-2 text-sm">
            <span>This Week</span>
            <Switch
              className="shadow-none"
              checked={showThisWeek}
              onCheckedChange={setShowThisWeek}
            />
          </Label>
        </div>
        <SidebarInput
          ref={searchRef}
          type="search"
          aria-label={searchPlaceholder.replace(/…$/, "")}
          aria-keyshortcuts="/"
          placeholder={searchPlaceholder}
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup className="px-0">
          {!isLoading && entries.length === 0 ? (
            <SidebarEmptyMessage {...emptyState} />
          ) : entries.length > 0 && filteredEntries.length === 0 ? (
            <SidebarEmptyMessage
              title="No results found"
              description="Try adjusting your search or filters."
            />
          ) : (
            <SelectionList isLoading={isLoading} entries={filteredEntries} />
          )}
        </SidebarGroup>
      </SidebarContent>
    </>
  )

  if (isMobile) {
    return (
      <Sidebar>
        <SheetTitle className="sr-only">{header ?? "Appointments"}</SheetTitle>
        <SheetDescription className="sr-only">
          Select an appointment to view its details.
        </SheetDescription>
        {content}
      </Sidebar>
    )
  }

  return (
    <motion.div
      animate={{ width: open ? "var(--sidebar-width)" : 0 }}
      transition={{ ease: [0.25, 0.46, 0.45, 0.94], duration: 0.2 }}
      className="hidden shrink-0 overflow-hidden md:block"
    >
      <Sidebar collapsible="none" className="hidden flex-1 border-r md:flex">
        {content}
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
        <ViewAsAccountControl />
      </SidebarFooter>
    </Sidebar>
  )
}

export function AppSidebar({
  isLoading,
  entries,
  header,
  emptyState,
  searchPlaceholder,
  ...props
}: {
  isLoading: boolean
  entries: SidebarEntry[] | false
  header?: string
  emptyState?: SidebarEmptyState
  searchPlaceholder?: string
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
          emptyState={emptyState}
          searchPlaceholder={searchPlaceholder}
        />
      )}
    </div>
  )
}
