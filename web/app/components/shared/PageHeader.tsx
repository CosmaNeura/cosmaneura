import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"

// Detail pages render their own <h1> in the content area, so the sticky
// header only repeats the record title as plain text. Section index pages
// have no content heading of their own, so there the section name is the <h1>.
export function PageHeader({
  title,
  isPageTitle = false,
  actions,
}: {
  title: string
  isPageTitle?: boolean
  actions?: React.ReactNode
}) {
  return (
    <header className="bg-background sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b p-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      {isPageTitle ? (
        <h1 className="text-lg font-semibold">{title}</h1>
      ) : (
        <p
          className="text-muted-foreground min-w-0 truncate text-sm font-medium"
          data-ph-no-capture=""
          data-sentry-block=""
        >
          {title}
        </p>
      )}
      {actions && <div className="ml-auto">{actions}</div>}
    </header>
  )
}
