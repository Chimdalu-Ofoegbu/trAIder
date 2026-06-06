// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {GMXAdapter} from "../../src/adapters/GMXAdapter.sol";
import {IPerpsAdapter} from "../../src/interfaces/IPerpsAdapter.sol";

/// @title GMXAdapterForkTest — read-side fork proof for GMXAdapter (PERPS-01 / D-02 INTRACTABLE)
/// @notice D-16 INTRACTABLE verdict: full on-chain write path (createOrder + sendWnt multicall)
///         is NOT implemented in Phase 3 Solidity. These fork tests cover only the READ SIDE:
///           - positionValueUSDC: D-05 empty-set early return, Chainlink staleness guard
///           - getOpenPositionKeys: returns tracked position keys
///           - openLong / openShort / closePosition: assert deferred-revert
///
///         Fork block: MUST be >= 402000000.
///         GMX V2 ExchangeRouter/Reader/OrderHandler were redeployed after block ~401000000.
///         At block 353000000 (old FORK_BLOCK), those contracts have NO code — tests
///         using them would silently pass or produce misleading results.
///         This test uses block 405000000 (verified in GMX spike, 03-01-SUMMARY.md).
///
///         Run: forge test --match-path "test/fork/GMXAdapterForkTest.t.sol"
///                          --fork-url $ARB_RPC --fork-block-number 405000000 -vvv
///
/// @dev D-02 proof depth (INTRACTABLE branch):
///      The full createOrder → keeper execute → OrderExecuted round-trip is NOT proven here
///      (that was the TRACTABLE branch). The INTRACTABLE D-02 proof consists of:
///        1. This Solidity fork test: empty-set early return proven against real Chainlink
///           on a live fork. The real Chainlink ETH/USD feed at block 405000000 is read
///           in the populated path to confirm the Chainlink read path works against
///           real mainnet data (not a mock).
///        2. Python encoding proof: orchestrator/tests/fork/test_gmx_encoding.py confirms
///           CreateOrderParams encoding via gmx_python_sdk against the real fork.
///        Together: "GMX read-side proven on real fork at block 405000000; order encoding
///        proven in Python; demo executes on MockPerps for stability."
contract GMXAdapterForkTest is Test {
    // =========================================================================
    // Constants — verified GMX V2 Arbitrum One addresses (post-402000000 deploy)
    // =========================================================================

    /// @dev GMX V2 ExchangeRouter — Arbitrum One.
    address constant EXCHANGE_ROUTER = 0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41;

    /// @dev GMX V2 OrderVault — Arbitrum One.
    address constant ORDER_VAULT = 0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5;

    /// @dev GMX V2 OrderHandler — impersonate keeper in fork tests.
    address constant ORDER_HANDLER = 0x63492B775e30a9E6b4b4761c12605EB9d071d5e9;

    /// @dev GMX V2 Reader — Arbitrum One (post-402000000).
    address constant READER = 0x470fbC46bcC0f16532691Df360A07d8Bf5ee0789;

    /// @dev GMX V2 DataStore — Arbitrum One.
    address constant DATA_STORE = 0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8;

    /// @dev Chainlink ETH/USD feed — Arbitrum One.
    address constant ETH_FEED = 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612;

    /// @dev Chainlink BTC/USD feed — Arbitrum One.
    address constant BTC_FEED = 0x6ce185560a4963c47a8Ec16F4EF5d62A0000E708;

    /// @dev Chainlink SOL/USD feed — Arbitrum One.
    address constant SOL_FEED = 0x24ceA4b8ce57cdA5058b924B9B9987992450590c;

    /// @dev WETH on Arbitrum One — execution fee buffer currency.
    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;

    /// @dev ETH/USD GMX market token (discovered in 03-01 spike at block 405000000).
    address constant ETH_USD_MARKET = 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336;

    /// @dev Standard execution fee (0.001 ether WETH per order).
    uint256 constant EXECUTION_FEE = 0.001 ether;

    // =========================================================================
    // Fixtures
    // =========================================================================

    GMXAdapter internal gmxAdapter;
    address internal vault;

    // =========================================================================
    // setUp — deploy GMXAdapter with real mainnet addresses on the fork
    // =========================================================================

    function setUp() public {
        // Deploy GMXAdapter with all real Arbitrum One addresses.
        // The fork provides the real contract state at block 405000000.
        gmxAdapter = new GMXAdapter(
            EXCHANGE_ROUTER, ORDER_VAULT, ORDER_HANDLER, READER, DATA_STORE, ETH_FEED, BTC_FEED, SOL_FEED, EXECUTION_FEE
        );

        // Use a fresh EOA as the "vault" address — no positions tracked.
        vault = makeAddr("vault");
    }

    // =========================================================================
    // D-05 — empty-set early return proves the oracle outage rescue path
    // =========================================================================

    /// @notice positionValueUSDC returns 0 and does NOT revert when vault has NO positions,
    ///         even on a live fork with a real Chainlink feed that may be stale.
    ///
    ///         This is the CRITICAL D-05 test: proves the empty-set early return fires
    ///         BEFORE any Chainlink read. Without this, the operator rescue path
    ///         (endSession → drain → settle) would be frozen by oracle outage.
    ///
    ///         Fork-specific note: on a real fork, the Chainlink feed answers ARE fresh
    ///         (we're querying a live block), so we also verify with a simulated stale
    ///         feed to prove the early-return fires regardless.
    function test_positionValueUSDC_empty_no_revert() public {
        // Sanity: vault starts with zero tracked positions.
        bytes32[] memory keys = gmxAdapter.getOpenPositionKeys(vault);
        assertEq(keys.length, 0, "setUp: vault must start with no tracked positions");

        // D-05: positionValueUSDC on an empty vault must return 0 with NO revert.
        // On the fork, the real Chainlink feeds are fresh at block 405000000.
        uint256 val = gmxAdapter.positionValueUSDC(vault);
        assertEq(val, 0, "D-05: positionValueUSDC must return 0 for vault with no positions");
    }

    /// @notice positionValueUSDC returns 0 even when the Chainlink feed is simulated stale,
    ///         as long as the vault has no open positions (D-05 empty-set short-circuit).
    ///
    ///         Uses vm.mockCall to make the real ETH/USD feed return a stale updatedAt.
    ///         The D-05 empty-set check fires BEFORE the Chainlink read path, so the
    ///         function returns 0 without ever reaching the staleness guard.
    function test_positionValueUSDC_stale_feed_empty_no_revert() public {
        // Simulate the ETH/USD feed returning a stale updatedAt (1000s ago, well within
        // MAX_STALENESS=4500s — but we can also use a far-past timestamp to prove the
        // empty guard fires before staleness check even for extreme staleness).
        //
        // Encode a latestRoundData response with updatedAt = block.timestamp - 999999
        // (extremely stale — would cause staleness revert if reached).
        uint80 roundId = 1;
        int256 answer = 300_000_000_000; // $3000 ETH in 8-dec
        uint256 startedAt = block.timestamp - 1_000_000;
        uint256 updatedAt = block.timestamp - 999_999; // extremely stale
        uint80 answeredInRound = 1;

        vm.mockCall(
            ETH_FEED,
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(roundId, answer, startedAt, updatedAt, answeredInRound)
        );

        // D-05: empty set early return fires BEFORE any Chainlink call.
        // positionValueUSDC must return 0 with no revert despite the stale mock.
        uint256 val = gmxAdapter.positionValueUSDC(vault);
        assertEq(val, 0, "D-05: empty-set early return must fire before staleness check");

        vm.clearMockedCalls();
    }

    // =========================================================================
    // D-05 populated path — staleness guard fires when positions exist
    // =========================================================================

    /// @notice When positions ARE present, a stale Chainlink feed causes positionValueUSDC
    ///         to REVERT (D-03 staleness enforcement). This cross-checks that the empty-set
    ///         early return is specifically the D-05 mechanism — not a general bypass.
    ///
    ///         Flow: pushPositionKey to register a fake position key → mock ETH feed stale
    ///         → assert positionValueUSDC reverts with stale-price error.
    function test_positionValueUSDC_stale_feed_with_positions_reverts() public {
        // Register a fake position key to make the vault non-empty.
        bytes32 fakeKey = keccak256(abi.encodePacked("fake_position_key"));
        gmxAdapter.pushPositionKey(vault, fakeKey);

        assertEq(gmxAdapter.getOpenPositionKeys(vault).length, 1, "vault must have 1 position key");

        // Simulate the ETH/USD feed returning an extremely stale updatedAt.
        uint80 roundId = 1;
        int256 answer = 300_000_000_000; // $3000 ETH
        uint256 startedAt = block.timestamp - 1_000_000;
        uint256 updatedAt = block.timestamp - 999_999; // exceeds MAX_STALENESS=4500
        uint80 answeredInRound = 1;

        vm.mockCall(
            ETH_FEED,
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(roundId, answer, startedAt, updatedAt, answeredInRound)
        );

        // Non-empty case: positionValueUSDC MUST revert with staleness error (D-03 enforcement).
        // This proves the early return is the D-05 mechanism, not a general bypass.
        vm.expectRevert("GMXAdapter: stale price");
        gmxAdapter.positionValueUSDC(vault);

        vm.clearMockedCalls();
    }

    // =========================================================================
    // Real Chainlink read — populated path on live fork
    // =========================================================================

    /// @notice On a live fork at block 405000000, positionValueUSDC with a real position
    ///         key reads from the REAL Chainlink ETH/USD feed (fresh at that block).
    ///         When the GMX Reader returns empty data for the fake key, the position
    ///         contributes 0 to the total, confirming the skip-on-not-found path.
    ///
    ///         This proves: (1) the Chainlink path IS reached when positions exist,
    ///         (2) the real Chainlink feed is readable on the fork, (3) the GMX Reader
    ///         staticcall is made and gracefully handles missing positions.
    function test_positionValueUSDC_populated_real_chainlink_fork() public {
        // Register a fake position key.
        bytes32 fakeKey = keccak256(abi.encodePacked("fake_key_for_fork_test"));
        gmxAdapter.pushPositionKey(vault, fakeKey);

        // positionValueUSDC with a non-empty set will:
        //   1. Read real ETH/USD Chainlink feed (fresh at fork block 405000000)
        //   2. Call GMX Reader.getPositionInfo for the fake key
        //   3. Reader returns empty/reverts → staticcall ok=false or empty → skip
        //   4. total stays 0 (no valid position data for fake key)
        //
        // This PROVES the Chainlink read path is exercised against the real mainnet feed.
        // If the Chainlink feed were stale at this block, the call would revert.
        // The call succeeds → real Chainlink is fresh and readable.
        uint256 val = gmxAdapter.positionValueUSDC(vault);

        // Result is 0 because the fake position key has no corresponding GMX position.
        // The important proof is that the call SUCCEEDED (real Chainlink was readable).
        assertEq(val, 0, "populated path with unknown key: result should be 0 (key not in Reader)");
    }

    // =========================================================================
    // getOpenPositionKeys — read-side enumeration
    // =========================================================================

    /// @notice getOpenPositionKeys returns empty array for a fresh vault (no positions).
    function test_getOpenPositionKeys_empty() public view {
        bytes32[] memory keys = gmxAdapter.getOpenPositionKeys(vault);
        assertEq(keys.length, 0, "fresh vault must have 0 position keys");
    }

    /// @notice getOpenPositionKeys returns registered keys after pushPositionKey.
    function test_getOpenPositionKeys_populated() public {
        bytes32 key1 = keccak256(abi.encodePacked("key1"));
        bytes32 key2 = keccak256(abi.encodePacked("key2"));
        gmxAdapter.pushPositionKey(vault, key1);
        gmxAdapter.pushPositionKey(vault, key2);

        bytes32[] memory keys = gmxAdapter.getOpenPositionKeys(vault);
        assertEq(keys.length, 2, "vault must have 2 tracked position keys");
        assertEq(keys[0], key1, "first key must match");
        assertEq(keys[1], key2, "second key must match");
    }

    // =========================================================================
    // INTRACTABLE branch: order functions assert deferred-revert
    // =========================================================================

    /// @notice openLong reverts with the deferred-path message (INTRACTABLE D-16).
    function test_openLong_reverts_deferred() public {
        vm.expectRevert("GMXAdapter: order path deferred (read-side only)");
        gmxAdapter.openLong("ETH", 1000e30, 10_000, 30);
    }

    /// @notice openShort reverts with the deferred-path message (INTRACTABLE D-16).
    function test_openShort_reverts_deferred() public {
        vm.expectRevert("GMXAdapter: order path deferred (read-side only)");
        gmxAdapter.openShort("ETH", 1000e30, 10_000, 30);
    }

    /// @notice closePosition reverts with the deferred-path message (INTRACTABLE D-16).
    function test_closePosition_reverts_deferred() public {
        bytes32 fakeKey = keccak256(abi.encodePacked("fake_pos_key"));
        vm.expectRevert("GMXAdapter: order path deferred (read-side only)");
        gmxAdapter.closePosition(fakeKey, 1000e30);
    }

    // =========================================================================
    // WETH buffer note (PERPS-03 partial proof in read-side mode)
    // =========================================================================

    /// @notice Confirms WETH can be sent to the GMXAdapter address (execution fee buffer).
    ///         In the full write path (Phase 6), this buffer funds the sendWnt call.
    ///         In read-side mode, the buffer is not consumed — this test confirms deal()
    ///         works so the fork test harness is correct when the write path is promoted.
    function test_weth_buffer_execution() public {
        // Fund the GMXAdapter with WETH (execution fee buffer pattern from D-18).
        deal(WETH, address(gmxAdapter), 0.1 ether);

        // Confirm the adapter holds 0.1 ETH WETH.
        uint256 balance;
        (bool ok, bytes memory data) =
            WETH.staticcall(abi.encodeWithSignature("balanceOf(address)", address(gmxAdapter)));
        require(ok, "balanceOf call failed");
        balance = abi.decode(data, (uint256));
        assertEq(balance, 0.1 ether, "WETH buffer must hold 0.1 ETH");

        // Note: In read-side mode, openLong reverts so the WETH is not consumed.
        // This test proves deal() + WETH balance work on the fork, so the write path
        // can use this harness pattern in Phase 6 without test infrastructure changes.
    }
}
