// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPerpsAdapter} from "./interfaces/IPerpsAdapter.sol";
import {MTokenVault} from "./mTokenVault.sol";

/// @title SettlementContract — trAIder session wind-down: position drain + frozen rate + pull claim (SETT-01/02)
/// @notice At session end this contract:
///           1. Sets sessionEnded=true (blocks further normal redeem — Pitfall 5 race fix).
///           2. Drains every open vault position via the adapter (SETT-01 — the contract
///              itself issues the closes, never assumed of the caller).
///           3. Snapshots the vault share supply and freezes a single redemption rate:
///                `redemptionRate = USDC_in_vault * 1e18 / supplySnapshot`
///              USDC custody STAYS IN THE VAULT — no custodian transfer (D-18, locked).
///           4. Lets holders pull their pro-rata USDC via `claim()` which:
///              - Burns the holder's vault shares via `MTokenVault.settlementBurn` (D-18).
///              - Pays USDC FROM THE VAULT via `MTokenVault.settlementWithdraw`.
///              - Rounds DOWN (D-14). Dust stays in the vault (no operator sweep, D-14).
///              - Is a pull pattern — no push loop over holders (D-15).
///              - Unclaimed funds remain claimable indefinitely (trust-minimization, D-14).
///
///         `endSession` is callable by the SessionFactory at any time, or by anyone once
///         `block.timestamp >= deadline` (SETT-02 recovery hatch). A non-factory caller
///         before the deadline is rejected. Either trigger still performs the full drain
///         before freezing the rate.
///
///         The frozen rate has no ongoing NAV or oracle dependency after settlement. The
///         only on-stage demo beat is the live `claim()` → USDC arrives in the caller's
///         wallet. Tests against MockPerps (vm.roll to advance past executionDelay).
///
/// @dev Inherits: ReentrancyGuardTransient (Cancun tstore/tload, EIP-1153)
///
///      Threat mitigations:
///        T-1-settle:     sessionEnded=true FIRST (Pitfall 5) + drain + freeze one-time.
///        T-1-drainassume: the contract itself issues closePosition for every open key.
///        T-1-burnauth:   vault.settlementBurn gated to vault.settlement == address(this).
///        T-1-claim:      pull-over-push; no holder-iteration loop.
///        T-1-settlereentrancy: nonReentrant + CEI (burn before pay).
///        T-1-custody:    settlement holds NO USDC; rate divides vault balance.
///        T-1-recovery:   factory || deadline gate on endSession.
///        T-1-sweep:      no sweep function; dust stays; no expiry.
///        T-1-roundup:    Math.mulDiv rounds DOWN; sum(claims) ≤ vault USDC.
contract SettlementContract is ReentrancyGuardTransient {
    // =========================================================================
    // State
    // =========================================================================

    /// @notice True once endSession has been called (blocks further normal redeem — Pitfall 5).
    bool public sessionEnded;

    /// @notice True once the redemption rate has been frozen (endSession fully complete).
    bool public settled;

    /// @notice Frozen redemption rate in 1e18-scaled fixed-point.
    ///         `claimAmount = Math.mulDiv(shares, redemptionRate, 1e18)` (rounds down, D-14).
    ///         Set once in endSession; never updated thereafter (no oracle dependency post-settle).
    uint256 public redemptionRate;

    /// @notice Vault share supply snapshot taken at the moment the rate was frozen.
    ///         Used by `totalClaimable()` for the Plan 07 solvency invariant.
    uint256 public supplySnapshot;

    // =========================================================================
    // Immutables
    // =========================================================================

    /// @notice USDC token address (6 decimals). The settlement contract NEVER holds USDC;
    ///         it reads `balanceOf(vault)` to compute the frozen rate (D-18, locked custody).
    address public immutable usdc;

    /// @notice The perps adapter (IPerpsAdapter). Used to enumerate open position keys and
    ///         to call settlementClosePosition on the vault for each key during the drain.
    address public immutable adapter;

    /// @notice The mTokenVault address — both the USDC custodian and the share ledger (D-18).
    ///         USDC custody STAYS IN THE VAULT (no transfer to this contract).
    ///         The share IS the vault's own ERC-4626 token (TOKEN-01, D-18, ONE TOKEN).
    address public immutable vault;

    /// @notice The SessionFactory address. Only caller permitted before the deadline (SETT-02).
    address public immutable sessionFactory;

    /// @notice Unix timestamp after which any address may call endSession (SETT-02 recovery hatch).
    uint256 public immutable deadline;

    /// @notice Operator/LP key address for the AMM position (D-18).
    ///         If non-zero, endSession reverts if this address still holds vault shares —
    ///         ensuring the MM/operator has redeemed all AMM LP positions before settlement.
    ///         Pass address(0) to disable the guard (Phase 1 / tests without AMM).
    address public immutable mmAddress;

    // =========================================================================
    // Events
    // =========================================================================

    /// @notice Emitted once when the redemption rate is frozen (endSession completes).
    /// @param rate           Frozen redemption rate (1e18-scaled: USDC per share × 1e18).
    /// @param supplySnapshot Vault share supply at the freeze moment.
    /// @param usdcBalance    Vault USDC balance at the freeze moment (custody in the vault, D-18).
    event SessionSettled(uint256 rate, uint256 supplySnapshot, uint256 usdcBalance);

    /// @notice Emitted each time a holder pulls their pro-rata USDC via claim().
    /// @param holder    Address that claimed.
    /// @param shares    Vault shares burned (via settlementBurn — the mTOKEN is the vault share, D-18).
    /// @param usdcAmount USDC transferred from the vault to the holder (via settlementWithdraw).
    event Claimed(address indexed holder, uint256 shares, uint256 usdcAmount);

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Deploy the settlement contract for a specific vault session.
    /// @dev The SessionFactory deploys this and then calls vault.setSettlement(address(this))
    ///      (Plan 06) so the vault authorizes settlementBurn and settlementWithdraw from here.
    ///      In tests: `vm.prank(sessionFactory); vault.setSettlement(address(settlement));`
    /// @param usdc_           USDC token address (6 decimals).
    /// @param adapter_        IPerpsAdapter address (MockPerps in Phase 1).
    /// @param vault_          MTokenVault address — USDC custodian and share ledger.
    /// @param sessionFactory_ SessionFactory address — only caller before the deadline.
    /// @param deadline_       Unix timestamp after which endSession is permissionless.
    /// @param mmAddress_      Operator/LP key for AMM position guard (D-18). Pass address(0) to disable.
    constructor(
        address usdc_,
        address adapter_,
        address vault_,
        address sessionFactory_,
        uint256 deadline_,
        address mmAddress_
    ) {
        require(usdc_ != address(0), "Settlement: zero usdc");
        require(adapter_ != address(0), "Settlement: zero adapter");
        require(vault_ != address(0), "Settlement: zero vault");
        require(sessionFactory_ != address(0), "Settlement: zero factory");
        require(deadline_ > block.timestamp, "Settlement: deadline in past");

        usdc = usdc_;
        adapter = adapter_;
        vault = vault_;
        sessionFactory = sessionFactory_;
        deadline = deadline_;
        mmAddress = mmAddress_;
    }

    // =========================================================================
    // Session end — drain + freeze (SETT-01/02)
    // =========================================================================

    /// @notice End the session: drain all open positions, freeze the redemption rate.
    /// @dev Ordering (Pitfall 5 — closes the race where a holder redeems before the rate is set):
    ///        1. `sessionEnded = true` FIRST — the vault's `maxRedeem`/`maxWithdraw` return 0
    ///           post-sessionEnded so normal ERC-4626 exits are blocked from this point.
    ///           Note: the vault's own `sessionEnded` is set by the factory calling
    ///           `vault.endSession()` (VAULT-07). This contract's `sessionEnded` flag adds
    ///           a second layer blocking normal exits during the drain window.
    ///        2. Drain every open position: enumerate keys via adapter.getOpenPositionKeys(vault)
    ///           and call vault.settlementClosePosition(key, 0) for each. MockPerps queues an
    ///           async close executed after `executionDelay` blocks — tests use `vm.roll` +
    ///           `adapter.executeOrder(orderKey)` to settle before the rate is frozen.
    ///           The contract MUST drain; there is no escape hatch and no "skip if caller
    ///           pre-closed" branch (SETT-01).
    ///        3. Gate: only proceed to freeze once `adapter.positionValueUSDC(vault) == 0`
    ///           (all positions drained). A non-zero open position blocks the freeze.
    ///        4. Snapshot supply → freeze rate → emit SessionSettled → `settled = true`.
    ///
    /// @dev Trigger gate (SETT-02): factory at any time OR anyone once `block.timestamp >= deadline`.
    ///      nonReentrant guards against re-entry during the drain loop.
    // slither-disable-next-line reentrancy-no-eth — nonReentrant (ReentrancyGuardTransient) prevents re-entry; settled=true after the loop closes the gate
    function endSession() external nonReentrant {
        require(!settled, "Settlement: already settled");
        // slither-disable-next-line timestamp
        require(
            msg.sender == sessionFactory || block.timestamp >= deadline, "Settlement: not authorized before deadline"
        );

        // Pitfall 5 / WR-01 fix: call vault.endSession() FIRST to put the vault into settled
        // mode before the drain. This ensures:
        //   - vault.maxWithdraw/maxRedeem → 0 immediately (no race to redeem during drain, WR-01)
        //   - vault._tradingLocked cleared so settlementClosePosition is not bricked (WR-05)
        //   - vault.sessionEnded=true so settlementBurn's sessionEnded guard is satisfied (CR-02)
        // vault.endSession() is gated to factory OR settlement (this contract is the settlement).
        // If vault.endSession() reverts (session not active — already ended by the factory),
        // we swallow the revert: the vault is already in settled mode and we can proceed.
        // slither-disable-next-line reentrancy-benign — nonReentrant guard prevents re-entry;
        // sessionEnded is set immediately after. vault.endSession() is a trusted call (vault
        // is an immutable set at deploy time by the factory; not user-controlled).
        try MTokenVault(vault).endSession() {} catch {}
        // intentionally swallowed — vault may already be ended by factory before this call.

        // Set our own local sessionEnded flag.
        sessionEnded = true;

        // SETT-01 DRAIN — the contract is responsible for issuing every close.
        // There is no "or positions are already closed" branch; the contract drains them.
        bytes32[] memory openKeys = IPerpsAdapter(adapter).getOpenPositionKeys(vault);
        uint256 keyCount = openKeys.length;
        for (uint256 i = 0; i < keyCount; i++) {
            // vault.settlementClosePosition routes to adapter.closePosition as the vault
            // (adapter gates on pos.vault == msg.sender; vault is msg.sender here).
            // sizeUsd = 0 → full close in MockPerps (partial close is Phase 3 scope).
            // slither-disable-next-line unused-return,calls-loop — return value (orderKey) intentionally ignored in drain loop; nonReentrant guards re-entry
            MTokenVault(vault).settlementClosePosition(openKeys[i], 0);
        }

        // Gate: position value must be zero before the rate is frozen.
        // In tests: vm.roll(block.number + executionDelay) + executeOrder() to settle the
        // async closes BEFORE calling endSession, so positionValueUSDC(vault) == 0 here.
        // In production (GMX): the keeper executes the orders; endSession is called after.
        require(IPerpsAdapter(adapter).positionValueUSDC(vault) == 0, "Settlement: positions not drained");

        // D-18 mmAddress guard: if the operator/MM LP key is set, they must have redeemed
        // all vault shares before endSession can proceed. This ensures the AMM LP position
        // (held as vault shares) is fully unwound before the redemption rate is frozen.
        // address(0) disables the guard (Phase 1 / tests without AMM, ARB-01).
        require(
            mmAddress == address(0) || MTokenVault(vault).balanceOf(mmAddress) == 0,
            "Settlement: operator/MM must redeem shares before endSession"
        );

        // Snapshot the vault share supply (the mTOKEN IS the vault share — D-18, ONE TOKEN).
        // USDC custody stays in the vault; no transfer to this contract.
        supplySnapshot = MTokenVault(vault).totalSupply();
        require(supplySnapshot > 0, "Settlement: no shares outstanding");

        // Freeze redemption rate: vault USDC balance / supply snapshot (D-18, locked custody).
        // The rate divides the VAULT's post-drain USDC balance — not this contract's (it holds none).
        uint256 usdcBal = IERC20(usdc).balanceOf(vault);
        // Math.mulDiv(a, b, c) computes a*b/c with overflow safety (D-14).
        redemptionRate = Math.mulDiv(usdcBal, 1e18, supplySnapshot);
        settled = true;

        emit SessionSettled(redemptionRate, supplySnapshot, usdcBal);
    }

    // =========================================================================
    // Claim — pull distribution (D-14/15)
    // =========================================================================

    /// @notice Pull pro-rata USDC by burning the caller's vault shares.
    /// @dev CEI order (T-1-settlereentrancy mitigation):
    ///        1. Check: require settled + shares > 0.
    ///        2. Compute: `usdcAmount = Math.mulDiv(shares, redemptionRate, 1e18)` (rounds DOWN, D-14).
    ///        3. Effect (burn FIRST): `MTokenVault(vault).settlementBurn(msg.sender, shares)`
    ///           — burns the caller's vault shares (the mTOKEN IS the vault share, D-18).
    ///           A reentrant call finds balanceOf == 0 and reverts immediately.
    ///        4. Interaction (pay SECOND): `MTokenVault(vault).settlementWithdraw(msg.sender, usdcAmount)`
    ///           — transfers USDC FROM THE VAULT to the caller. Settlement holds NO USDC.
    ///
    ///      No expiry, no operator sweep (D-14): dust (rounding remainder) stays IN THE VAULT
    ///      and is claimable by any holder with remaining shares. There is no function on
    ///      this contract to sweep or recover USDC (it holds none).
    ///
    ///      Pull pattern (D-15): each holder calls claim() for themselves. There is NO
    ///      holder-iteration loop — the only loop in this contract is the position-drain loop
    ///      in endSession. One reverting recipient cannot block others.
    function claim() external nonReentrant {
        require(settled, "Settlement: not finalized");

        // Read the caller's vault share balance (the mTOKEN IS the vault share — D-18).
        uint256 shares = MTokenVault(vault).balanceOf(msg.sender);
        require(shares > 0, "Settlement: no shares");

        // Compute pro-rata USDC (rounds down — D-14; dust stays in vault).
        uint256 usdcAmount = Math.mulDiv(shares, redemptionRate, 1e18);

        // CEI: burn FIRST (effect), then pay (interaction).
        // Both calls go through the vault's gated hooks — requires vault.settlement == address(this).
        // The USDC is paid FROM THE VAULT; this contract never custodies it (D-18, locked).
        MTokenVault(vault).settlementBurn(msg.sender, shares);
        MTokenVault(vault).settlementWithdraw(msg.sender, usdcAmount);

        emit Claimed(msg.sender, shares, usdcAmount);
    }

    // =========================================================================
    // View — Plan 07 solvency invariant helper
    // =========================================================================

    /// @notice Total USDC claimable by all remaining share-holders at the frozen rate.
    /// @dev Used by Plan 07's `invariant_TotalAssetsGeTotalClaimable` to verify that the
    ///      vault's USDC balance never falls below the sum of all outstanding claims.
    ///      After all claims are settled this returns 0 (all shares burned).
    ///      Returns 0 before settlement (redemptionRate == 0).
    /// @return uint256 Total claimable USDC (6 decimals) at the frozen rate.
    function totalClaimable() external view returns (uint256) {
        if (!settled) return 0;
        return Math.mulDiv(MTokenVault(vault).totalSupply(), redemptionRate, 1e18);
    }
}
