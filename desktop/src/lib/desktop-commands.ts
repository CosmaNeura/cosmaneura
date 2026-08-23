import { invoke } from "@tauri-apps/api/core"
import { listen, type UnlistenFn } from "@tauri-apps/api/event"

import { DESKTOP_STATE_EVENT } from "../constants"
import {
  desktopSnapshotSchema,
  type DesktopSnapshot,
} from "./desktop-state"

function parseSnapshot(value: unknown): DesktopSnapshot {
  return desktopSnapshotSchema.parse(value)
}

export async function getDesktopSnapshot(): Promise<DesktopSnapshot> {
  return parseSnapshot(await invoke("get_desktop_snapshot"))
}

export async function openProviderPlatform(): Promise<DesktopSnapshot> {
  return parseSnapshot(await invoke("open_provider_platform"))
}

export async function startCapture(): Promise<DesktopSnapshot> {
  return parseSnapshot(await invoke("start_capture"))
}

export async function stopCapture(): Promise<DesktopSnapshot> {
  return parseSnapshot(await invoke("stop_capture"))
}

export async function listenForDesktopSnapshot(
  onSnapshot: (snapshot: DesktopSnapshot) => void,
): Promise<UnlistenFn> {
  return listen<unknown>(DESKTOP_STATE_EVENT, (event) => {
    const parsed = desktopSnapshotSchema.safeParse(event.payload)
    if (parsed.success) onSnapshot(parsed.data)
  })
}
