// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {DepthVault} from "../src/DepthVault.sol";

contract MockUSDC is ERC20 {
    constructor() ERC20("USD Coin", "USDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract DepthVaultTest is Test {
    MockUSDC usdc;
    DepthVault vault;

    address operator = makeAddr("operator");
    address feeRecipient = makeAddr("feeRecipient");
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    function setUp() public {
        usdc = new MockUSDC();
        vault = new DepthVault(usdc, feeRecipient, operator);
        address[3] memory users = [alice, bob, operator];
        for (uint256 i; i < users.length; i++) {
            usdc.mint(users[i], 10_000e6);
            vm.prank(users[i]);
            usdc.approve(address(vault), type(uint256).max);
        }
    }

    function _deposit(address who, uint256 amount) internal {
        vm.prank(who);
        vault.deposit(amount, who);
    }

    function _assetsOf(address who) internal view returns (uint256) {
        return vault.convertToAssets(vault.balanceOf(who));
    }

    function test_NoFeeOnDeposits() public {
        _deposit(alice, 1_000e6);
        _deposit(bob, 500e6);
        _deposit(alice, 100e6);
        assertEq(vault.balanceOf(feeRecipient), 0);
        assertEq(_assetsOf(alice), 1_100e6);
    }

    function test_SharePriceUnchangedWhenCapitalDeployed() public {
        _deposit(alice, 1_000e6);
        vm.prank(operator);
        vault.pullForMM(800e6);
        assertEq(vault.totalAssets(), 1_000e6);
        assertEq(vault.idleAssets(), 200e6);
        assertEq(vault.deployedAssets(), 800e6);
        assertEq(_assetsOf(alice), 1_000e6);
    }

    function test_PerformanceFeeOnProfit() public {
        _deposit(alice, 1_000e6);
        _deposit(bob, 500e6);
        vm.startPrank(operator);
        vault.pullForMM(1_000e6);
        vault.reportNAV(1_150e6); // +150 USDC of PnL
        vm.stopPrank();

        // 20% of 150 = 30 USDC goes to the fee recipient (as shares)
        assertApproxEqAbs(_assetsOf(feeRecipient), 30e6, 2);
        assertApproxEqAbs(_assetsOf(alice), 1_080e6, 2);
        assertApproxEqAbs(_assetsOf(bob), 540e6, 2);
    }

    function test_NoFeeAfterLossUntilRecovered() public {
        _deposit(alice, 1_000e6);
        vm.startPrank(operator);
        vault.pullForMM(1_000e6);
        vault.reportNAV(900e6); // -100
        vault.reportNAV(1_000e6); // back to break-even
        vm.stopPrank();
        assertEq(vault.balanceOf(feeRecipient), 0);
    }

    function test_WithdrawLimitedToIdle() public {
        _deposit(alice, 1_000e6);
        vm.prank(operator);
        vault.pullForMM(700e6);
        assertEq(vault.maxWithdraw(alice), 300e6);

        vm.prank(alice);
        vault.withdraw(300e6, alice, alice);
        assertEq(usdc.balanceOf(alice), 9_300e6);
    }

    function test_ReturnCapital() public {
        _deposit(alice, 1_000e6);
        vm.startPrank(operator);
        vault.pullForMM(1_000e6);
        vault.returnFromMM(1_000e6);
        vm.stopPrank();
        assertEq(vault.deployedAssets(), 0);
        assertEq(vault.idleAssets(), 1_000e6);
    }

    function test_OnlyOperatorMovesCapital() public {
        _deposit(alice, 1_000e6);
        vm.prank(alice);
        vm.expectRevert("DEPTH: not operator");
        vault.pullForMM(1e6);
    }
}
