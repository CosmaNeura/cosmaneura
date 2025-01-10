import { Outlet, useOutletContext } from "react-router"

export default function NotesLayout() {
  return <Outlet context={useOutletContext()} />
}
