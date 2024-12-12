import { useNavigation as useRouterNavigation, useLocation } from 'react-router-dom';

export function useNavigation() {
  const navigation = useRouterNavigation();
  const location = useLocation();
  
  return {
    state: navigation.state,
    direction: location.state?.direction || 'none'
  };
}