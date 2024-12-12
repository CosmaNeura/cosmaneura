import * as React from 'react';
import { ArchiveX, Command, File, Inbox, Settings, Send, Trash2 } from 'lucide-react';

import { NavUser } from '@/components/nav-user';
import { Label } from '@/components/ui/label';
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
} from '@/components/ui/sidebar';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { mockData } from '@/data/mock';
import { StatusBadge } from './status-badge';
import { useLocation } from 'react-router-dom';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface AppSidebarProps extends React.ComponentProps<typeof Sidebar> {
  onMailSelect?: (mail: any) => void;
  selectedMail?: any;
  onNavigate?: (page: string) => void;
  currentPage?: string;
  isLoading?: boolean;
  loadingContent?: React.ReactNode;
}

export function AppSidebar({ 
  onMailSelect, 
  selectedMail, 
  onNavigate,
  currentPage = 'intake',
  isLoading,
  loadingContent,
  ...props 
}: AppSidebarProps) {
  const location = useLocation();
  const { setOpen } = useSidebar();
  const [mails, setMails] = React.useState(mockData.mails);

  const getIcon = (iconName: string) => {
    switch (iconName) {
      case 'Inbox':
        return Inbox;
      case 'File':
        return File;
      case 'Settings':
        return Settings;
      default:
        return File;
    }
  };

  return (
    <Sidebar
      collapsible="icon"
      className="overflow-hidden [&>[data-sidebar=sidebar]]:flex-row"
      {...props}
    >
      {/* First sidebar - icons */}
      <Sidebar
        collapsible="none"
        className="!w-[calc(var(--sidebar-width-icon)_+_1px)] border-r"
      >
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild className="md:h-8 md:p-0">
                <a href="#">
                  <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
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
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent className="px-1.5 md:px-0">
              <SidebarMenu>
                {mockData.navMain.map((item) => {
                  const Icon = getIcon(item.icon);
                  return (
                    <SidebarMenuItem key={item.title}>
                      <SidebarMenuButton
                        tooltip={{
                          children: item.title,
                          hidden: false,
                        }}
                        onClick={() => {
                          if (onNavigate) {
                            onNavigate(item.page);
                          }
                          if (location.pathname !== '/settings') {
                            setOpen(true);
                          }
                        }}
                        isActive={currentPage === item.page}
                        className="px-2.5 md:px-2"
                      >
                        <Icon />
                        <span>{item.title}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <NavUser user={mockData.user} />
        </SidebarFooter>
      </Sidebar>

      {/* Second sidebar - message list */}
      {currentPage !== 'settings' && (
        <Sidebar collapsible="none" className="hidden flex-1 md:flex">
          <SidebarHeader className="gap-3.5 border-b p-4">
            <div className="flex w-full items-center justify-between">
              <div className="text-base font-medium text-foreground">
                {currentPage === 'intake' ? 'Intake' : 'Notes'}
              </div>
              <Label className="flex items-center gap-2 text-sm">
                <span>Today</span>
                <Switch className="shadow-none" />
              </Label>
            </div>
            <SidebarInput placeholder="Type to search..." />
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup className="px-0">
              <SidebarGroupContent>
                {isLoading ? (
                  loadingContent
                ) : (
                  mails.map((mail) => (
                    <button
                      key={mail.email}
                      onClick={() => onMailSelect?.(mail)}
                      className={cn(
                        "w-full flex flex-col items-start gap-2 whitespace-nowrap border-b p-4 text-sm leading-tight last:border-b-0 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                        selectedMail?.email === mail.email && "bg-sidebar-accent text-sidebar-accent-foreground"
                      )}
                    >
                      <div className="flex w-full items-center justify-between gap-2">
                        <span className="font-medium">{mail.name}</span>
                        <span className="text-xs text-slate-500">{mail.date}</span>
                      </div>
                      <div className="flex w-full items-center justify-between gap-2">
                        <span className="text-xs text-slate-500">{mail.location}</span>
                        {currentPage === 'intake' && (
                          <StatusBadge status={mail.status || "not-started"} />
                        )}
                      </div>
                      <span className="font-medium">{mail.subject}</span>
                      <span className="line-clamp-2 text-left w-[260px] whitespace-break-spaces text-xs text-slate-500">
                        {mail.teaser}
                      </span>
                    </button>
                  ))
                )}
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>
      )}
    </Sidebar>
  );
}