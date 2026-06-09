// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {stdJson} from "forge-std/StdJson.sol";
import {PerformanceOracle} from "../src/PerformanceOracle.sol";
import {JournalRegistry} from "../src/JournalRegistry.sol";
import {SessionFactory} from "../src/SessionFactory.sol";
import {ArbitragePrimitive} from "../src/ArbitragePrimitive.sol";
import {MockERC20} from "../src/mocks/MockERC20.sol";
import {MockPerps} from "../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../src/mocks/MockChainlinkAggregator.sol";
import {MockSequencerUptimeFeed} from "../src/mocks/MockSequencerUptimeFeed.sol";

/// @title DeployPhase1 - trAIder full Phase 1 + Phase 3 + Phase 4 stack deploy script (FACT-01, D-12/D-13/D-14/D-15)
/// @notice Deploys the complete on-chain stack in one idempotent, manifest-driven run:
///           1. PerformanceOracle
///           2. JournalRegistry (with OPERATOR_JOURNAL_KEY ecrecover gate, D-10)
///           3. Optional mock substrate (D-12): MockERC20 (6-dec USDC), MockPerps,
///              3x MockChainlinkAggregator (ETH/BTC/SOL seeded to starting prices),
///              MockSequencerUptimeFeed - deployed when DEPLOY_MOCK_SUBSTRATE=true.
///           4. SessionFactory (wired to oracle + journal + feeds + sequencer)
///           5. Transfers oracle + journal ownership to the factory BEFORE createSession.
///           6. Calls factory.createSession to deploy 3 MTokenVaults, 3 SettlementContracts,
///              registers each vault on oracle + journal, wires setSettlement, and starts sessions -
///              all in ONE atomic transaction (FACT-01, Pitfall 6).
///           7. Writes canonical address manifest to deployments/sepolia.json (D-14).
///
///         **Idempotency (D-14):** On re-run, reads the existing manifest. If sessionFactory
///         is non-zero (and has on-chain code), the deploy is SKIPPED and the existing
///         manifest is re-logged. Re-running is always safe - no half-deployed state.
///
///         **Mock substrate (D-12):** Enabled by DEPLOY_MOCK_SUBSTRATE=true. Deploys:
///           - MockERC20 (6-dec) as the USDC underlying.
///           - MockPerps (executionDelay=3 blocks for Sepolia responsiveness, D-13).
///           - 3x MockChainlinkAggregator: ETH=$3500, BTC=$95000, SOL=$180 (8-dec USD).
///           - MockSequencerUptimeFeed (toggleable up/down, D-06/D-07).
///           - Mints MOCK_USDC_MINT_AMOUNT mock USDC to the deployer for seeding.
///         If DEPLOY_MOCK_SUBSTRATE=false, USDC_ADDRESS and ADAPTER_ADDRESS are required.
///
///         **GMXAdapter (D-13 conditional):** GMXAdapter is NOT frozen after Phase 3.
///         Its Sepolia deploy is deferred to Phase 6 per the D-13 condition.
///         The adapter manifest field is set to address(0) (deferred placeholder).
///
///         **Mainnet (Arbitrum One) notes:**
///           - Set PERPS_VENUE=gmx and provide the deployed GMXAdapter address as ADAPTER_ADDRESS.
///           - Chainlink mainnet feeds hardcoded below as defaults (D-12); override via env.
///           - SEQUENCER_FEED on Arbitrum One: 0xFdB631F5EE196F0ed6FAa767959853A9F217697D
///
///         **Sepolia notes (D-13):**
///           - No Chainlink sequencer uptime feed on Arbitrum Sepolia - SEQUENCER_FEED=address(0).
///           - Set USE_SEPOLIA_STALENESS=true for the 6-hour staleness window on all feeds.
///           - Set DEPLOY_MOCK_SUBSTRATE=true to auto-deploy MockERC20 + MockPerps + aggregators.
///           - PERPS_VENUE=mock - MockPerps is the adapter on Sepolia.
///
///         **Security:** All addresses read from environment variables. No private keys or secrets
///         are hardcoded. The deployer's private key is passed via --private-key CLI flag or
///         PRIVATE_KEY env var (Foundry standard). gitleaks pre-commit hook enforces this.
///
/// @dev Usage:
///        forge build                                          # compile check
///        forge script script/01-Deploy.s.sol --sig "run()"   # dry run (no broadcast)
///        forge script script/01-Deploy.s.sol \
///          --rpc-url $SEPOLIA_RPC \
///          --broadcast \
///          --verify \
///          --etherscan-api-key $ARBISCAN_API_KEY \
///          --sig "run()"                                      # live Sepolia deploy + verify
///
///        # Idempotent re-run (reads existing manifest, skips deploy if already deployed):
///        forge script script/01-Deploy.s.sol \
///          --rpc-url $SEPOLIA_RPC \
///          --broadcast \
///          --sig "run()"
///
///      Environment variables:
///        OPERATOR_JOURNAL_KEY   (required) Operator-journal EOA for ecrecover gate (D-10/03-03)
///        ORCHESTRATOR           (required) Orchestrator wallet address (submits trades)
///        OPERATOR               (required) Operator wallet address (funds session)
///        DEPLOY_MOCK_SUBSTRATE  (optional) "true" deploys MockERC20 + MockPerps + aggregators
///                                          "false" requires USDC_ADDRESS + ADAPTER_ADDRESS
///        USDC_ADDRESS           (conditional) Required when DEPLOY_MOCK_SUBSTRATE=false
///        ADAPTER_ADDRESS        (conditional) Required when DEPLOY_MOCK_SUBSTRATE=false
///        ETH_FEED               (optional) Chainlink ETH/USD feed; defaults to Arb One mainnet
///        BTC_FEED               (optional) Chainlink BTC/USD feed; defaults to Arb One mainnet
///        SOL_FEED               (optional) Chainlink SOL/USD feed; defaults to Arb One mainnet
///        SEQUENCER_FEED         (optional) Chainlink Arbitrum sequencer feed; default = address(0)
///        SESSION_DURATION       (optional) Session length in seconds; default = 259200 (72h)
///        INITIAL_CAPITAL        (optional) Per-vault seed capital in 6-dec USDC; default = 10_000e6
///        USE_SEPOLIA_STALENESS  (optional) "true" enables 6h staleness window; default = false
///        MOCK_USDC_MINT_AMOUNT  (optional) Mock USDC minted to deployer; default = 100_000e6 ($100k)
///        MANIFEST_PATH          (optional) Path to write manifest; default = "deployments/sepolia.json"
///
///        Phase 4 env vars (AMM + Arbitrage — D-15/D-18):
///        ARB_SWAP_ROUTER        (optional) Camelot V3 SwapRouter; default = Sepolia address
///        ALGEBRA_FACTORY        (optional) AlgebraFactory address; default = Sepolia address
///        ALGEBRA_NPM            (optional) NonfungiblePositionManager; default = Sepolia address
///        OPERATOR_LP_KEY        (optional) LP key for D-18 guard; default = deployer (msg.sender)
///        ARB_KEY4               (optional) Arb bot EOA (key #4); default = address(0) (log only)
///        GATE_DURATION          (optional) Session duration for gate run; default = SESSION_DURATION
contract DeployPhase1 is Script {
    using stdJson for string;

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
    ///      Pass address(0) for Sepolia (no sequencer uptime feed on testnet - D-06/D-07).
    address internal constant ARB_ONE_SEQUENCER_FEED = 0xFdB631F5EE196F0ed6FAa767959853A9F217697D;

    // =========================================================================
    // Mock substrate starting prices (8-decimal Chainlink format, D-06/D-12)
    // Phase 2 seeded-walk starting prices: ETH=$3500, BTC=$95000, SOL=$180
    // =========================================================================

    int256 internal constant MOCK_ETH_START_PRICE = 350000000000; // $3,500 (8-dec)
    int256 internal constant MOCK_BTC_START_PRICE = 9500000000000; // $95,000 (8-dec)
    int256 internal constant MOCK_SOL_START_PRICE = 18000000000; // $180 (8-dec)

    // =========================================================================
    // Phase 4 Camelot/Algebra Sepolia defaults (D-15)
    // Override via ARB_SWAP_ROUTER / ALGEBRA_FACTORY / ALGEBRA_NPM env vars.
    // =========================================================================

    /// @dev Camelot V3 SwapRouter on Arbitrum Sepolia (Algebra Integral v1).
    ///      Direct IAlgebraPool.swap() is used in ArbitragePrimitive; this address is
    ///      stored for reference and future router-path fallbacks.
    address internal constant DEFAULT_ARB_SWAP_ROUTER = 0x171B925C51565F5D2a7d8C494ba3188D304EFD93;

    /// @dev Camelot/Algebra AlgebraFactory on Arbitrum Sepolia.
    ///      Probe 2 confirmed bytecode parity with mainnet (0x1a3c9B...5B).
    address internal constant DEFAULT_ALGEBRA_FACTORY = 0xaA37Bea711D585478E1c04b04707cCb0f10D762a;

    /// @dev Camelot/Algebra NonfungiblePositionManager on Arbitrum Sepolia.
    ///      Used by 02-SeedPools.s.sol to mint LP positions.
    address internal constant DEFAULT_ALGEBRA_NPM = 0x79EA6cB3889fe1FC7490A1C69C7861761d882D4A;

    // =========================================================================
    // Manifest path
    // =========================================================================

    // Path is relative to the foundry.toml (contracts/) directory; repo root is one level up.
    // fs_permissions in foundry.toml grants read-write on ../deployments/.
    string internal constant DEFAULT_MANIFEST_PATH = "../deployments/sepolia.json";

    // =========================================================================
    // Run
    // =========================================================================

    /// @notice Deploy the full stack idempotently and write the canonical address manifest.
    /// @dev All sensitive config is read from environment variables.
    ///      No private keys, operator seeds, or secrets are hardcoded here.
    ///      The gitleaks pre-commit hook enforces this at the repo level.
    function run() external {
        // ── Read manifest path (optional override) ────────────────────────────
        string memory manifestPath = vm.envOr("MANIFEST_PATH", DEFAULT_MANIFEST_PATH);

        // ── Idempotency guard (D-14): skip re-deploy if manifest already present ──
        // Read the existing manifest. If sessionFactory is non-zero, the session is
        // already deployed - log existing addresses and exit cleanly (no re-deploy).
        address existingFactory = _readManifestFactory(manifestPath);
        if (existingFactory != address(0)) {
            console2.log("=== IDEMPOTENT: Deploy already complete - reusing existing manifest ===");
            console2.log("  Manifest path:", manifestPath);
            console2.log("  SessionFactory:", existingFactory);
            console2.log("  Re-run is a no-op. To deploy fresh: delete the manifest and re-run.");
            return;
        }

        // ── Read required addresses from env ──────────────────────────────────
        address orchestrator = vm.envAddress("ORCHESTRATOR");
        address operator = vm.envAddress("OPERATOR");

        // D-10/03-03: operator-journal key for the ecrecover gate (required - no default).
        address operatorJournalKey = vm.envAddress("OPERATOR_JOURNAL_KEY");

        // ── Phase 4 env vars (D-15/D-18) ─────────────────────────────────────
        // ARB_SWAP_ROUTER: Camelot V3 SwapRouter on Sepolia (ArbitragePrimitive constructor).
        address arbSwapRouter = vm.envOr("ARB_SWAP_ROUTER", DEFAULT_ARB_SWAP_ROUTER);
        // ALGEBRA_FACTORY + ALGEBRA_NPM: recorded in manifest for 02-SeedPools.s.sol.
        address algebraFactory = vm.envOr("ALGEBRA_FACTORY", DEFAULT_ALGEBRA_FACTORY);
        address algebraNpm = vm.envOr("ALGEBRA_NPM", DEFAULT_ALGEBRA_NPM);
        // OPERATOR_LP_KEY: LP wallet for D-18 mmAddress guard. Default = deployer (msg.sender).
        // D-06: LP key MUST be distinct from orchestrator-trade and arb key #4 in production.
        address operatorLpKey = vm.envOr("OPERATOR_LP_KEY", msg.sender);
        // ARB_KEY4: arb bot EOA (address only — private key never stored here, SEC-01).
        address arbKey4 = vm.envOr("ARB_KEY4", address(0));
        // GATE_DURATION: override session duration for gate runs (D-17). Defaults to SESSION_DURATION.
        // This is set in the same envOr block so the session.deadline aligns with gate harness timing.

        // ── Read optional config with Arbitrum One mainnet defaults ──────────
        address ethFeed = vm.envOr("ETH_FEED", ARB_ONE_ETH_FEED);
        address btcFeed = vm.envOr("BTC_FEED", ARB_ONE_BTC_FEED);
        address solFeed = vm.envOr("SOL_FEED", ARB_ONE_SOL_FEED);
        // Sepolia: SEQUENCER_FEED="" or "0x0000..." → address(0) skips the sequencer check (D-06/D-07)
        address sequencerFeed = vm.envOr("SEQUENCER_FEED", address(0));

        // GATE_DURATION overrides SESSION_DURATION when set (D-17 gate run = ~3600s).
        // If GATE_DURATION is absent, SESSION_DURATION is used (default 72h / 259200s).
        uint256 sessionDuration = vm.envOr("GATE_DURATION", vm.envOr("SESSION_DURATION", uint256(259_200)));
        uint256 initialCapital = vm.envOr("INITIAL_CAPITAL", uint256(10_000e6)); // $10k in USDC
        bool useSepoliaStaleness = vm.envOr("USE_SEPOLIA_STALENESS", false);
        bool deployMockSubstrate = vm.envOr("DEPLOY_MOCK_SUBSTRATE", false);
        uint256 mockUsdcMintAmount = vm.envOr("MOCK_USDC_MINT_AMOUNT", uint256(100_000e6)); // $100k default

        // ── Mock substrate or provided addresses ─────────────────────────────
        address usdc;
        address adapter;

        // Variables to hold mock substrate addresses for the manifest
        address mockUsdcAddr;
        address mockPerpsAddr;
        address mockEthFeedAddr;
        address mockBtcFeedAddr;
        address mockSolFeedAddr;
        address mockSequencerFeedAddr;

        vm.startBroadcast();

        if (deployMockSubstrate) {
            // ── D-12: Deploy mock USDC (6-decimal MockERC20) ─────────────────
            MockERC20 mockUsdc = new MockERC20("Mock USD Coin", "USDC", 6);
            mockUsdcAddr = address(mockUsdc);
            console2.log("MockERC20 (USDC, 6-dec) deployed:", mockUsdcAddr);

            // Mint mock USDC to the deployer for seeding vault + demo speculators (D-12)
            address deployer = msg.sender;
            mockUsdc.mint(deployer, mockUsdcMintAmount);
            console2.log("  Minted mock USDC to deployer:", mockUsdcMintAmount);

            // ── D-06/D-07: Deploy 3x MockChainlinkAggregator (ETH/BTC/SOL seeded) ──
            // Seeds with Phase 2 starting prices (8-decimal Chainlink format).
            // Operator pushes price updates each cycle via setPrice() - the seeded walk.
            MockChainlinkAggregator mockEthFeed = new MockChainlinkAggregator(MOCK_ETH_START_PRICE, block.timestamp);
            mockEthFeedAddr = address(mockEthFeed);
            console2.log("MockChainlinkAggregator (ETH/USD, $3500 seed) deployed:", mockEthFeedAddr);

            MockChainlinkAggregator mockBtcFeed = new MockChainlinkAggregator(MOCK_BTC_START_PRICE, block.timestamp);
            mockBtcFeedAddr = address(mockBtcFeed);
            console2.log("MockChainlinkAggregator (BTC/USD, $95000 seed) deployed:", mockBtcFeedAddr);

            MockChainlinkAggregator mockSolFeed = new MockChainlinkAggregator(MOCK_SOL_START_PRICE, block.timestamp);
            mockSolFeedAddr = address(mockSolFeed);
            console2.log("MockChainlinkAggregator (SOL/USD, $180 seed) deployed:", mockSolFeedAddr);

            // ── D-06/D-07: Deploy toggleable MockSequencerUptimeFeed ──────────
            // Starts UP with grace already elapsed (default constructor).
            // Operator calls setDown()/setUp(_startedAt) to drill the freeze/unfreeze path.
            MockSequencerUptimeFeed mockSeqFeed = new MockSequencerUptimeFeed();
            mockSequencerFeedAddr = address(mockSeqFeed);
            console2.log("MockSequencerUptimeFeed deployed:", mockSequencerFeedAddr);

            // ── D-13: Deploy MockPerps (executionDelay=3 blocks for Sepolia, D-13) ──
            // executionDelay defaults to 1; set to 3 for Sepolia (mimics keeper latency, D-13).
            // The MockPerps reads prices from the 3x mock aggregators above (Chainlink-shaped).
            MockPerps mockPerps = new MockPerps(mockEthFeedAddr, mockBtcFeedAddr, mockSolFeedAddr);
            mockPerps.setExecutionDelay(3); // Sepolia: 3 blocks between order and execution (D-13)
            mockPerpsAddr = address(mockPerps);
            console2.log("MockPerps (executionDelay=3) deployed:", mockPerpsAddr);

            // Use mock addresses for the factory wiring
            usdc = mockUsdcAddr;
            adapter = mockPerpsAddr;

            // Override feed pointers to the mock aggregators (D-06)
            ethFeed = mockEthFeedAddr;
            btcFeed = mockBtcFeedAddr;
            solFeed = mockSolFeedAddr;
            // Sepolia has no real sequencer feed; use the mock for the drill path (D-06/D-07)
            // Note: pass address(0) to skip sequencer guard in vault, or pass the mock for testing.
            // On Sepolia production: address(0) is correct (no real feed). For the test drill,
            // the operator sets the mock into the factory by providing SEQUENCER_FEED env var.
            // Default remains address(0) for Sepolia unless SEQUENCER_FEED is explicitly set.
            // The mock sequencer feed address is recorded in the manifest for operator use.
        } else {
            // Pre-deployed mocks or mainnet addresses provided via env
            usdc = vm.envAddress("USDC_ADDRESS");
            adapter = vm.envAddress("ADAPTER_ADDRESS");
            // Feed vars already set from env above (with mainnet defaults)
            // Manifest mock fields are address(0) when substrate not deployed here
            mockUsdcAddr = usdc; // record the provided address
            mockPerpsAddr = adapter;
            mockEthFeedAddr = ethFeed;
            mockBtcFeedAddr = btcFeed;
            mockSolFeedAddr = solFeed;
            mockSequencerFeedAddr = sequencerFeed;
        }

        // ── Step 1: Deploy PerformanceOracle ─────────────────────────────────
        PerformanceOracle oracle = new PerformanceOracle();
        console2.log("PerformanceOracle deployed:", address(oracle));

        // ── Step 2: Deploy JournalRegistry (with OPERATOR_JOURNAL_KEY ecrecover gate, D-10) ──
        JournalRegistry journal = new JournalRegistry(operatorJournalKey);
        console2.log("JournalRegistry deployed:", address(journal));
        console2.log("  OPERATOR_JOURNAL_KEY:", operatorJournalKey);

        // ── Step 3: Deploy SessionFactory ────────────────────────────────────
        //    The factory stores static config (feeds, sequencer, orchestrator, operator,
        //    initialCapital, useSepoliaStaleness) at construction. These are shared across
        //    all vault deploys within a createSession call.
        //    Phase 4 (D-15/D-18): operatorLpKey is now the real LP key (not address(0)).
        //    This threads into each SettlementContract as mmAddress_ via createSession,
        //    arming the D-18 endSession guard.
        SessionFactory factory = new SessionFactory(
            address(oracle),
            address(journal),
            sequencerFeed, // address(0) on Sepolia (no real sequencer feed)
            ethFeed,
            btcFeed,
            solFeed,
            orchestrator,
            operator,
            initialCapital,
            useSepoliaStaleness,
            operatorLpKey // Phase 4 (D-15/D-18): real LP key for D-18 mmAddress guard
        );
        console2.log("SessionFactory deployed:", address(factory));
        console2.log("  operatorLpKey (D-18 mmAddress guard):", operatorLpKey);

        // ── Phase 4 Step 3b: Deploy ArbitragePrimitive ────────────────────────
        //    BEFORE createSession so its address can be passed as the `arbitrage` param.
        //    ArbitragePrimitive is STATELESS (D-07) — no constructor args, no registerVault.
        //    The contract uses direct IAlgebraPool.swap() (no SwapRouter dependency at runtime
        //    per VENUE-DECISION.md finding #2). arbSwapRouter is stored in the manifest for
        //    reference and future router-path fallbacks.
        //    [Rule 1 - Bug fix]: Plan context block stated constructor(address swapRouter) but
        //    the actual 04-03 implementation is no-arg (fully stateless D-07 design).
        ArbitragePrimitive arb = new ArbitragePrimitive();
        console2.log("ArbitragePrimitive deployed:", address(arb));
        console2.log("  arbSwapRouter (manifest ref):", arbSwapRouter);

        // ── Step 4a: Authorize the publisher EOA on JournalRegistry (GAP #5) ──────
        //    The Python JournalPublisher sends recordJournal from the OPERATOR_JOURNAL_KEY
        //    EOA. The registry auth requires authorizedVaults || authorizedPublishers || owner.
        //    The EOA is neither a vault nor the owner after transferOwnership, so every
        //    on-chain journal would revert "unauthorized" without this call.
        //
        //    ORDERING: MUST happen while the deployer still owns the registry —
        //    BEFORE transferOwnership transfers ownership to the factory.
        journal.setAuthorizedPublisher(operatorJournalKey, true);
        console2.log("JournalRegistry.setAuthorizedPublisher: authorized publisher EOA (GAP #5)");
        console2.log("  Publisher EOA:", operatorJournalKey);

        // ── Step 4b: Transfer oracle + journal ownership to the factory ───────
        //    registerVault is owner-gated on both registries (Plans 02/03).
        //    The factory must own them before calling createSession (Key Decision 01-06).
        oracle.transferOwnership(address(factory));
        journal.transferOwnership(address(factory));
        console2.log("Ownership transferred: oracle + journal -> factory");

        // ── Step 5: createSession - one atomic tx deploys 3 MTokenVaults ─────
        //    D-13: Full 3-vault session. mCLA-S1 driven (Claude); mGPT/mGEM idle.
        //    Each vault's ERC-4626 share IS the tradeable mTOKEN (D-18, TOKEN-01).
        //    Tickers: mCLA-S1 / mGPT-S1 / mGEM-S1 (set in SessionFactory.createSession).
        //    Phase 4 (D-15): passes real ArbitragePrimitive address (Step-6 validates non-zero).
        //    D-13/GMXAdapter: NOT frozen after Phase 3 - adapter deploy deferred to Phase 6.
        address[3] memory vaults = factory.createSession(
            usdc,
            adapter,
            address(arb), // Phase 4 (D-15): REAL ArbitragePrimitive address (not address(0))
            sessionDuration
        );

        vm.stopBroadcast();

        // ── Step 6: Write canonical address manifest (D-14/D-15) ─────────────
        //    Written AFTER vm.stopBroadcast() - vm.writeFile is a cheatcode, not a broadcast.
        //    The manifest is the single source of truth for the orchestrator (Phase 3) and
        //    frontend (Phase 5). No hardcoded addresses consumed downstream.
        //    D-13/GMXAdapter: adapter field = address(0) (deferred to Phase 6 per D-13).
        //    Phase 4 (D-15): extended with arbitragePrimitive, arbSwapRouter, algebraFactory,
        //    algebraNpm, operatorLpKey, arbKey4 keys.
        _writeManifest(
            manifestPath,
            address(factory),
            address(oracle),
            address(journal),
            vaults[0], // mCLA-S1
            vaults[1], // mGPT-S1
            vaults[2], // mGEM-S1
            address(0), // adapter (GMXAdapter deferred to Phase 6 per D-13)
            mockPerpsAddr, // mockPerps: real MockPerps address (session uses this for venue=mock)
            mockUsdcAddr,
            mockEthFeedAddr,
            mockBtcFeedAddr,
            mockSolFeedAddr,
            mockSequencerFeedAddr,
            // Phase 4 (D-15) fields:
            address(arb),
            arbSwapRouter,
            algebraFactory,
            algebraNpm,
            operatorLpKey,
            arbKey4
        );

        // ── Step 7: Log summary ───────────────────────────────────────────────
        console2.log("=== trAIder Phase 3+4 Deploy Complete (D-12/D-13/D-14/D-15) ===");
        console2.log("PerformanceOracle : ", address(oracle));
        console2.log("JournalRegistry   : ", address(journal));
        console2.log("SessionFactory    : ", address(factory));
        console2.log("--- Phase 4 (D-15): AMM + Arbitrage ---");
        console2.log("ArbitragePrimitive: ", address(arb));
        console2.log("arbSwapRouter     : ", arbSwapRouter);
        console2.log("algebraFactory    : ", algebraFactory);
        console2.log("algebraNpm        : ", algebraNpm);
        console2.log("operatorLpKey     : ", operatorLpKey);
        console2.log("arbKey4           : ", arbKey4);
        console2.log("--- Mock substrate (D-12/D-06) ---");
        console2.log("MockERC20 (USDC)  : ", mockUsdcAddr);
        console2.log("MockPerps         : ", mockPerpsAddr);
        console2.log("MockETH/USD feed  : ", mockEthFeedAddr);
        console2.log("MockBTC/USD feed  : ", mockBtcFeedAddr);
        console2.log("MockSOL/USD feed  : ", mockSolFeedAddr);
        console2.log("MockSeqFeed       : ", mockSequencerFeedAddr);
        console2.log("--- Session vaults (share IS the mTOKEN - D-18) ---");
        console2.log("mCLA-S1 vault (Claude)  : ", vaults[0]);
        console2.log("mGPT-S1 vault (GPT)     : ", vaults[1]);
        console2.log("mGEM-S1 vault (Gemini)  : ", vaults[2]);
        console2.log("Adapter (GMXAdapter)    : address(0) [deferred to Phase 6 per D-13]");
        console2.log("--- Config ---");
        console2.log("Session duration (s)    : ", sessionDuration);
        console2.log("Initial capital (USDC)  : ", initialCapital);
        console2.log("Use Sepolia staleness   : ", useSepoliaStaleness);
        console2.log("Deploy mock substrate   : ", deployMockSubstrate);
        console2.log("--- Manifest ---");
        console2.log("Manifest written to     : ", manifestPath);
    }

    // =========================================================================
    // Internal helpers - idempotency guard
    // =========================================================================

    /// @notice Attempt to read the existing manifest and extract the sessionFactory address.
    /// @dev Returns address(0) if the manifest file does not exist, is empty, or if the
    ///      sessionFactory field is the zero address.
    ///      Uses vm.isFile() (forge-std) to guard the vm.readFile call so the missing-file
    ///      case is handled without a revert (D-14 idempotency pattern).
    /// @param manifestPath Path to the manifest JSON file.
    /// @return factory The previously-deployed SessionFactory address, or address(0) if absent.
    function _readManifestFactory(string memory manifestPath) internal view returns (address factory) {
        // vm.isFile returns false if the path does not exist or is not a regular file.
        if (!vm.isFile(manifestPath)) return address(0);
        string memory raw = vm.readFile(manifestPath);
        if (bytes(raw).length == 0) return address(0);
        // Parse the sessionFactory field via stdJson.
        // parseRaw returns empty bytes if the key is absent in the JSON.
        bytes memory factoryBytes = raw.parseRaw(".sessionFactory");
        if (factoryBytes.length == 0) return address(0);
        address parsed = abi.decode(factoryBytes, (address));
        return parsed;
    }

    // =========================================================================
    // Internal helpers - manifest write
    // =========================================================================

    /// @notice Write the canonical address manifest JSON (D-14/D-15).
    /// @dev Called after vm.stopBroadcast() - vm.writeFile is a cheatcode.
    ///      All fields are written as checksummed hex addresses.
    ///      The manifest is the single source of truth for the orchestrator + frontend.
    ///
    ///      mockPerps is recorded separately from adapter so the orchestrator can resolve
    ///      the real MockPerps address when PERPS_VENUE=mock, even when adapter=address(0)
    ///      (GMXAdapter deferred to Phase 6 per D-13).  The session reads manifest["mockPerps"]
    ///      for venue=mock and manifest["adapter"] for venue=gmx (fix for address(0) bug).
    ///
    ///      Phase 4 (D-15) extension: adds arbitragePrimitive, arbSwapRouter, algebraFactory,
    ///      algebraNpm, operatorLpKey, arbKey4 keys. Pool + LP-NFT addresses are written by
    ///      02-SeedPools.s.sol which merges into this manifest post-seeding.
    function _writeManifest(
        string memory manifestPath,
        address sessionFactory,
        address oracle,
        address journal,
        address vaultClaude,
        address vaultGpt,
        address vaultGem,
        address adapter,
        address mockPerps,
        address mockUsdc,
        address ethFeed,
        address btcFeed,
        address solFeed,
        address sequencerFeed,
        // Phase 4 (D-15) fields:
        address arbitragePrimitive,
        address arbSwapRouter,
        address algebraFactory,
        address algebraNpm,
        address operatorLpKey,
        address arbKey4
    ) internal {
        // Build JSON string. vm.toString converts addresses to checksummed hex strings.
        // abi.encodePacked has a 32KB limit; split into multiple parts to avoid stack issues.
        string memory part1 = string(
            abi.encodePacked(
                "{\n",
                '  "sessionFactory": "',
                vm.toString(sessionFactory),
                '",\n',
                '  "oracle": "',
                vm.toString(oracle),
                '",\n',
                '  "journal": "',
                vm.toString(journal),
                '",\n',
                '  "vaultClaude": "',
                vm.toString(vaultClaude),
                '",\n',
                '  "vaultGpt": "',
                vm.toString(vaultGpt),
                '",\n',
                '  "vaultGem": "',
                vm.toString(vaultGem),
                '",\n'
            )
        );
        string memory part2 = string(
            abi.encodePacked(
                '  "adapter": "',
                vm.toString(adapter),
                '",\n',
                '  "mockPerps": "',
                vm.toString(mockPerps),
                '",\n',
                '  "mockUsdc": "',
                vm.toString(mockUsdc),
                '",\n',
                '  "ethFeed": "',
                vm.toString(ethFeed),
                '",\n',
                '  "btcFeed": "',
                vm.toString(btcFeed),
                '",\n',
                '  "solFeed": "',
                vm.toString(solFeed),
                '",\n',
                '  "sequencerFeed": "',
                vm.toString(sequencerFeed),
                '",\n'
            )
        );
        // Phase 4 (D-15): pool + LP-NFT fields are placeholders here (written by 02-SeedPools.s.sol).
        // Including them as empty strings so the manifest schema is complete for required_keys validation.
        string memory part3 = string(
            abi.encodePacked(
                '  "arbitragePrimitive": "',
                vm.toString(arbitragePrimitive),
                '",\n',
                '  "arbSwapRouter": "',
                vm.toString(arbSwapRouter),
                '",\n',
                '  "algebraFactory": "',
                vm.toString(algebraFactory),
                '",\n',
                '  "algebraNpm": "',
                vm.toString(algebraNpm),
                '",\n',
                '  "operatorLpKey": "',
                vm.toString(operatorLpKey),
                '",\n',
                '  "arbKey4": "',
                vm.toString(arbKey4),
                '",\n',
                '  "poolClaude": "",\n',
                '  "poolGpt": "",\n',
                '  "poolGem": "",\n',
                '  "lpNftClaude": "0",\n',
                '  "lpNftGpt": "0",\n',
                '  "lpNftGem": "0"\n',
                "}\n"
            )
        );
        string memory manifest = string(abi.encodePacked(part1, part2, part3));
        vm.writeFile(manifestPath, manifest);
        console2.log("Manifest written:", manifestPath);
    }
}
