import { useLocation, useNavigation as useRouterNavigation } from "react-router"

export function useNavigation() {
  const navigation = useRouterNavigation()
  const location = useLocation()

  return {
    state: navigation.state,
    direction: location.state?.direction || "none",
  }
}
