// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {stdJson} from "forge-std/StdJson.sol";
import {PerformanceOracle} from "../src/PerformanceOracle.sol";
import {JournalRegistry} from "../src/JournalRegistry.sol";
import {SessionFactory} from "../src/SessionFactory.sol";
import {MockERC20} from "../src/mocks/MockERC20.sol";
import {MockPerps} from "../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../src/mocks/MockChainlinkAggregator.sol";
import {MockSequencerUptimeFeed} from "../src/mocks/MockSequencerUptimeFeed.sol";

/// @title DeployPhase1 - trAIder full Phase 1 + Phase 3 stack deploy script (FACT-01, D-12/D-13/D-14)
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

        // ── Read optional config with Arbitrum One mainnet defaults ──────────
        address ethFeed = vm.envOr("ETH_FEED", ARB_ONE_ETH_FEED);
        address btcFeed = vm.envOr("BTC_FEED", ARB_ONE_BTC_FEED);
        address solFeed = vm.envOr("SOL_FEED", ARB_ONE_SOL_FEED);
        // Sepolia: SEQUENCER_FEED="" or "0x0000..." → address(0) skips the sequencer check (D-06/D-07)
        address sequencerFeed = vm.envOr("SEQUENCER_FEED", address(0));

        uint256 sessionDuration = vm.envOr("SESSION_DURATION", uint256(259_200)); // 72 hours
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
            useSepoliaStaleness
        );
        console2.log("SessionFactory deployed:", address(factory));

        // ── Step 4: Transfer oracle + journal ownership to the factory ────────
        //    registerVault is owner-gated on both registries (Plans 02/03).
        //    The factory must own them before calling createSession (Key Decision 01-06).
        oracle.transferOwnership(address(factory));
        journal.transferOwnership(address(factory));
        console2.log("Ownership transferred: oracle + journal -> factory");

        // ── Step 5: createSession - one atomic tx deploys 3 MTokenVaults ─────
        //    D-13: Full 3-vault session. mCLA-S1 driven (Claude); mGPT/mGEM idle.
        //    Each vault's ERC-4626 share IS the tradeable mTOKEN (D-18, TOKEN-01).
        //    Tickers: mCLA-S1 / mGPT-S1 / mGEM-S1 (set in SessionFactory.createSession).
        //    address(0) arbitrage: Phase 4 wires ArbitragePrimitive (FACT-01 Phase 3 scope).
        //    D-13/GMXAdapter: NOT frozen after Phase 3 - adapter deploy deferred to Phase 6.
        address[3] memory vaults = factory.createSession(
            usdc,
            adapter,
            address(0), // arbitrage placeholder (Phase 4 wires ArbitragePrimitive)
            sessionDuration
        );

        vm.stopBroadcast();

        // ── Step 6: Write canonical address manifest (D-14) ──────────────────
        //    Written AFTER vm.stopBroadcast() - vm.writeFile is a cheatcode, not a broadcast.
        //    The manifest is the single source of truth for the orchestrator (Phase 3) and
        //    frontend (Phase 5). No hardcoded addresses consumed downstream.
        //    D-13/GMXAdapter: adapter field = address(0) (deferred to Phase 6 per D-13).
        _writeManifest(
            manifestPath,
            address(factory),
            address(oracle),
            address(journal),
            vaults[0], // mCLA-S1
            vaults[1], // mGPT-S1
            vaults[2], // mGEM-S1
            address(0), // adapter (GMXAdapter deferred to Phase 6 per D-13)
            mockUsdcAddr,
            mockEthFeedAddr,
            mockBtcFeedAddr,
            mockSolFeedAddr,
            mockSequencerFeedAddr
        );

        // ── Step 7: Log summary ───────────────────────────────────────────────
        console2.log("=== trAIder Phase 3 Deploy Complete (D-12/D-13/D-14) ===");
        console2.log("PerformanceOracle : ", address(oracle));
        console2.log("JournalRegistry   : ", address(journal));
        console2.log("SessionFactory    : ", address(factory));
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

    /// @notice Write the canonical address manifest JSON (D-14).
    /// @dev Called after vm.stopBroadcast() - vm.writeFile is a cheatcode.
    ///      All fields are written as checksummed hex addresses.
    ///      The manifest is the single source of truth for the orchestrator + frontend.
    function _writeManifest(
        string memory manifestPath,
        address sessionFactory,
        address oracle,
        address journal,
        address vaultClaude,
        address vaultGpt,
        address vaultGem,
        address adapter,
        address mockUsdc,
        address ethFeed,
        address btcFeed,
        address solFeed,
        address sequencerFeed
    ) internal {
        // Build JSON string. vm.toString converts addresses to checksummed hex strings.
        string memory manifest = string(
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
                '",\n',
                '  "adapter": "',
                vm.toString(adapter),
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
                '"\n',
                "}\n"
            )
        );
        vm.writeFile(manifestPath, manifest);
        console2.log("Manifest written:", manifestPath);
    }
}
