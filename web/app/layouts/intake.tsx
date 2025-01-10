import { Outlet, useOutletContext } from "react-router"

export default function IntakeLayout() {
  return <Outlet context={useOutletContext()} />
}
