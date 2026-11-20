// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

/// @title DepthVault
/// @notice USDC liquidity vault on Arc that funds market-making on Gemini Prediction Markets.
/// @dev
///  - totalAssets = idle USDC in the vault + capital deployed to the market-making account
///    (marked to market by the operator via reportNAV).
///  - 20% performance fee on share-price gains above a per-share high-water mark,
///    paid by minting vault shares to the fee recipient (no forced selling of positions).
///  - Withdrawals are limited to idle USDC; the operator returns capital when needed.
contract DepthVault is ERC4626, Ownable {
    using SafeERC20 for IERC20;

    uint256 public constant PERFORMANCE_FEE_BPS = 2000; // 20%
    uint256 private constant PRICE_SCALE = 1e18;

    /// @notice USDC currently deployed to the Gemini market-making account (marked to market)
    uint256 public deployedAssets;
    /// @notice High-water mark on assets per share (scaled by 1e18)
    uint256 public highWaterMark;
    address public feeRecipient;
    address public operator;
    uint256 public lastReportAt;

    event CapitalPulled(address indexed to, uint256 amount, uint256 deployedAssets);
    event CapitalReturned(address indexed from, uint256 amount, uint256 deployedAssets);
    event NavReported(uint256 previousDeployed, uint256 newDeployed, int256 pnl);
    event FeeCollected(uint256 feeAssets, uint256 feeShares, uint256 newHighWaterMark);
    event OperatorUpdated(address operator);
    event FeeRecipientUpdated(address feeRecipient);

    modifier onlyOperator() {
        require(msg.sender == operator || msg.sender == owner(), "DEPTH: not operator");
        _;
    }

    constructor(IERC20 usdc, address _feeRecipient, address _operator)
        ERC4626(usdc)
        ERC20("DEPTH Vault Share", "dUSDC")
        Ownable(msg.sender)
    {
        require(_feeRecipient != address(0) && _operator != address(0), "DEPTH: zero address");
        feeRecipient = _feeRecipient;
        operator = _operator;
        highWaterMark = PRICE_SCALE; // 1 USDC per share unit at start
    }

    // ---------------------------------------------------------------------
    // Accounting
    // ---------------------------------------------------------------------

    function idleAssets() public view returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }

    function totalAssets() public view override returns (uint256) {
        return idleAssets() + deployedAssets;
    }

    /// @notice Assets per share, scaled by 1e18
    function sharePrice() public view returns (uint256) {
        return _pricePerShare(totalAssets(), totalSupply());
    }

    /// @dev Withdrawals can only be served from idle USDC.
    function maxWithdraw(address owner_) public view override returns (uint256) {
        return Math.min(super.maxWithdraw(owner_), idleAssets());
    }

    function maxRedeem(address owner_) public view override returns (uint256) {
        return Math.min(super.maxRedeem(owner_), convertToShares(idleAssets()));
    }

    // ---------------------------------------------------------------------
    // Performance fee (per-share high-water mark)
    // ---------------------------------------------------------------------

    function _pricePerShare(uint256 assets_, uint256 supply_) internal pure returns (uint256) {
        if (supply_ == 0) return PRICE_SCALE;
        return assets_ * PRICE_SCALE / supply_;
    }

    function _collectFee() internal {
        uint256 supply = totalSupply();
        if (supply == 0) return;
        uint256 assets_ = totalAssets();
        uint256 price = _pricePerShare(assets_, supply);
        if (price <= highWaterMark) return;

        uint256 profit = (price - highWaterMark) * supply / PRICE_SCALE;
        uint256 feeAssets = profit * PERFORMANCE_FEE_BPS / 10_000;
        if (feeAssets == 0 || feeAssets >= assets_) return;

        // Mint shares worth `feeAssets` to the fee recipient without moving USDC.
        uint256 feeShares = feeAssets * supply / (assets_ - feeAssets);
        _mint(feeRecipient, feeShares);
        highWaterMark = _pricePerShare(assets_, supply + feeShares);
        emit FeeCollected(feeAssets, feeShares, highWaterMark);
    }

    function collectFee() external {
        _collectFee();
    }

    function deposit(uint256 assets, address receiver) public override returns (uint256) {
        _collectFee();
        return super.deposit(assets, receiver);
    }

    function mint(uint256 shares, address receiver) public override returns (uint256) {
        _collectFee();
        return super.mint(shares, receiver);
    }

    function withdraw(uint256 assets, address receiver, address owner_) public override returns (uint256) {
        _collectFee();
        return super.withdraw(assets, receiver, owner_);
    }

    function redeem(uint256 shares, address receiver, address owner_) public override returns (uint256) {
        _collectFee();
        return super.redeem(shares, receiver, owner_);
    }

    // ---------------------------------------------------------------------
    // Operator: capital movements and NAV reporting
    // ---------------------------------------------------------------------

    /// @notice Move idle USDC to the market-making account.
    function pullForMM(uint256 amount) external onlyOperator {
        require(amount <= idleAssets(), "DEPTH: insufficient idle");
        deployedAssets += amount;
        IERC20(asset()).safeTransfer(operator, amount);
        emit CapitalPulled(operator, amount, deployedAssets);
    }

    /// @notice Return USDC (principal and/or profit) from the market-making account. Approve first.
    function returnFromMM(uint256 amount) external onlyOperator {
        IERC20(asset()).safeTransferFrom(msg.sender, address(this), amount);
        deployedAssets = amount >= deployedAssets ? 0 : deployedAssets - amount;
        emit CapitalReturned(msg.sender, amount, deployedAssets);
        _collectFee();
    }

    /// @notice Mark the deployed capital to market (positions + cash + rebates + liquidity rewards).
    function reportNAV(uint256 newDeployedAssets) external onlyOperator {
        uint256 prev = deployedAssets;
        deployedAssets = newDeployedAssets;
        lastReportAt = block.timestamp;
        emit NavReported(prev, newDeployedAssets, int256(newDeployedAssets) - int256(prev));
        _collectFee();
    }

    // ---------------------------------------------------------------------
    // Admin
    // ---------------------------------------------------------------------

    function setOperator(address _operator) external onlyOwner {
        require(_operator != address(0), "DEPTH: zero address");
        operator = _operator;
        emit OperatorUpdated(_operator);
    }

    function setFeeRecipient(address _feeRecipient) external onlyOwner {
        require(_feeRecipient != address(0), "DEPTH: zero address");
        _collectFee();
        feeRecipient = _feeRecipient;
        emit FeeRecipientUpdated(_feeRecipient);
    }
}
