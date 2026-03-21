import { type ComponentPropsWithoutRef, useMemo } from "react"

import { CircleIcon } from "lucide-react"

import { cn } from "~/lib/utils"

import { useComboboxContext } from "./context"
import type { ComboboxItemBase } from "./types"

export type ComboboxItemProps = ComboboxItemBase &
  ComponentPropsWithoutRef<"li">

export const ComboboxItem = ({
  label,
  value,
  disabled,
  className,
  children,
  ...props
}: ComboboxItemProps) => {
  const { filteredItems, getItemProps, selectedItem } = useComboboxContext()

  const isSelected = selectedItem?.value === value
  const item = useMemo(
    () => ({ disabled, label, value }),
    [disabled, label, value],
  )
  const index = (filteredItems || []).findIndex(
    (item) => item.value.toLowerCase() === value.toLowerCase(),
  )
  if (index < 0) return null

  return (
    <li
      {...props}
      data-index={index}
      className={cn(
        `aria-selected:bg-accent aria-selected:text-accent-foreground relative flex cursor-default flex-col rounded-sm px-3 py-1.5 select-none aria-disabled:pointer-events-none aria-disabled:opacity-50`,
        !children && "ps-8",
        className,
      )}
      {...getItemProps?.({ item, index })}
    >
      {children || (
        <>
          <span className="text-foreground text-sm">{label}</span>
          {isSelected && (
            <span className="absolute start-3 top-0 flex h-full items-center justify-center">
              <CircleIcon className="size-2 fill-current" />
            </span>
          )}
        </>
      )}
    </li>
  )
}
