import { type UseComboboxProps, useCombobox } from "downshift"
import { type PropsWithChildren, useCallback, useEffect, useState } from "react"

import { Popover } from "~/components/ui/popover"

import { ComboboxContext } from "./context"
import type { ComboboxItemBase } from "./types"

const { stateChangeTypes } = useCombobox

const defaultFilter = (inputValue: string, items: ComboboxItemBase[]) =>
  items.filter(
    (item) =>
      !inputValue ||
      item.label.toLowerCase().includes(inputValue.toLowerCase()),
  )

export type ComboboxProps = PropsWithChildren<{
  value?: string | null
  onValueChange?: (value: string | null) => void
  onSelectedItemChange?: (item: ComboboxItemBase | null) => void
  filterItems?: (
    inputValue: string,
    items: ComboboxItemBase[],
  ) => ComboboxItemBase[]
}>

export const ComboBox = ({
  value,
  onValueChange,
  onSelectedItemChange,
  filterItems = defaultFilter,
  children,
}: ComboboxProps) => {
  const [items, setItems] = useState<ComboboxItemBase[]>([]),
    [filteredItems, setFilteredItems] = useState<ComboboxItemBase[]>(items)
  const [openedOnce, setOpenedOnce] = useState(false)

  const stateReducer = useCallback<
    NonNullable<UseComboboxProps<ComboboxItemBase>["stateReducer"]>
  >(
    (prev, { type, changes }) => {
      switch (type) {
        case stateChangeTypes.InputChange: {
          const filteredEnabledItems = filterItems(
            changes.inputValue || prev.inputValue,
            items,
          ).filter(({ disabled }) => !disabled)
          const highlightedIndex =
            typeof changes.highlightedIndex === "number"
              ? changes.highlightedIndex
              : prev.highlightedIndex

          return {
            ...changes,
            highlightedIndex:
              changes.inputValue &&
              filteredEnabledItems.length > 0 &&
              highlightedIndex < 0
                ? 0
                : changes.highlightedIndex,
          }
        }

        case stateChangeTypes.InputBlur:
        case stateChangeTypes.InputClick:
        case stateChangeTypes.InputKeyDownEnter:
        case stateChangeTypes.InputKeyDownEscape: {
          // occurs when a item is selected
          if (changes.isOpen || !prev.isOpen) {
            return {
              ...changes,
              inputValue: prev.inputValue,
              selectedItem: prev.selectedItem,
            }
          }

          // occurs if nothing is selected and no input exists
          if (!prev.inputValue && prev.highlightedIndex < 0) {
            return { ...changes, inputValue: "", selectedItem: null }
          }

          // no item is selected, but there is an input value, let's assume a new item
          if (prev.inputValue) {
            return {
              ...changes,
              inputValue: prev.inputValue,
              selectedItem: {
                label: prev.inputValue,
                // this new_item value is arbitrary and must be properly handled
                // by the user's `onSelectedItemChange` handler, otherwise new_item will be the value set in the form element
                value: "new_item",
              },
            }
          }

          const inputValue =
            changes.selectedItem?.label || prev.selectedItem?.label || ""
          return { ...changes, inputValue }
        }

        default:
          return changes
      }
    },
    [filterItems, items],
  )

  const {
    getInputProps,
    getItemProps,
    getMenuProps,
    highlightedIndex,
    inputValue,
    isOpen,
    selectedItem,
    selectItem,
    setInputValue,
  } = useCombobox({
    items: filteredItems,
    itemToString: (item) => (item ? item.label : ""),
    isItemDisabled: (item) => item.disabled ?? false,

    selectedItem:
      typeof value !== "undefined"
        ? items.find((item) => item.value === value) || null
        : undefined,
    onSelectedItemChange: ({ selectedItem }) => {
      onSelectedItemChange?.(selectedItem)
      return onValueChange?.(selectedItem?.value || null)
    },

    stateReducer,
  })

  useEffect(() => {
    if (isOpen && !openedOnce) setOpenedOnce(isOpen)
  }, [isOpen, openedOnce])

  useEffect(() => {
    setFilteredItems(filterItems(inputValue, items))
  }, [filterItems, inputValue, items])

  return (
    <ComboboxContext.Provider
      value={{
        filteredItems,
        getInputProps,
        getItemProps,
        getMenuProps,
        highlightedIndex,
        inputValue,
        isOpen,
        items,
        onItemsChange: setItems,
        onValueChange,
        openedOnce,
        selectedItem,
        selectItem,
        setInputValue,
      }}
    >
      <Popover open={isOpen}>{children}</Popover>
    </ComboboxContext.Provider>
  )
}
