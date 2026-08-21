import { useState } from "react"

import { Check, Circle, Minus } from "lucide-react"

import { BAA_PDF_URL } from "~/lib/baa"
import { cn } from "~/lib/utils"

export type SettingsSectionId =
  | "practice"
  | "biography"
  | "terminology"
  | "notetaking"
  | "ai"
  | "zoom"
  | "natmed"
  | "lab-ranges"

export type SetupStatus = "done" | "review" | "open"

export type SetupItem = {
  id: string
  label: string
  status: SetupStatus
  note: string
  section: SettingsSectionId
}

export type SettingsNavItem = {
  id: SettingsSectionId
  label: string
  hint: string
}

const STATUS: Record<
  SetupStatus,
  { label: string; className: string; Icon: typeof Check }
> = {
  done: { label: "Done", className: "text-emerald-700", Icon: Check },
  review: { label: "Review", className: "text-amber-700", Icon: Minus },
  open: { label: "Not yet", className: "text-destructive", Icon: Circle },
}

export function SettingsShell({
  sections,
  section,
  onSection,
  setup,
  related,
  children,
}: {
  sections: SettingsNavItem[]
  section: SettingsSectionId
  onSection: (id: SettingsSectionId) => void
  setup: React.ReactNode
  related: React.ReactNode
  children: React.ReactNode
}) {
  const [mobileRail, setMobileRail] = useState<"edit" | "setup" | "related">(
    "edit",
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="settings-grid min-h-0 flex-1 overflow-y-auto p-4 pb-24 lg:pb-4">
        <nav
          aria-label="Settings sections"
          className="min-w-0 lg:sticky lg:top-4"
        >
          <p className="text-muted-foreground mb-2 hidden text-xs font-medium tracking-wide uppercase lg:block">
            This page
          </p>
          <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
            {sections.map((item) => {
              const active = section === item.id
              return (
                <li key={item.id} className="shrink-0">
                  <button
                    type="button"
                    onClick={() => {
                      onSection(item.id)
                      setMobileRail("edit")
                    }}
                    className={cn(
                      "flex min-h-11 w-full items-center rounded-md px-3 py-2 text-left text-sm",
                      active
                        ? "bg-primary text-primary-foreground"
                        : "bg-card text-foreground hover:bg-muted lg:bg-transparent",
                    )}
                  >
                    <span>
                      <span className="block font-medium">{item.label}</span>
                      <span
                        className={cn(
                          "hidden text-xs lg:block",
                          active
                            ? "text-primary-foreground/70"
                            : "text-muted-foreground",
                        )}
                      >
                        {item.hint}
                      </span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </nav>

        <div
          className={cn(
            "min-w-0",
            mobileRail === "edit" ? "block" : "hidden lg:block",
          )}
        >
          {children}
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-4">
          <div
            className={cn(mobileRail === "setup" ? "block" : "hidden lg:block")}
          >
            {setup}
          </div>
          <div
            className={cn(
              mobileRail === "related" ? "block" : "hidden lg:block",
            )}
          >
            {related}
          </div>
        </aside>
      </div>

      <nav
        aria-label="Panels"
        className="bg-background/95 fixed inset-x-0 bottom-0 z-20 border-t p-2 lg:hidden"
      >
        <div className="mx-auto grid max-w-lg grid-cols-3 gap-1">
          {(
            [
              ["edit", "Edit"],
              ["setup", "Setup"],
              ["related", "Related"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setMobileRail(id)}
              className={cn(
                "flex h-12 items-center justify-center rounded-lg text-xs font-medium",
                mobileRail === id
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>
    </div>
  )
}

export function SetupCard({
  items,
  onJump,
}: {
  items: SetupItem[]
  onJump: (section: SettingsSectionId) => void
}) {
  const done = items.filter((item) => item.status === "done").length
  const remaining = items.length - done

  return (
    <section
      aria-label="Setup"
      className="bg-card rounded-lg border p-4 shadow-sm"
    >
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">Setup</h2>
          <p className="text-muted-foreground text-xs">
            {remaining === 0
              ? "Every setting here is done."
              : `${remaining} still to finish.`}
          </p>
        </div>
        <p className="text-muted-foreground text-sm tabular-nums">
          {done} of {items.length}
        </p>
      </div>
      <div className="bg-muted mb-3 h-1.5 overflow-hidden rounded-full">
        <div
          className="bg-primary h-full rounded-full"
          style={{
            width: `${items.length === 0 ? 0 : Math.round((done / items.length) * 100)}%`,
          }}
        />
      </div>
      <ul>
        {items.map((item) => {
          const meta = STATUS[item.status]
          const Icon = meta.Icon
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => onJump(item.section)}
                className="hover:bg-muted flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left"
              >
                <Icon
                  className={cn("mt-0.5 size-3.5 shrink-0", meta.className)}
                  strokeWidth={1.75}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="text-sm font-medium">{item.label}</span>
                    <span
                      className={cn(
                        "text-xs font-medium tracking-wide uppercase",
                        meta.className,
                      )}
                    >
                      {meta.label}
                    </span>
                  </span>
                  <span className="text-muted-foreground block text-xs leading-snug">
                    {item.note}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export function RelatedCard() {
  return (
    <section
      aria-label="Related"
      className="bg-card rounded-lg border p-4 shadow-sm"
    >
      <h2 className="text-base font-semibold">Related</h2>
      <p className="text-muted-foreground mt-1 mb-3 text-xs">
        Documents and destinations that are not this page.
      </p>
      <ul className="space-y-1">
        <li>
          <a
            href={BAA_PDF_URL}
            target="_blank"
            rel="noreferrer"
            className="hover:bg-muted block rounded-md px-2 py-2"
          >
            <span className="block text-sm font-medium">
              BAA agreement (PDF)
            </span>
            <span className="text-muted-foreground block text-xs">
              Download the Cosmaneura Inc BAA.
            </span>
          </a>
        </li>
        <li>
          <a
            href="/privacy-policy"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:bg-muted block rounded-md px-2 py-2"
          >
            <span className="block text-sm font-medium">Privacy Policy</span>
            <span className="text-muted-foreground block text-xs">
              What Zoom and note generation process.
            </span>
          </a>
        </li>
        <li>
          <a
            href="/terms-of-service"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:bg-muted block rounded-md px-2 py-2"
          >
            <span className="block text-sm font-medium">Terms of Service</span>
            <span className="text-muted-foreground block text-xs">
              Account and recording terms.
            </span>
          </a>
        </li>
        <li>
          <a href="/support" className="hover:bg-muted block rounded-md px-2 py-2">
            <span className="block text-sm font-medium">Support</span>
            <span className="text-muted-foreground block text-xs">
              Help for the practice.
            </span>
          </a>
        </li>
      </ul>
    </section>
  )
}
