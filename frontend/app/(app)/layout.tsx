// =============================================================================
// frontend/app/(app)/layout.tsx — app-shell layout (Coliseum / Model / Arbitrage
// / Portfolio / Verifier). Renders the design's `.app` grid: sticky sidebar +
// scrolling main. Each page supplies its own <header class="topbar"> + .app-body.
// =============================================================================

import { Sidebar } from "@/components/app/Sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="app">
      <Sidebar />
      <main className="app-main">{children}</main>
    </div>
  );
}
