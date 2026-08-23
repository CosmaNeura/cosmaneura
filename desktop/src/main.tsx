import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import { App } from "./app"
import "./styles.css"

const rootElement = document.getElementById("root")

if (!rootElement) throw new Error("desktop root element is unavailable")

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
