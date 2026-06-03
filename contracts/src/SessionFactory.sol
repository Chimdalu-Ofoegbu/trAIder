// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MTokenVault} from "./mTokenVault.sol";
import {PerformanceOracle} from "./PerformanceOracle.sol";
import {JournalRegistry} from "./JournalRegistry.sol";
import {SettlementContract} from "./SettlementContract.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title SessionFactory — trAIder one-tx 3-vault deploy + registration (FACT-01)
/// @notice Deploys exactly three `MTokenVault` instances (one per model: Claude / GPT / Gemini)
///         in a SINGLE atomic transaction and registers each with the PerformanceOracle and
///         JournalRegistry. The vault's ERC-4626 share IS the tradeable mTOKEN (D-18, TOKEN-01)
///         — there is NO separate standalone mToken contract.
///
///         `createSession` atomically:
///           1. Deploys 3 `MTokenVault`s with tickers mCLA-S1 / mGPT-S1 / mGEM-S1.
///           2. Deploys 3 `SettlementContract`s (one per vault — USDC custody stays in vault, D-18).
///           3. Calls `vault.setSettlement(settlement)` on each vault to authorize the gated
///              `settlementBurn` / `settlementWithdraw` paths (D-18).
///           4. Registers each vault with the PerformanceOracle and JournalRegistry.
///           5. Optionally registers each vault with an ArbitrageContract if provided (Phase 4;
///              `address(0)` is accepted in Phase 1 — registration is skipped).
///           6. Calls `vault.startSession(durationSeconds)` to open trading.
///
///         The `new` keyword guarantees atomic rollback: if any sub-deploy or call reverts, the
///         entire `createSession` tx reverts with no orphaned or partially-wired contracts (Pitfall 6).
///
///         Ownership of PerformanceOracle and JournalRegistry MUST be transferred to this factory
///         before `createSession` is called — otherwise `registerVault` (owner-gated on both
///         registries) will revert. The recommended flow in the deploy script:
///           1. Deploy oracle + journal.
///           2. Transfer oracle.ownership + journal.ownership to the factory address.
///           3. Call factory.createSession(...).
///
///         Access control: `createSession` is `onlyOwner`. The factory owner is the operator
///         (the deployer of the factory). All static config (feeds, sequencer, orchestrator,
///         operator, initialCapital, useSepoliaStaleness) is stored at construction time and
///         re-used for each vault deploy — keeping `createSession`'s calldata small.
///
/// @dev NatSpec on every external function.
///      No hot-swap setter for the adapter — it is wired at vault construction (D-04, no governance
///      attack surface). The factory address is passed as `sessionFactory_` into each vault ctor
///      so only this factory may call `startSession` / `endSession` / `setSettlement`.
contract SessionFactory is Ownable {
    // =========================================================================
    // Immutable config — stored once, reused per vault deploy
    // =========================================================================

    /// @notice PerformanceOracle — registry + Coliseum Score engine (Plan 02).
    PerformanceOracle public immutable oracle;

    /// @notice JournalRegistry — per-trade IPFS CID registry (Plan 03).
    JournalRegistry public immutable journal;

    /// @notice SettlementContract template source for the session wind-down (Plan 05).
    ///         Stored for event transparency; each session deploys a fresh SettlementContract
    ///         per vault (USDC custody stays in vault — D-18).
    address public immutable settlementTemplate;

    /// @notice Chainlink sequencer uptime feed (Arbitrum One).
    ///         Pass address(0) for Sepolia / Robinhood Chain (skips the sequencer check — D-11).
    address public immutable sequencerFeed;

    /// @notice Chainlink ETH/USD feed (Arbitrum One: 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612).
    address public immutable ethFeed;

    /// @notice Chainlink BTC/USD feed (Arbitrum One: 0x6ce185560a4963c47a8Ec16f4EF5d62A0000E708).
    address public immutable btcFeed;

    /// @notice Chainlink SOL/USD feed (Arbitrum One: 0x24ceA4b8ce57cdA5058b924B9B9987992450590c).
    address public immutable solFeed;

    /// @notice Orchestrator key — the only address permitted to call openLong/openShort/closePosition.
    address public immutable orchestrator;

    /// @notice Operator key — funds the session; CANNOT withdraw vault USDC directly (VAULT-08).
    address public immutable operator;

    /// @notice Seed capital per vault for PnL normalization in the PerformanceOracle (typically 10_000e6).
    uint256 public immutable initialCapitalUsdc;

    /// @notice If true, uses the 6-hour Sepolia staleness window for all Chainlink feeds (D-12).
    bool public immutable useSepoliaStaleness;

    // =========================================================================
    // Events
    // =========================================================================

    /// @notice Emitted once per successful createSession call.
    /// @param vaults          The three deployed MTokenVault addresses [CLA, GPT, GEM].
    /// @param durationSeconds Session duration passed to each vault.startSession.
    event SessionCreated(address[3] vaults, uint256 durationSeconds);

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Deploy the SessionFactory and store the static config shared across all vaults.
    /// @dev The deployer becomes the owner (Ownable) and is the only address that may call
    ///      createSession. The oracle and journal MUST transfer ownership to this factory
    ///      (via oracle.transferOwnership(address(this))) before createSession is called,
    ///      because registerVault is owner-gated on both registries (Plans 02/03).
    /// @param _oracle             PerformanceOracle deployed by the operator.
    /// @param _journal            JournalRegistry deployed by the operator.
    /// @param _sequencerFeed      Chainlink Arbitrum sequencer uptime feed (address(0) = skip).
    /// @param _ethFeed            Chainlink ETH/USD feed.
    /// @param _btcFeed            Chainlink BTC/USD feed.
    /// @param _solFeed            Chainlink SOL/USD feed.
    /// @param _orchestrator       Orchestrator key for trading operations.
    /// @param _operator           Operator key (funds session; cannot withdraw USDC).
    /// @param _initialCapitalUsdc Seed capital per vault in 6-decimal USDC units (e.g. 10_000e6).
    /// @param _useSepoliaStaleness If true, uses 6-hour staleness window for all Chainlink feeds.
    constructor(
        address _oracle,
        address _journal,
        address _sequencerFeed,
        address _ethFeed,
        address _btcFeed,
        address _solFeed,
        address _orchestrator,
        address _operator,
        uint256 _initialCapitalUsdc,
        bool _useSepoliaStaleness
    ) Ownable(msg.sender) {
        require(_oracle != address(0), "Factory: zero oracle");
        require(_journal != address(0), "Factory: zero journal");
        require(_ethFeed != address(0), "Factory: zero ethFeed");
        require(_btcFeed != address(0), "Factory: zero btcFeed");
        require(_solFeed != address(0), "Factory: zero solFeed");
        require(_orchestrator != address(0), "Factory: zero orchestrator");
        require(_operator != address(0), "Factory: zero operator");

        oracle = PerformanceOracle(_oracle);
        journal = JournalRegistry(_journal);
        settlementTemplate = address(0); // unused; each vault gets a fresh SettlementContract
        sequencerFeed = _sequencerFeed;
        ethFeed = _ethFeed;
        btcFeed = _btcFeed;
        solFeed = _solFeed;
        orchestrator = _orchestrator;
        operator = _operator;
        initialCapitalUsdc = _initialCapitalUsdc > 0 ? _initialCapitalUsdc : 10_000e6;
        useSepoliaStaleness = _useSepoliaStaleness;
    }

    // =========================================================================
    // Core — one-tx 3-vault deploy + registration (FACT-01)
    // =========================================================================

    /// @notice Deploys three MTokenVaults (mCLA-S1, mGPT-S1, mGEM-S1), wires their settlement
    ///         contracts, registers each with the PerformanceOracle and JournalRegistry, and
    ///         starts the session — all in ONE atomic transaction.
    ///
    /// @dev Atomicity: the `new` keyword causes the entire tx to revert if ANY sub-deploy or
    ///      external call fails — guaranteeing no orphaned or partially-wired contracts (Pitfall 6).
    ///
    ///      Settlement per vault: a fresh `SettlementContract(usdc, adapter, vault, this, deadline)`
    ///      is deployed for each vault. USDC custody stays in the vault — the settlement holds
    ///      NO USDC (D-18 locked). The factory then calls `vault.setSettlement(settlement)` to
    ///      authorize `settlementBurn` / `settlementWithdraw` from that address.
    ///
    ///      Arbitrage placeholder (Phase 1): if `arbitrage == address(0)`, the arb registration
    ///      step is skipped. Full ArbitragePrimitive wiring is deferred to Phase 4 (RESEARCH open Q3).
    ///
    ///      Ownership requirement: the PerformanceOracle and JournalRegistry MUST have transferred
    ///      ownership to address(this) before this call — otherwise registerVault reverts.
    ///
    /// @param usdc            USDC ERC-20 address (6 decimals) — the vault underlying asset.
    /// @param adapter         IPerpsAdapter address (MockPerps in Phase 1, GMXAdapter in Phase 3).
    ///                        Wired into each vault constructor at deploy time — no hot-swap (D-04).
    /// @param arbitrage       ArbitragePrimitive address. Pass address(0) in Phase 1 (deferred).
    /// @param durationSeconds Session length in seconds (e.g. 259200 for 72 hours).
    /// @return vaults         The three deployed MTokenVault addresses [CLA, GPT, GEM].
    function createSession(address usdc, address adapter, address arbitrage, uint256 durationSeconds)
        external
        onlyOwner
        returns (address[3] memory vaults)
    {
        require(usdc != address(0), "Factory: zero usdc");
        require(adapter != address(0), "Factory: zero adapter");
        require(durationSeconds > 0, "Factory: zero duration");

        // Session deadline for each SettlementContract's permissionless recovery hatch (SETT-02).
        uint256 deadline = block.timestamp + durationSeconds;

        // Three model ticker names — the vault share IS the tradeable mTOKEN (D-18, TOKEN-01).
        // There is NO separate standalone mToken deploy. NO `new MToken`, NO `setVault`.
        string[3] memory names = ["mCLA-S1", "mGPT-S1", "mGEM-S1"];

        for (uint256 i = 0; i < 3; i++) {
            // ── Step 1: deploy the vault (share IS the ticker mTOKEN — D-18) ──────────────
            MTokenVault vault = new MTokenVault(
                IERC20(usdc),
                names[i], // name_ == ticker (e.g. "mCLA-S1") — the share IS the mTOKEN
                names[i], // symbol_ == same ticker
                adapter, // wired at construction; no setter (D-04)
                sequencerFeed,
                ethFeed,
                btcFeed,
                solFeed,
                address(this), // sessionFactory_ — only this contract may start/end/setSettlement
                orchestrator,
                operator,
                initialCapitalUsdc,
                useSepoliaStaleness
            );
            vaults[i] = address(vault);

            // ── Step 2: deploy a fresh SettlementContract for this vault ─────────────────
            SettlementContract settlement =
                new SettlementContract(usdc, adapter, address(vault), address(this), deadline);

            // ── Step 3: wire the settlement so settlementBurn/settlementWithdraw are authorized ──
            vault.setSettlement(address(settlement));

            // ── Step 4: register with PerformanceOracle (owner-gated; oracle.owner == this) ──
            oracle.registerVault(address(vault));

            // ── Step 5: register with JournalRegistry (owner-gated; journal.owner == this) ──
            journal.registerVault(address(vault));

            // ── Step 6: ArbitragePrimitive registration (deferred to Phase 4) ───────────
            // address(0) accepted in Phase 1 — skip (RESEARCH open Q3).
            // Phase 4 will replace this no-op body with the real ArbitragePrimitive call
            // without changing the factory ABI (same createSession signature).
            if (arbitrage != address(0)) {
                // Phase 4 TODO: ArbitragePrimitive(arbitrage).registerVault(address(vault));
                // The condition is intentionally a no-op in Phase 1.
                // solhint-disable-next-line no-empty-blocks
            }

            // ── Step 7: start the session on this vault ───────────────────────────────────
            vault.startSession(durationSeconds);
        }

        emit SessionCreated(vaults, durationSeconds);
    }
}
