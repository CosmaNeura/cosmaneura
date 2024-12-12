import { Outlet, useLocation, useNavigate } from "react-router"

import { RedirectToSignIn, SignedIn, SignedOut } from "@clerk/clerk-react"

export default function AuthenticatedLayout() {
  const navigate = useNavigate()
  const location = useLocation()

  // Derive current page from location
  const getCurrentPage = (pathname: string) => {
    switch (pathname) {
      case "/":
        return "intake"
      case "/notes":
        return "notetaking"
      case "/settings":
        return "settings"
      default:
        return "intake"
    }
  }

  const currentPage = getCurrentPage(location.pathname)

  const handleNavigate = (page: string) => {
    switch (page) {
      case "intake":
        navigate("/intake", { state: { direction: "left" } })
        break
      case "notetaking":
        navigate("/notes", { state: { direction: "middle" } })
        break
      case "settings":
        navigate("/settings", { state: { direction: "right" } })
        break
    }
  }

  return (
    <>
      <Outlet context={{ onNavigate: handleNavigate, currentPage }} />
    </>
  )
}
