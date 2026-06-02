// =============================================================================
// frontend/store/index.ts — Zustand store index (D-40..D-43)
//
// Phase 0: skeleton export only. Store slices ship in Phase 5 (FRONT-02).
//
// Architecture (D-40..D-43):
//   - One slice per vault (Claude, GPT-5.5, Gemini) — mirrors ws/vault/{addr}
//   - One global slice — mirrors ws/global
//   - All live push data (WS) flows through Zustand
//   - All REST / request-response data flows through TanStack Query
//   - NO cross-writing between the two stores (D-40 strict boundary)
//
// Phase 5 will add:
//   import { useVaultStore } from "./vaultStore";
//   import { useGlobalStore } from "./globalStore";
//   export { useVaultStore, useGlobalStore };
// =============================================================================

// Placeholder — Phase 5 (FRONT-02) adds vault + global slices
export {};
