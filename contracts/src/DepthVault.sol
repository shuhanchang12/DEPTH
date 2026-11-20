// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title DepthVault
/// @notice USDC liquidity vault for market-making on Gemini Prediction Markets.
/// @dev 20% performance fee on net profit above high-water mark. Operator can pull capital for MM.
contract DepthVault is ERC4626, Ownable {
    uint256 public constant PERFORMANCE_FEE_BPS = 2000; // 20%
    uint256 public highWaterMark;
    address public feeRecipient;
    address public operator;

    event FeeCollected(uint256 amount);
    event CapitalPulled(address indexed to, uint256 amount);
    event CapitalReturned(address indexed from, uint256 amount);

    constructor(IERC20 usdc, address _feeRecipient, address _operator)
        ERC4626(usdc)
        ERC20("DEPTH Vault Share", "dUSDC")
        Ownable(msg.sender)
    {
        feeRecipient = _feeRecipient;
        operator = _operator;
        highWaterMark = 1e6; // 1 USDC (6 decimals)
    }

    function totalAssets() public view override returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }

    function _collectFee() internal {
        uint256 current = totalAssets();
        if (current > highWaterMark) {
            uint256 profit = current - highWaterMark;
            uint256 fee = (profit * PERFORMANCE_FEE_BPS) / 10000;
            if (fee > 0) {
                IERC20(asset()).transfer(feeRecipient, fee);
                emit FeeCollected(fee);
            }
            highWaterMark = totalAssets();
        }
    }

    function deposit(uint256 assets, address receiver) public override returns (uint256) {
        _collectFee();
        return super.deposit(assets, receiver);
    }

    function withdraw(uint256 assets, address receiver, address owner) public override returns (uint256) {
        _collectFee();
        return super.withdraw(assets, receiver, owner);
    }

    /// @notice Operator pulls USDC for market-making account
    function pullForMM(uint256 amount) external {
        require(msg.sender == operator || msg.sender == owner(), "Not operator");
        IERC20(asset()).transfer(operator, amount);
        emit CapitalPulled(operator, amount);
    }

    /// @notice Return capital from market-making account (must approve first)
    function returnFromMM(uint256 amount) external {
        IERC20(asset()).transferFrom(msg.sender, address(this), amount);
        _collectFee();
        emit CapitalReturned(msg.sender, amount);
    }

    function setOperator(address _operator) external onlyOwner {
        operator = _operator;
    }

    function setFeeRecipient(address _feeRecipient) external onlyOwner {
        feeRecipient = _feeRecipient;
    }
}
