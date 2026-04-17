import { Link } from "react-router"

export function AppFooter() {
  return (
    <footer className="flex shrink-0 justify-center gap-6 border-t bg-background px-4 py-2 text-xs">
      <Link to="/privacy-policy" className="text-muted-foreground hover:text-foreground transition-colors">
        Privacy Policy
      </Link>
      <Link to="/terms-of-service" className="text-muted-foreground hover:text-foreground transition-colors">
        Terms of Service
      </Link>
      <Link to="/support" className="text-muted-foreground hover:text-foreground transition-colors">
        Support
      </Link>
    </footer>
  )
}
