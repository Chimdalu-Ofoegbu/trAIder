// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {stdJson} from "forge-std/StdJson.sol";

/// @title WithdrawLP - trAIder Phase 4 single-sided LP unwind (Seam D re-seed, step 1 of 2)
/// @notice Withdraws the OLD, mis-positioned single-sided LP positions (NFT ids 103/104/105)
///         that 02-SeedPools created BEFORE the tick-range fix. Those mints landed entirely on the
///         mTOKEN side (≈1000 mTOKEN, 0 USDC) because the LP range was centered on tick 0 instead of
///         the pool's real decimals-adjusted price tick (~±276324). This script fully decreases each
///         position's liquidity and collects BOTH token legs back to OPERATOR_LP_KEY, so the operator
///         can then re-run the FIXED 02-SeedPools (which now centers on the live tick → two-sided).
///
///         **What it does, per LP NFT id in {103, 104, 105} (all owned by operatorLpKey):**
///           1. `npm.positions(tokenId)` → read the position's current `liquidity` (Algebra Integral v1
///              layout: NO `fee` field — verified against the live Sepolia NPM, see @dev below).
///           2. If liquidity == 0, SKIP (re-runnable / idempotent — a re-run after a partial broadcast
///              won't revert on already-emptied positions).
///           3. `npm.decreaseLiquidity({tokenId, liquidity: fullLiquidity, amount0Min:0, amount1Min:0,
///              deadline: block.timestamp + 600})` → moves all liquidity into the position's owed
///              tokens (does NOT transfer to the wallet yet).
///           4. `npm.collect({tokenId, recipient: operatorLpKey, amount0Max: max, amount1Max: max})`
///              → sweeps the now-owed token0 + token1 (both legs + any accrued fees) to operatorLpKey.
///
///         **Key separation (SEC-01):** the LP NFTs are owned by OPERATOR_LP_KEY, so the OPERATOR runs
///         this with `--private-key $OPERATOR_LP_KEY_PRIVATE_KEY`. decreaseLiquidity/collect are
///         owner-or-approved gated on the NPM, so only the LP key can unwind these positions.
///
///         **NAV note (VAULT-08):** this returns mTOKEN + USDC to the operator's LP wallet only; it does
///         NOT touch vault USDC and does NOT change NAV. The recovered mTOKEN/USDC are then re-LP'd by
///         02-SeedPools (the deposit-at-NAV round-trip there is the NAV-neutral path).
///
/// @dev Usage (OPERATOR runs — produces state changes, hence --broadcast at run time, NOT here):
///        forge build                                                    # compile check (no broadcast)
///        # dry-run (simulate against live Sepolia state, sends NOTHING):
///        forge script script/03-WithdrawLP.s.sol:WithdrawLP --sig "run()" \
///          --rpc-url $SEPOLIA_RPC --private-key $OPERATOR_LP_KEY_PRIVATE_KEY
///        # live (operator only):
///        forge script script/03-WithdrawLP.s.sol:WithdrawLP --sig "run()" \
///          --rpc-url $SEPOLIA_RPC --private-key $OPERATOR_LP_KEY_PRIVATE_KEY --broadcast
///
///      Required: 02-SeedPools.s.sol must have already run (manifest must contain algebraNpm + the LP
///      NFT ids). The broadcasting key MUST be OPERATOR_LP_KEY (the LP NFT owner).
///
///      Environment variables:
///        MANIFEST_PATH    (optional) Manifest JSON path; default = "../deployments/sepolia.json"
///        ALGEBRA_NPM      (optional) NonfungiblePositionManager; default = manifest `.algebraNpm`
///
/// @dev **Algebra Integral v1 `positions()` return layout (VERIFIED against the live Sepolia NPM
///      0x79EA…2D4A via `cast call positions(uint256)` on the real LP ids before writing this script):**
///        (uint96 nonce, address operator, address token0, address token1,
///         int24 tickLower, int24 tickUpper, uint128 liquidity,
///         uint256 feeGrowthInside0LastX128, uint256 feeGrowthInside1LastX128,
///         uint128 tokensOwed0, uint128 tokensOwed1)
///      This differs from Uniswap V3, which inserts a `uint24 fee` field between token1 and tickLower
///      (12 returns). Algebra uses a pool-level DYNAMIC fee, so there is NO per-position `fee` field
///      (11 returns). `liquidity` is the 7th return value. Confirmed live: id 103 → token0=USDC,
///      token1=mCLA (Case B); ids 104/105 → token0=mGPT/mGEM, token1=USDC (Case A); all
///      liquidity = 13886583925219844502464, all owned by operatorLpKey.
contract WithdrawLP is Script {
    using stdJson for string;

    // Algebra Integral v1 Sepolia NPM default (mirrors 02-SeedPools default / manifest).
    address internal constant DEFAULT_ALGEBRA_NPM = 0x79EA6cB3889fe1FC7490A1C69C7861761d882D4A;

    string internal constant DEFAULT_MANIFEST_PATH = "../deployments/sepolia.json";

    /// @dev The three old single-sided LP NFT ids from the pre-fix 02-SeedPools seed (manifest
    ///      lpNftClaude/lpNftGpt/lpNftGem = 103/104/105). Hard-listed here (rather than parsed from the
    ///      manifest, where they are stored as decimal strings) for an unambiguous, auditable unwind set.
    uint256[3] internal LP_NFT_IDS = [uint256(103), uint256(104), uint256(105)];

    function run() external {
        string memory manifestPath = vm.envOr("MANIFEST_PATH", DEFAULT_MANIFEST_PATH);
        require(vm.isFile(manifestPath), "WithdrawLP: manifest not found - run 01-Deploy/02-SeedPools first");
        string memory raw = vm.readFile(manifestPath);

        // ── Resolve the NPM: env override → manifest `.algebraNpm` → hardcoded default ──────────
        address algebraNpm = vm.envOr("ALGEBRA_NPM", address(0));
        if (algebraNpm == address(0)) {
            bytes memory npmBytes = raw.parseRaw(".algebraNpm");
            algebraNpm = npmBytes.length > 0 ? abi.decode(npmBytes, (address)) : DEFAULT_ALGEBRA_NPM;
        }
        require(algebraNpm != address(0), "WithdrawLP: algebraNpm is zero");

        // operatorLpKey from manifest = the LP NFT owner = the address that MUST broadcast this.
        address operatorLpKey = abi.decode(raw.parseRaw(".operatorLpKey"), (address));
        require(operatorLpKey != address(0), "WithdrawLP: operatorLpKey is zero");

        console2.log("=== WithdrawLP (Seam D re-seed, step 1/2) ===");
        console2.log("algebraNpm:", algebraNpm);
        console2.log("operatorLpKey (LP NFT owner / required broadcaster):", operatorLpKey);

        INonfungiblePositionManager npm = INonfungiblePositionManager(algebraNpm);

        vm.startBroadcast();

        for (uint256 i = 0; i < LP_NFT_IDS.length; i++) {
            uint256 tokenId = LP_NFT_IDS[i];
            console2.log("");
            console2.log("--- LP NFT tokenId ---", tokenId);

            // ── Step 1: read current liquidity (Algebra 11-field layout; liquidity is the 7th value) ──
            (,, address token0, address token1,,, uint128 liquidity,,,,) = npm.positions(tokenId);
            console2.log("  token0:", token0);
            console2.log("  token1:", token1);
            console2.log("  liquidity:", uint256(liquidity));

            // ── Step 2: idempotent skip if already empty (re-runnable after a partial broadcast) ──
            if (liquidity == 0) {
                console2.log("  liquidity == 0 -> SKIP (already withdrawn)");
                continue;
            }

            // ── Step 3: decrease ALL liquidity (min 0 — we are exiting; slippage is acceptable) ──
            INonfungiblePositionManager.DecreaseLiquidityParams memory dec =
                INonfungiblePositionManager.DecreaseLiquidityParams({
                    tokenId: tokenId,
                    liquidity: liquidity,
                    amount0Min: 0,
                    amount1Min: 0,
                    deadline: block.timestamp + 600
                });
            (uint256 amount0Dec, uint256 amount1Dec) = npm.decreaseLiquidity(dec);
            console2.log("  decreaseLiquidity amount0:", amount0Dec);
            console2.log("  decreaseLiquidity amount1:", amount1Dec);

            // ── Step 4: collect BOTH legs (and any accrued fees) to operatorLpKey ──
            INonfungiblePositionManager.CollectParams memory col = INonfungiblePositionManager.CollectParams({
                tokenId: tokenId, recipient: operatorLpKey, amount0Max: type(uint128).max, amount1Max: type(uint128).max
            });
            (uint256 collected0, uint256 collected1) = npm.collect(col);
            console2.log("  collected token0 -> operatorLpKey:", collected0);
            console2.log("  collected token1 -> operatorLpKey:", collected1);
        }

        vm.stopBroadcast();

        console2.log("");
        console2.log("=== WithdrawLP complete: positions 103/104/105 unwound to operatorLpKey ===");
        console2.log("Next: re-run 02-SeedPools.s.sol (fixed tick range) to re-seed two-sided LP at NAV.");
    }
}

/// @notice Minimal Algebra Integral v1 NonfungiblePositionManager interface for LP unwind.
/// @dev positions / decreaseLiquidity / collect shapes verified against the live Sepolia NPM
///      (see the WithdrawLP contract NatSpec). decreaseLiquidity and collect take the SAME struct
///      shapes as Uniswap V3 (neither involves the fee field); only positions differs (no fee field).
interface INonfungiblePositionManager {
    /// @dev Algebra Integral v1 layout — NO `fee` field (dynamic pool fee). 11 return values.
    function positions(uint256 tokenId)
        external
        view
        returns (
            uint96 nonce,
            address operator,
            address token0,
            address token1,
            int24 tickLower,
            int24 tickUpper,
            uint128 liquidity,
            uint256 feeGrowthInside0LastX128,
            uint256 feeGrowthInside1LastX128,
            uint128 tokensOwed0,
            uint128 tokensOwed1
        );

    struct DecreaseLiquidityParams {
        uint256 tokenId;
        uint128 liquidity;
        uint256 amount0Min;
        uint256 amount1Min;
        uint256 deadline;
    }

    function decreaseLiquidity(DecreaseLiquidityParams calldata params)
        external
        payable
        returns (uint256 amount0, uint256 amount1);

    struct CollectParams {
        uint256 tokenId;
        address recipient;
        uint128 amount0Max;
        uint128 amount1Max;
    }

    function collect(CollectParams calldata params) external payable returns (uint256 amount0, uint256 amount1);
}
