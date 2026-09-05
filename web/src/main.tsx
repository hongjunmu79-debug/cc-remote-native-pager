import { createRoot } from "react-dom/client";
import "./index.css";
import "./App.css";
import "./mobile-webview-compat.css";
import App from "./App";
import { PairingPage } from "./components/PairingPage";
import { ErrorBoundary } from "./ErrorBoundary";
import { useMobileViewport } from "./use-mobile-viewport";

export function RootApp() {
  useMobileViewport();
  return new URLSearchParams(window.location.search).get("pair") === "1" ? <PairingPage /> : <App />;
}

createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <RootApp />
  </ErrorBoundary>
);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js");
  });
}
