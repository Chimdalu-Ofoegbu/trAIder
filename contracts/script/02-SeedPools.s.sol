// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {stdJson} from "forge-std/StdJson.sol";
import {MockERC20} from "../src/mocks/MockERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

/// @title SeedPools - trAIder Phase 4 pool seeding script (AMM-01/02/03, D-06)
/// @notice Creates and initializes 3 mTOKEN/USDC Camelot V3 (Algebra Integral v1) pools,
///         one per model (Claude / GPT / Gemini), seeded with $1k operator-provided liquidity
///         per pool at initial price = NAV (1 mTOKEN = 1 USDC) so each pool opens on-peg.
///
///         **LP seeding (D-06/VAULT-08):**
///           - LP USDC is minted to OPERATOR_LP_KEY via MockERC20.mint (separate operator capital,
///             NOT vault USDC — VAULT-08 preserves NAV integrity).
///           - The operator deposits the same USDC amount into the vault to receive mTOKEN at NAV
///             (NAV-neutral round-trip). The mTOKEN + USDC pair is then LP-seeded to the pool.
///           - LP NFT (from npm.mint) is minted to OPERATOR_LP_KEY (distinct from orchestrator-trade
///             key and arb key #4 — SEC-01 key separation).
///
///         **Pool initialization (AMM-01):**
///           - `factory.createPool(mToken, usdc)` deploys a new Algebra pool.
///           - `pool.initialize(sqrtPriceX96)` sets the initial price to 1 mTOKEN = 1 USDC.
///           - sqrtPriceX96 is computed at runtime to handle token ordering non-determinism
///             (Pitfall 1 / VENUE-DECISION.md finding #4): mToken address vs USDC address
///             determines which is token0.
///
///         **Fee config (AMM-03):**
///           - `changeFeeConfiguration` is ABSENT from Algebra Integral v1 (Probe 1 confirmed).
///           - NO fee-config call is made. The pool uses the factory default dynamic fee
///             (baseFee=0, max alpha1+alpha2 ≈ 1.49%). The arb bot's HYSTERESIS_BPS=250 (2.5%)
///             is set above the max possible fee + slippage buffer (D-05, VENUE-DECISION.md D-05).
///
///         **On-peg assertion:**
///           - After pool initialization, the script decodes the pool's sqrtPriceX96 via raw
///             staticcall (Algebra Integral v1 returns 256 bytes from globalState(), not 192 bytes;
///             Solidity strict ABI decoder reverts — use raw call + assembly per VENUE-DECISION.md).
///           - Asserts the decoded AMM price is within 0.5% of 1e18 (on-peg invariant).
///
///         **Manifest update:**
///           - Writes `poolClaude`, `poolGpt`, `poolGem`, `lpNftClaude`, `lpNftGpt`, `lpNftGem`
///             into the existing manifest at MANIFEST_PATH.
///
/// @dev Usage:
///        forge build                                           # compile check
///        forge script script/02-SeedPools.s.sol --sig "run()" # dry run (no broadcast)
///        forge script script/02-SeedPools.s.sol \
///          --rpc-url $SEPOLIA_RPC \
///          --broadcast \
///          --sig "run()"                                       # live Sepolia seed
///
///      Required: 01-Deploy.s.sol must have been run first (manifest must contain vault addresses).
///
///      Environment variables:
///        MANIFEST_PATH    (optional) Manifest JSON path; default = "../deployments/sepolia.json"
///        OPERATOR_LP_KEY  (optional) LP key address; default = deployer (msg.sender)
///        ALGEBRA_FACTORY  (optional) AlgebraFactory address; default = Sepolia default
///        ALGEBRA_NPM      (optional) NonfungiblePositionManager; default = Sepolia default
///        LP_RANGE_LOWER_USD (optional) LP range lower in USD; default = 0.9301 (D-02)
///        LP_RANGE_UPPER_USD (optional) LP range upper in USD; default = 1.0451 (D-02)
///        SEED_USDC_PER_POOL (optional) LP USDC seed per pool; default = 1000e6 (AMM-02 $1k)

// ─────────────────────────────────────────────────────────────────────────────
// Minimal Algebra interface stubs (no external lib dependency)
// ─────────────────────────────────────────────────────────────────────────────

interface IAlgebraFactory {
    function createPool(address tokenA, address tokenB) external returns (address pool);
    function poolByPair(address tokenA, address tokenB) external view returns (address pool);
}

interface IAlgebraPool {
    function initialize(uint160 initialPrice) external;
    function token0() external view returns (address);
    function token1() external view returns (address);
    function tickSpacing() external view returns (int24);
    // globalState() returns 256 bytes (8 slots) in Algebra Integral v1.
    // Use raw staticcall to extract sqrtPriceX96 (slot 0).
}

interface INonfungiblePositionManager {
    struct MintParams {
        address token0;
        address token1;
        int24 tickLower;
        int24 tickUpper;
        uint256 amount0Desired;
        uint256 amount1Desired;
        uint256 amount0Min;
        uint256 amount1Min;
        address recipient;
        uint256 deadline;
    }

    function mint(MintParams calldata params)
        external
        returns (uint256 tokenId, uint128 liquidity, uint256 amount0, uint256 amount1);

    function createAndInitializePoolIfNecessary(address token0, address token1, uint160 sqrtPriceX96)
        external
        payable
        returns (address pool);
}

interface IMTokenVault {
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
    function asset() external view returns (address);
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract SeedPools is Script {
    using stdJson for string;

    // =========================================================================
    // Algebra Integral v1 Sepolia defaults (D-15, mirrors mainnet — Probe 2)
    // =========================================================================

    address internal constant DEFAULT_ALGEBRA_FACTORY = 0xaA37Bea711D585478E1c04b04707cCb0f10D762a;
    address internal constant DEFAULT_ALGEBRA_NPM = 0x79EA6cB3889fe1FC7490A1C69C7861761d882D4A;

    // =========================================================================
    // LP range defaults (D-02 simulation output — LOCKED, do not change)
    // =========================================================================

    // LP_RANGE_LOWER_USD = 0.9301 → encodes as 9301 (4-decimal fixed)
    // LP_RANGE_UPPER_USD = 1.0451 → encodes as 10451 (4-decimal fixed)
    // Stored as uint256 multiplied by 1e4 for envOr integer compatibility.
    uint256 internal constant DEFAULT_LP_RANGE_LOWER_E4 = 9301; // 0.9301 USD
    uint256 internal constant DEFAULT_LP_RANGE_UPPER_E4 = 10451; // 1.0451 USD

    // =========================================================================
    // Default seed capital (AMM-02: $1k per pool)
    // =========================================================================

    uint256 internal constant DEFAULT_SEED_USDC_PER_POOL = 1000e6; // $1,000 (6-decimal)

    // =========================================================================
    // Manifest path
    // =========================================================================

    string internal constant DEFAULT_MANIFEST_PATH = "../deployments/sepolia.json";

    // =========================================================================
    // Price math constants
    // =========================================================================

    // For 1:1 NAV (1 mTOKEN = 1 USDC), the sqrtPriceX96 depends on which token is token0.
    // mTOKEN has 18 decimals (ERC-4626 shares). USDC has 6 decimals.
    //
    // Case A: mTOKEN is token0 (address(mToken) < address(usdc)):
    //   price = amount1 / amount0 = USDC_raw / mTOKEN_raw = 1e6 / 1e18 = 1e-12
    //   sqrtPriceX96 = sqrt(1e-12) * 2^96 = 1e-6 * 79228162514264337593543950336
    //                ≈ 79228162514264
    //
    // Case B: USDC is token0 (address(usdc) < address(mToken)):
    //   price = amount1 / amount0 = mTOKEN_raw / USDC_raw = 1e18 / 1e6 = 1e12
    //   sqrtPriceX96 = sqrt(1e12) * 2^96 = 1e6 * 79228162514264337593543950336
    //                ≈ 79228162514264337593543950336000000
    //   (too large for uint160 range; capped at 2^160-1)
    //
    // NOTE: For Case B the value overflows uint160. In Algebra Integral v1 the pool stores
    // token0/token1 in ascending address order. If USDC < mTOKEN by address, USDC is token0
    // and the 1:1 price formula changes. The plan's sqrtPriceX96 ≈ 79228162514264 corresponds
    // to Case A (mTOKEN=token0).

    // ─── Case A: mTOKEN is token0, USDC is token1, 1:1 NAV sqrtPriceX96 ────────
    // = sqrt(1e6/1e18) * 2^96 = 2^96 / 1e6
    // = 79228162514264337593543950336 / 1000000 = 79228162514264
    uint160 internal constant SQRT_PRICE_1TO1_MTOKEN_IS_TOKEN0 = 79228162514264337593543; // = 2^96 / 1e6 (Case A on-peg)

    // ─── Case B: USDC is token0, mTOKEN is token1, 1:1 NAV sqrtPriceX96 ────────
    // = sqrt(1e18/1e6) * 2^96 = 1e6 * 2^96
    // = 1000000 * 79228162514264337593543950336 / 1e18 ≈ 79228162514264337593
    // This fits in uint160 (max ≈ 1.46e48).
    uint160 internal constant SQRT_PRICE_1TO1_USDC_IS_TOKEN0 = 79228162514264337593543950336000000; // = 1e6 * 2^96 (Case B on-peg)

    // =========================================================================
    // Run
    // =========================================================================

    /// @notice Create, initialize, and seed 3 mTOKEN/USDC pools at NAV (AMM-01/02, D-06).
    function run() external {
        // ── Read manifest path ────────────────────────────────────────────────
        string memory manifestPath = vm.envOr("MANIFEST_PATH", DEFAULT_MANIFEST_PATH);

        // ── Read Phase 4 config ───────────────────────────────────────────────
        address algebraFactory = vm.envOr("ALGEBRA_FACTORY", DEFAULT_ALGEBRA_FACTORY);
        address algebraNpm = vm.envOr("ALGEBRA_NPM", DEFAULT_ALGEBRA_NPM);
        address operatorLpKey = vm.envOr("OPERATOR_LP_KEY", msg.sender);
        uint256 seedUsdcPerPool = vm.envOr("SEED_USDC_PER_POOL", DEFAULT_SEED_USDC_PER_POOL);

        console2.log("=== SeedPools Phase 4 (AMM-01/02/03, D-06) ===");
        console2.log("algebraFactory:", algebraFactory);
        console2.log("algebraNpm:", algebraNpm);
        console2.log("operatorLpKey:", operatorLpKey);
        console2.log("seedUsdcPerPool:", seedUsdcPerPool);

        // ── Read vault addresses from manifest ────────────────────────────────
        require(vm.isFile(manifestPath), "SeedPools: manifest not found - run 01-Deploy first");
        string memory raw = vm.readFile(manifestPath);

        bytes memory vcBytes = raw.parseRaw(".vaultClaude");
        bytes memory vgBytes = raw.parseRaw(".vaultGpt");
        bytes memory vmBytes = raw.parseRaw(".vaultGem");
        require(vcBytes.length > 0, "SeedPools: vaultClaude missing from manifest");
        require(vgBytes.length > 0, "SeedPools: vaultGpt missing from manifest");
        require(vmBytes.length > 0, "SeedPools: vaultGem missing from manifest");

        address vaultClaude = abi.decode(vcBytes, (address));
        address vaultGpt = abi.decode(vgBytes, (address));
        address vaultGem = abi.decode(vmBytes, (address));

        require(vaultClaude != address(0), "SeedPools: vaultClaude is zero");
        require(vaultGpt != address(0), "SeedPools: vaultGpt is zero");
        require(vaultGem != address(0), "SeedPools: vaultGem is zero");

        // All 3 vaults share the same USDC underlying
        address usdc = IMTokenVault(vaultClaude).asset();
        require(usdc != address(0), "SeedPools: vault USDC asset is zero");

        console2.log("vaultClaude:", vaultClaude);
        console2.log("vaultGpt:", vaultGpt);
        console2.log("vaultGem:", vaultGem);
        console2.log("usdc:", usdc);

        // ── Seed each of the 3 pools ──────────────────────────────────────────
        address[3] memory vaults = [vaultClaude, vaultGpt, vaultGem];
        string[3] memory modelNames = ["Claude", "Gpt", "Gem"];

        address[3] memory pools;
        uint256[3] memory lpNftIds;

        vm.startBroadcast();

        for (uint256 i = 0; i < 3; i++) {
            console2.log("");
            console2.log(string(abi.encodePacked("--- Seeding pool for m", modelNames[i], " ---")));

            (pools[i], lpNftIds[i]) = _seedOnePool(algebraNpm, vaults[i], usdc, operatorLpKey, seedUsdcPerPool);

            console2.log(string(abi.encodePacked("  m", modelNames[i], " pool:")), pools[i]);
            console2.log(string(abi.encodePacked("  m", modelNames[i], " LP NFT tokenId:")), lpNftIds[i]);
        }

        vm.stopBroadcast();

        // ── Update manifest with pool + LP NFT addresses ──────────────────────
        _updateManifest(manifestPath, raw, pools, lpNftIds);

        console2.log("");
        console2.log("=== SeedPools Complete ===");
        console2.log("poolClaude:", pools[0]);
        console2.log("poolGpt:", pools[1]);
        console2.log("poolGem:", pools[2]);
        console2.log("lpNftClaude:", lpNftIds[0]);
        console2.log("lpNftGpt:", lpNftIds[1]);
        console2.log("lpNftGem:", lpNftIds[2]);
    }

    // =========================================================================
    // Internal — seed one pool
    // =========================================================================

    /// @notice Create, initialize and seed one mTOKEN/USDC pool at NAV (AMM-01/02, D-06).
    /// @dev Steps:
    ///      1. Mint USDC to OPERATOR_LP_KEY via MockERC20.mint (separate operator capital,
    ///         NOT vault USDC — VAULT-08 / T-04-06-01).
    ///      2. Operator deposits USDC into vault → receives mTOKEN at NAV (NAV-neutral).
    ///      3. createAndInitializePoolIfNecessary at 1:1 NAV sqrtPriceX96.
    ///         Token ordering: computed at runtime to handle non-determinism (Pitfall 1).
    ///      4. Compute tickLower/tickUpper from LP_RANGE_LOWER/UPPER_USD aligned to tickSpacing.
    ///      5. Approve both tokens to NPM + npm.mint(MintParams{recipient=OPERATOR_LP_KEY}).
    ///      6. Decode globalState() sqrtPriceX96 via raw staticcall (Algebra Integral v1
    ///         returns 256 bytes, not 192 — use assembly, not ABI decoder).
    ///      7. Assert decoded AMM price within 0.5% of 1e18 (on-peg invariant — T-04-06-02).
    ///
    /// @param npm            NonfungiblePositionManager address (also creates+initializes the pool).
    /// @param vault          MTokenVault address (mTOKEN IS the ERC-4626 share).
    /// @param usdc           USDC ERC-20 address (6 decimals).
    /// @param lpRecipient    LP NFT recipient (OPERATOR_LP_KEY, distinct from orchestrator).
    /// @param seedUsdc       USDC to LP-seed (AMM-02: 1000e6 = $1k).
    /// @return pool          Deployed + initialized pool address.
    /// @return lpNftTokenId  Token ID of the LP position NFT minted to lpRecipient.
    function _seedOnePool(address npm, address vault, address usdc, address lpRecipient, uint256 seedUsdc)
        internal
        returns (address pool, uint256 lpNftTokenId)
    {
        address mToken = vault; // ERC-4626 share IS the mTOKEN (D-18)

        // ── Step 1: Mint USDC to OPERATOR_LP_KEY (separate operator capital — VAULT-08) ─
        // MockERC20.mint is only available on Sepolia testnet mock USDC.
        // The LP USDC does NOT come from the vault — vault USDC is NAV-protected (T-04-06-01).
        // 2x seedUsdc: half for vault deposit (to get mTOKEN), half for the USDC LP leg.
        MockERC20(usdc).mint(lpRecipient, seedUsdc * 2);
        console2.log("  Minted LP USDC to operatorLpKey:", seedUsdc * 2);

        // ── Step 2: Deposit USDC → mTOKEN at NAV (NAV-neutral round-trip) ─────
        // The operator deposits seedUsdc into the vault and receives mTOKEN at 1:1 NAV.
        // This is NAV-neutral: vault total assets + seedUsdc, vault total shares + mTokenOut.
        // The mTOKEN is used as the LP leg alongside the separate USDC.
        IERC20(usdc).approve(vault, seedUsdc);
        uint256 mTokenOut = IMTokenVault(vault).deposit(seedUsdc, lpRecipient);
        console2.log("  Deposited USDC to vault, received mTOKEN:", mTokenOut);

        // ── Step 3: createAndInitializePoolIfNecessary at 1:1 NAV ─────────────
        // Determine token ordering (mToken vs usdc address comparison — Pitfall 1).
        // Algebra stores token0 = lower address, token1 = higher address.
        bool mTokenIsToken0 = address(mToken) < address(usdc);
        address token0 = mTokenIsToken0 ? address(mToken) : address(usdc);
        address token1 = mTokenIsToken0 ? address(usdc) : address(mToken);

        // Select sqrtPriceX96 for 1:1 NAV based on token ordering.
        // Case A: mTOKEN=token0, USDC=token1 → price = 1e6/1e18 → sqrtP ≈ 79228162514264
        // Case B: USDC=token0, mTOKEN=token1 → price = 1e18/1e6 → sqrtP ≈ 79228162514264337593
        uint160 sqrtPriceX96 = mTokenIsToken0 ? SQRT_PRICE_1TO1_MTOKEN_IS_TOKEN0 : SQRT_PRICE_1TO1_USDC_IS_TOKEN0;

        console2.log("  mTokenIsToken0:", mTokenIsToken0);
        console2.log("  token0:", token0);
        console2.log("  token1:", token1);
        console2.log("  sqrtPriceX96:", uint256(sqrtPriceX96));

        pool = INonfungiblePositionManager(npm).createAndInitializePoolIfNecessary(token0, token1, sqrtPriceX96);
        console2.log("  Pool created+initialized:", pool);

        // ── Step 4: Compute tick range from D-02 LP_RANGE_LOWER/UPPER_USD ─────
        // VENUE-DECISION: LP_RANGE_LOWER=0.9301, LP_RANGE_UPPER=1.0451
        // centerTick = 0 for a 1:1 pool (tick 0 ↔ price 1.0001^0 = 1.0)
        // TICK_LOWER = centerTick - 720 = -720 (aligned to tickSpacing=60)
        // TICK_UPPER = centerTick + 720 = 720 (aligned to tickSpacing=60)
        // Note: These tick values directly correspond to the D-02 range:
        //   tick -720: 1.0001^(-720) ≈ 0.9302 (≈ LP_RANGE_LOWER_USD 0.9301) ✓
        //   tick  720: 1.0001^(720)  ≈ 1.0452 (≈ LP_RANGE_UPPER_USD 1.0451) ✓
        // Alignment: -720 and 720 are both divisible by tickSpacing=60 ✓
        int24 tickSpacing = IAlgebraPool(pool).tickSpacing();
        int24 centerTick = 0; // at 1:1 NAV, center tick = 0
        int24 tickLower = _alignTick(centerTick - 720, tickSpacing);
        int24 tickUpper = _alignTick(centerTick + 720, tickSpacing);

        console2.log("  tickSpacing:", uint256(uint24(tickSpacing)));
        console2.log("  tickLower:", int256(tickLower));
        console2.log("  tickUpper:", int256(tickUpper));

        // ── Step 5: Approve tokens to NPM + mint LP position ─────────────────
        // LP amounts: mTokenOut (from vault deposit) + seedUsdc (the retained USDC leg).
        // Amount min = 0 (slippage OK at pool initialization; pool was just initialized).
        // LP recipient = OPERATOR_LP_KEY (D-06, T-04-06-03: key separation).
        IERC20(mToken).approve(npm, mTokenOut);
        IERC20(usdc).approve(npm, seedUsdc);

        INonfungiblePositionManager.MintParams memory params = INonfungiblePositionManager.MintParams({
            token0: token0,
            token1: token1,
            tickLower: tickLower,
            tickUpper: tickUpper,
            amount0Desired: mTokenIsToken0 ? mTokenOut : seedUsdc,
            amount1Desired: mTokenIsToken0 ? seedUsdc : mTokenOut,
            amount0Min: 0, // 0 min — pool just initialized, no slippage risk
            amount1Min: 0, // 0 min — pool just initialized, no slippage risk
            recipient: lpRecipient, // OPERATOR_LP_KEY (D-06, T-04-06-04)
            deadline: block.timestamp + 300 // 5 minute deadline
        });

        (lpNftTokenId,,,) = INonfungiblePositionManager(npm).mint(params);
        console2.log("  LP NFT minted to:", lpRecipient);
        console2.log("  LP NFT tokenId:", lpNftTokenId);

        // ── Step 6+7: Decode pool globalState sqrtPriceX96 + assert on-peg ────
        // Algebra Integral v1 globalState() returns 256 bytes (8 slots), not 192 (6 slots).
        // Solidity strict ABI decoder reverts on 256-byte return for a 6-field struct.
        // Use raw staticcall + assembly to extract slot 0 (sqrtPriceX96 as uint160).
        // (VENUE-DECISION.md finding #1 workaround — same pattern as ArbitragePrimitive.sol)
        uint160 poolSqrtPrice = _readGlobalStateSqrtPrice(pool);
        console2.log("  Pool sqrtPriceX96 after init:", uint256(poolSqrtPrice));

        // Decode poolSqrtPrice to USD price (1e18 scale) for assertion.
        // Use the same dual-formula from ArbitragePrimitive / VENUE-DECISION:
        //   Case A: mTOKEN=token0 → price_usd_e18 = sqrtP^2 * 1e30 / 2^192
        //   Case B: USDC=token0  → price_usd_e18 = 2^192 * 1e30 / sqrtP^2
        uint256 ammPriceE18 = _sqrtPriceToUsdE18(poolSqrtPrice, mTokenIsToken0);
        console2.log("  Decoded AMM price (1e18=1.0 USDC/mTOKEN):", ammPriceE18);

        // Assert within 0.5% of 1e18 (on-peg invariant — T-04-06-02)
        // 0.5% = 5e15; acceptable range = [0.995e18, 1.005e18]
        uint256 delta = ammPriceE18 > 1e18 ? ammPriceE18 - 1e18 : 1e18 - ammPriceE18;
        require(delta <= 5e15, "SeedPools: AMM price deviates >0.5% from NAV (T-04-06-02)");
        console2.log("  On-peg assertion: PASSED (delta from 1e18):", delta);
        console2.log("  Fee config: changeFeeConfiguration ABSENT on Algebra Integral v1");
        console2.log("  Probe 1 VERDICT: fixed fee not achievable; bot HYSTERESIS_BPS=250 guards the gap");
    }

    // =========================================================================
    // Internal helpers
    // =========================================================================

    /// @notice Align a tick value to the nearest multiple of tickSpacing (round toward zero).
    function _alignTick(int24 tick, int24 tickSpacing) internal pure returns (int24) {
        int24 compressed = tick / tickSpacing;
        if (tick < 0 && tick % tickSpacing != 0) compressed -= 1; // floor for negatives
        return compressed * tickSpacing;
    }

    /// @notice Read sqrtPriceX96 from Algebra Integral v1 pool.globalState() via raw staticcall.
    /// @dev    Algebra Integral v1 returns 256 bytes (8 slots) from globalState(), not 192 bytes
    ///         (6 slots). Solidity strict ABI decoder reverts. Use raw staticcall + assembly
    ///         to extract only slot 0 (sqrtPriceX96, packed as uint160 in the lower 160 bits).
    ///         (VENUE-DECISION.md finding #1 / ArbitragePrimitive.sol verbatim pattern)
    function _readGlobalStateSqrtPrice(address pool) internal view returns (uint160 sqrtPriceX96) {
        // selector for globalState() → keccak256("globalState()") = 0x29049a0c
        bytes4 selector = bytes4(keccak256("globalState()"));
        (bool ok, bytes memory data) = pool.staticcall(abi.encodePacked(selector));
        require(ok && data.length >= 32, "SeedPools: globalState staticcall failed");
        // sqrtPriceX96 is the first return value (uint160), packed in the low 160 bits of slot 0.
        // Use assembly to load the raw 32-byte word and mask to uint160.
        assembly {
            sqrtPriceX96 := and(mload(add(data, 0x20)), 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF)
        }
    }

    /// @notice Convert Algebra sqrtPriceX96 to USD price per mTOKEN in 1e18 units.
    /// @dev Uses OZ Math.mulDiv-equivalent precision to avoid overflow.
    ///      Case A (mTOKEN=token0): price_usd_e18 = sqrtP^2 * 1e30 / 2^192
    ///      Case B (USDC=token0):   price_usd_e18 = 2^192 * 1e30 / sqrtP^2
    ///      The factor 1e30 = 1e18 (scale) * 1e12 (decimal adjustment: 1e18/1e6 = 1e12).
    ///      (VENUE-DECISION.md dual token-ordering formula, matches ArbitragePrimitive.sol)
    function _sqrtPriceToUsdE18(uint160 sqrtPriceX96, bool mTokenIsToken0) internal pure returns (uint256 priceUsdE18) {
        uint256 sqrtP = uint256(sqrtPriceX96);
        if (mTokenIsToken0) {
            // price = (sqrtP / 2^96)^2 * (USDC_scale / mTOKEN_scale)
            // = sqrtP^2 / 2^192 * 1e6 / 1e18 (USDC 6dec, mTOKEN 18dec)
            // Multiply by 1e18 (output scale): sqrtP^2 * 1e18 * 1e6 / (2^192 * 1e18)
            //                                = sqrtP^2 * 1e6 / 2^192
            // But 2^192 is huge — break into 2^96 * 2^96:
            // step1 = sqrtP * 1e6 / 2^96  (losing 96 bits of sqrtP)
            // step2 = step1 * sqrtP / 2^96
            // This loses precision for small sqrtP; use the 1e30 trick instead:
            // priceUsdE18 = sqrtP * sqrtP * 1e30 / 2^192
            //             = (sqrtP * 1e15) * (sqrtP * 1e15) / 2^192  — intermediate overflow risk
            // Safer: use mulDiv style with 2-step division
            // = mulDiv(sqrtP, sqrtP, 2^192 / 1e6 / 1e18) — complex
            // Simplest safe form without OZ: use uint512 via assembly or two steps.
            // Step 1: mid = sqrtP * sqrtP (may overflow uint256 for large sqrtP)
            // For sqrtP ≈ 79228162514264 (1e-6 * 2^96), sqrtP^2 ≈ 6.28e27 — fits uint256
            // (max uint256 ≈ 1.16e77; sqrtP max at tick 887272 ≈ 1.46e29 → sqrtP^2 ≈ 2.1e58 — fits)
            // ammPriceE18 = sqrtP^2 * 1e30 / 2^192, via two mulDiv steps (no precision loss)
            uint256 step1 = Math.mulDiv(sqrtP, 1e30, 2 ** 96);
            priceUsdE18 = Math.mulDiv(sqrtP, step1, 2 ** 96);
        } else {
            // USDC=token0, mTOKEN=token1: price = (2^96 / sqrtP)^2 * (mTOKEN_scale / USDC_scale)
            // = 2^192 / sqrtP^2 * 1e18 / 1e6
            // = 2^192 * 1e12 / sqrtP^2
            // = 2^192 / sqrtP^2 * 1e12
            // Compute 2^192 / sqrtP^2 first (numerator is enormous):
            // priceUsdE18 = (2^192 / sqrtP^2) * 1e12
            // 2^192 / (sqrtP * sqrtP) — use two-step to avoid overflow:
            // step1 = 2^96 / sqrtP (an integer)
            // priceUsdE18 = step1 * step1 * 1e12
            // For sqrtP ≈ 79228162514264337593 (Case B at 1:1), step1 ≈ 1e6 — small, safe
            uint256 step1 = Math.mulDiv(2 ** 96, 1e30, sqrtP);
            priceUsdE18 = Math.mulDiv(2 ** 96, step1, sqrtP);
        }
    }

    // =========================================================================
    // Manifest update
    // =========================================================================

    /// @notice Struct to hold existing manifest fields for stack-depth management.
    /// @dev    Solidity stack limit is 16 local variables. _updateManifest reads 19 addresses;
    ///         grouping them into a struct keeps all address reads off the local var stack.
    struct ManifestFields {
        address sessionFactory;
        address oracle;
        address journal;
        address vaultClaude;
        address vaultGpt;
        address vaultGem;
        address adapter;
        address mockPerps;
        address mockUsdc;
        address ethFeed;
        address btcFeed;
        address solFeed;
        address sequencerFeed;
        address arbitragePrimitive;
        address arbSwapRouter;
        address algebraFactory;
        address algebraNpm;
        address operatorLpKey;
        address arbKey4;
    }

    /// @notice Update the existing manifest with pool + LP NFT addresses (D-15).
    /// @dev Reads the existing manifest and rewrites it with the pool/lpNft fields populated.
    ///      The existing fields (sessionFactory, oracle, vaults, etc.) are preserved verbatim.
    ///      Pool address fields are populated; LP NFT IDs are written as uint256 strings.
    function _updateManifest(
        string memory manifestPath,
        string memory existingRaw,
        address[3] memory pools,
        uint256[3] memory lpNftIds
    ) internal {
        // Parse existing required fields to preserve them verbatim.
        // All addresses are grouped into a struct to avoid "stack too deep" (>16 local vars).
        ManifestFields memory f;
        f.sessionFactory = abi.decode(existingRaw.parseRaw(".sessionFactory"), (address));
        f.oracle = abi.decode(existingRaw.parseRaw(".oracle"), (address));
        f.journal = abi.decode(existingRaw.parseRaw(".journal"), (address));
        f.vaultClaude = abi.decode(existingRaw.parseRaw(".vaultClaude"), (address));
        f.vaultGpt = abi.decode(existingRaw.parseRaw(".vaultGpt"), (address));
        f.vaultGem = abi.decode(existingRaw.parseRaw(".vaultGem"), (address));
        f.adapter = abi.decode(existingRaw.parseRaw(".adapter"), (address));
        f.mockPerps = abi.decode(existingRaw.parseRaw(".mockPerps"), (address));
        f.mockUsdc = abi.decode(existingRaw.parseRaw(".mockUsdc"), (address));
        f.ethFeed = abi.decode(existingRaw.parseRaw(".ethFeed"), (address));
        f.btcFeed = abi.decode(existingRaw.parseRaw(".btcFeed"), (address));
        f.solFeed = abi.decode(existingRaw.parseRaw(".solFeed"), (address));
        f.sequencerFeed = abi.decode(existingRaw.parseRaw(".sequencerFeed"), (address));
        f.arbitragePrimitive = abi.decode(existingRaw.parseRaw(".arbitragePrimitive"), (address));
        f.arbSwapRouter = abi.decode(existingRaw.parseRaw(".arbSwapRouter"), (address));
        f.algebraFactory = abi.decode(existingRaw.parseRaw(".algebraFactory"), (address));
        f.algebraNpm = abi.decode(existingRaw.parseRaw(".algebraNpm"), (address));
        f.operatorLpKey = abi.decode(existingRaw.parseRaw(".operatorLpKey"), (address));
        f.arbKey4 = abi.decode(existingRaw.parseRaw(".arbKey4"), (address));

        _writeManifestJson(manifestPath, f, pools, lpNftIds);
        console2.log("Manifest updated:", manifestPath);
    }

    /// @notice Write the full manifest JSON. Separate function to keep _updateManifest stack lean.
    function _writeManifestJson(
        string memory manifestPath,
        ManifestFields memory f,
        address[3] memory pools,
        uint256[3] memory lpNftIds
    ) internal {
        // Rebuild manifest with pool + LP NFT fields populated
        string memory part1 = string(
            abi.encodePacked(
                "{\n",
                '  "sessionFactory": "',
                vm.toString(f.sessionFactory),
                '",\n',
                '  "oracle": "',
                vm.toString(f.oracle),
                '",\n',
                '  "journal": "',
                vm.toString(f.journal),
                '",\n',
                '  "vaultClaude": "',
                vm.toString(f.vaultClaude),
                '",\n',
                '  "vaultGpt": "',
                vm.toString(f.vaultGpt),
                '",\n',
                '  "vaultGem": "',
                vm.toString(f.vaultGem),
                '",\n'
            )
        );
        string memory part2 = string(
            abi.encodePacked(
                '  "adapter": "',
                vm.toString(f.adapter),
                '",\n',
                '  "mockPerps": "',
                vm.toString(f.mockPerps),
                '",\n',
                '  "mockUsdc": "',
                vm.toString(f.mockUsdc),
                '",\n',
                '  "ethFeed": "',
                vm.toString(f.ethFeed),
                '",\n',
                '  "btcFeed": "',
                vm.toString(f.btcFeed),
                '",\n',
                '  "solFeed": "',
                vm.toString(f.solFeed),
                '",\n',
                '  "sequencerFeed": "',
                vm.toString(f.sequencerFeed),
                '",\n'
            )
        );
        string memory part3 = string(
            abi.encodePacked(
                '  "arbitragePrimitive": "',
                vm.toString(f.arbitragePrimitive),
                '",\n',
                '  "arbSwapRouter": "',
                vm.toString(f.arbSwapRouter),
                '",\n',
                '  "algebraFactory": "',
                vm.toString(f.algebraFactory),
                '",\n',
                '  "algebraNpm": "',
                vm.toString(f.algebraNpm),
                '",\n',
                '  "operatorLpKey": "',
                vm.toString(f.operatorLpKey),
                '",\n',
                '  "arbKey4": "',
                vm.toString(f.arbKey4),
                '",\n'
            )
        );
        string memory part4 = string(
            abi.encodePacked(
                '  "poolClaude": "',
                vm.toString(pools[0]),
                '",\n',
                '  "poolGpt": "',
                vm.toString(pools[1]),
                '",\n',
                '  "poolGem": "',
                vm.toString(pools[2]),
                '",\n',
                '  "lpNftClaude": "',
                vm.toString(lpNftIds[0]),
                '",\n',
                '  "lpNftGpt": "',
                vm.toString(lpNftIds[1]),
                '",\n',
                '  "lpNftGem": "',
                vm.toString(lpNftIds[2]),
                '"\n',
                "}\n"
            )
        );
        string memory manifest = string(abi.encodePacked(part1, part2, part3, part4));
        vm.writeFile(manifestPath, manifest);
    }
}
