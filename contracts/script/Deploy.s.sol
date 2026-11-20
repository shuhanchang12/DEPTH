// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {DepthVault} from "../src/DepthVault.sol";

/// forge script script/Deploy.s.sol --rpc-url arc_testnet --private-key $PRIVATE_KEY --broadcast
contract Deploy is Script {
    // USDC on Arc Testnet (ERC-20 interface, 6 decimals)
    address constant USDC = 0x3600000000000000000000000000000000000000;

    function run() external {
        address feeRecipient = vm.envOr("FEE_RECIPIENT", msg.sender);
        address operator = vm.envOr("OPERATOR", msg.sender);
        vm.startBroadcast();
        DepthVault vault = new DepthVault(IERC20(USDC), feeRecipient, operator);
        vm.stopBroadcast();
        console.log("DepthVault:", address(vault));
    }
}
