// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {PerformanceOracle} from "../src/PerformanceOracle.sol";
import {JournalRegistry} from "../src/JournalRegistry.sol";
import {SessionFactory} from "../src/SessionFactory.sol";

/// @title DeployPhase1 — trAIder full Phase 1 stack deploy script (FACT-01)
/// @notice Deploys the complete Phase 1 on-chain stack:
///           1. PerformanceOracle
///           2. JournalRegistry
///           3. SessionFactory (wired to oracle + journal)
///           4. Transfers oracle + journal ownership to the factory
///           5. Calls factory.createSession to deploy 3 MTokenVaults, 3 SettlementContracts,
///              registers each vault on oracle + journal, wires setSettlement, and starts sessions —
///              all in ONE atomic transaction (FACT-01, Pitfall 6).
///
///         The vault share IS the tradeable mTOKEN (D-18, TOKEN-01).
///         No standalone mToken is deployed. The tickers (mCLA-S1/mGPT-S1/mGEM-S1) are
///         set as the vault ERC-4626 name/symbol at construction.
///
///         **Sepolia path check (Phase 3):** Run without --broadcast to compile-check the
///         full-stack deploy flow. Add --broadcast --rpc-url $SEPOLIA_RPC for a live run.
///
///         **Mainnet (Arbitrum One) notes:**
///           - Set PERPS_VENUE=gmx and provide the deployed GMXAdapter address as ADAPTER_ADDRESS.
///           - Chainlink mainnet feeds hardcoded below as defaults (D-12); override via env.
///           - SEQUENCER_FEED on Arbitrum One: 0xFdB631F5EE196F0ed6FAa767959853A9F217697D
///
///         **Sepolia notes:**
///           - No Chainlink sequencer uptime feed on Arbitrum Sepolia — set SEQUENCER_FEED=address(0).
///           - Set USE_SEPOLIA_STALENESS=true for the 6-hour staleness window on all feeds.
///           - PERPS_VENUE=mock — use a deployed MockPerps address as ADAPTER_ADDRESS.
///
///         **Security:** All addresses read from environment variables. No private keys or secrets
///         are hardcoded. The deployer's private key is passed via --private-key CLI flag or
///         PRIVATE_KEY env var (Foundry standard). gitleaks pre-commit hook enforces this.
///
/// @dev Usage:
///        forge build                                        # compile check (Phase 1 gate)
///        forge script script/01-Deploy.s.sol --sig run()   # dry run (no broadcast)
///        forge script script/01-Deploy.s.sol \
///          --rpc-url $SEPOLIA_RPC \
///          --broadcast \
///          --sig run()                                      # live Sepolia deploy
///
///      Environment variables (with Sepolia defaults):
///        USDC_ADDRESS        (required) USDC token address on target chain
///        ADAPTER_ADDRESS     (required) IPerpsAdapter address (MockPerps or GMXAdapter)
///        ORCHESTRATOR        (required) Orchestrator wallet address
///        OPERATOR            (required) Operator wallet address
///        ETH_FEED            (optional) Chainlink ETH/USD feed; defaults to Arbitrum One mainnet
///        BTC_FEED            (optional) Chainlink BTC/USD feed; defaults to Arbitrum One mainnet
///        SOL_FEED            (optional) Chainlink SOL/USD feed; defaults to Arbitrum One mainnet
///        SEQUENCER_FEED      (optional) Chainlink Arbitrum sequencer feed; default = address(0) (skip)
///        SESSION_DURATION    (optional) Session length in seconds; default = 259200 (72h)
///        INITIAL_CAPITAL     (optional) Per-vault seed capital in 6-dec USDC; default = 10_000e6
///        USE_SEPOLIA_STALENESS (optional) "true" enables 6h staleness window; default = false
contract DeployPhase1 is Script {
    // =========================================================================
    // Chainlink mainnet defaults (Arbitrum One, D-12)
    // =========================================================================

    /// @dev Chainlink ETH/USD on Arbitrum One. Heartbeat 3,600s → MAX_STALENESS_ETH 4,500s.
    address internal constant ARB_ONE_ETH_FEED = 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612;

    /// @dev Chainlink BTC/USD on Arbitrum One. Heartbeat 86,400s → MAX_STALENESS_BTC 90,000s.
    address internal constant ARB_ONE_BTC_FEED = 0x6ce185560a4963c47a8Ec16F4EF5d62A0000E708;

    /// @dev Chainlink SOL/USD on Arbitrum One. Heartbeat 86,400s → MAX_STALENESS_SOL 90,000s.
    address internal constant ARB_ONE_SOL_FEED = 0x24ceA4b8ce57cdA5058b924B9B9987992450590c;

    /// @dev Chainlink Arbitrum sequencer uptime feed on Arbitrum One.
    ///      Pass address(0) for Sepolia (no sequencer uptime feed on testnet — D-11).
    address internal constant ARB_ONE_SEQUENCER_FEED = 0xFdB631F5EE196F0ed6FAa767959853A9F217697D;

    // =========================================================================
    // Run
    // =========================================================================

    /// @notice Deploy the full Phase 1 stack and call createSession.
    /// @dev All sensitive config is read from environment variables.
    ///      No private keys, operator seeds, or secrets are hardcoded here.
    ///      The gitleaks pre-commit hook enforces this at the repo level.
    function run() external {
        // ── Read required addresses from env ──────────────────────────────────
        address usdc = vm.envAddress("USDC_ADDRESS");
        address adapter = vm.envAddress("ADAPTER_ADDRESS");
        address orchestrator = vm.envAddress("ORCHESTRATOR");
        address operator = vm.envAddress("OPERATOR");

        // ── Read optional config with Arbitrum One mainnet defaults ──────────
        address ethFeed = vm.envOr("ETH_FEED", ARB_ONE_ETH_FEED);
        address btcFeed = vm.envOr("BTC_FEED", ARB_ONE_BTC_FEED);
        address solFeed = vm.envOr("SOL_FEED", ARB_ONE_SOL_FEED);
        // Sepolia: SEQUENCER_FEED="" or "0x0000..." → address(0) skips the sequencer check (D-11)
        address sequencerFeed = vm.envOr("SEQUENCER_FEED", address(0));

        uint256 sessionDuration = vm.envOr("SESSION_DURATION", uint256(259_200)); // 72 hours
        uint256 initialCapital = vm.envOr("INITIAL_CAPITAL", uint256(10_000e6)); // $10k in USDC
        bool useSepoliaStaleness = vm.envOr("USE_SEPOLIA_STALENESS", false);

        vm.startBroadcast();

        // ── Step 1: Deploy PerformanceOracle ─────────────────────────────────
        PerformanceOracle oracle = new PerformanceOracle();
        console2.log("PerformanceOracle deployed:", address(oracle));

        // ── Step 2: Deploy JournalRegistry ───────────────────────────────────
        JournalRegistry journal = new JournalRegistry();
        console2.log("JournalRegistry deployed:", address(journal));

        // ── Step 3: Deploy SessionFactory ────────────────────────────────────
        //    The factory stores static config (feeds, sequencer, orchestrator, operator,
        //    initialCapital, useSepoliaStaleness) at construction. These are shared across
        //    all vault deploys within a createSession call.
        SessionFactory factory = new SessionFactory(
            address(oracle),
            address(journal),
            sequencerFeed,
            ethFeed,
            btcFeed,
            solFeed,
            orchestrator,
            operator,
            initialCapital,
            useSepoliaStaleness
        );
        console2.log("SessionFactory deployed:", address(factory));

        // ── Step 4: Transfer oracle + journal ownership to the factory ────────
        //    registerVault is owner-gated on both registries (Plans 02/03).
        //    The factory must own them before calling createSession.
        oracle.transferOwnership(address(factory));
        journal.transferOwnership(address(factory));
        console2.log("Ownership transferred: oracle + journal -> factory");

        // ── Step 5: createSession — one atomic tx deploys 3 MTokenVaults ─────
        //    Each vault's ERC-4626 share IS the tradeable mTOKEN (D-18, TOKEN-01).
        //    Tickers: mCLA-S1 / mGPT-S1 / mGEM-S1 (set in SessionFactory.createSession).
        //    address(0) arbitrage: Phase 4 registration deferred (FACT-01 Phase 1 scope).
        address[3] memory vaults = factory.createSession(
            usdc,
            adapter,
            address(0), // arbitrage placeholder (Phase 1; Phase 4 wires ArbitragePrimitive)
            sessionDuration
        );

        // ── Step 6: Log deployed addresses ───────────────────────────────────
        console2.log("=== trAIder Phase 1 Deploy Complete ===");
        console2.log("PerformanceOracle : ", address(oracle));
        console2.log("JournalRegistry   : ", address(journal));
        console2.log("SessionFactory    : ", address(factory));
        console2.log("--- Session vaults (share IS the mTOKEN - D-18) ---");
        console2.log("mCLA-S1 vault (Claude)  : ", vaults[0]);
        console2.log("mGPT-S1 vault (GPT)     : ", vaults[1]);
        console2.log("mGEM-S1 vault (Gemini)  : ", vaults[2]);
        console2.log("Session duration (s)    : ", sessionDuration);
        console2.log("Initial capital (USDC)  : ", initialCapital);
        console2.log("Use Sepolia staleness   : ", useSepoliaStaleness);

        vm.stopBroadcast();
    }
}
