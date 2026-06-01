// =============================================================================
// frontend/types/api.ts — Generated TS types from backend OpenAPI schema (D-27)
//
// @linguist-generated: true
//
// DO NOT EDIT MANUALLY. Regenerate with:
//   make gen-types
//
// This file is committed to the repo and checked for drift in CI:
//   frontend.yml job: make gen-types → git diff --exit-code frontend/types/api.ts
//   Any drift fails CI (D-27 schema drift gate).
//
// Phase 0: placeholder stub. Backend OpenAPI schema ships in Phase 3 (BACK-01).
// Phase 3 will replace this file with generated types from:
//   GET /openapi.json → openapi-typescript → this file
//
// Current placeholder exports the WS event envelope type matching D-26:
//   {seq, serverTs, chainTs, blockNumber, eventType, payload}
// =============================================================================

// ── WS event envelope (D-26) ──────────────────────────────────────────────────
export interface WsEnvelope<T = unknown> {
  seq: number;
  serverTs: string; // ISO 8601 UTC
  chainTs: string | null; // ISO 8601 UTC; null for purely operational events
  blockNumber: number | null;
  eventType: WsEventType;
  payload: T;
}

export type WsEventType =
  | "NavTick"
  | "TradeEvent"
  | "JournalEvent"
  | "ModelStatus"
  | "ArbOpp"
  | "SessionEvent"
  | "CurrentState";

// ── Placeholder types — Phase 3 replaces with generated types ────────────────
// These types match the Pydantic models in backend/src/backend/ws/models.py (IFACE-04)
// and are surfaced into the OpenAPI schema via dummy endpoints (Pattern 4).

export interface NavTick {
  vaultAddress: string;
  navPerToken1e18: string; // uint256 as string (bigint-safe)
  totalAssets: string;
  blockNumber: number;
}

export interface TradeEvent {
  vaultAddress: string;
  orderKey: string;
  action: "open" | "close" | "adjust";
  market: "ETH" | "BTC" | "SOL";
  side: "long" | "short";
  sizeUsd: string;
  leverage: number;
  tradeHash: string;
  blockNumber: number;
}

export interface ModelStatus {
  vaultAddress: string;
  status: "active" | "paused" | "malformed" | "settling";
  reason?: string;
}

export interface CurrentState {
  vaultAddress: string;
  nav: NavTick;
  recentTrades: TradeEvent[];
  modelStatus: ModelStatus;
  seq: number;
}
