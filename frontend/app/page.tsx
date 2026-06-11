// =============================================================================
// frontend/app/page.tsx — root → Coliseum.
//
// The Coliseum (live 3-model standings) is the primary view per the Claude Design
// handoff. The marketing landing (index.html) can be ported to "/" later; for the
// demo, "/" lands straight on the live arena.
// =============================================================================

import { redirect } from "next/navigation";

export default function Home() {
  redirect("/coliseum");
}
