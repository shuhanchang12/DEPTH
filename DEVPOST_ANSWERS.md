# DEPTH: Devpost answers

## Elevator pitch
A USDC vault on Arc that market-makes Gemini Prediction Markets, so anyone can earn the maker rebates and liquidity rewards that are today reserved for institutions.

## What did you build?
DEPTH is a USDC liquidity vault on Arc that funds market-making on Gemini Prediction Markets. Users deposit USDC and receive vault shares (ERC-4626). A bot ranks the Gemini events that pay Liquidity Rewards, allocates capital to the best ones and quotes two-sided maker-only orders, earning the spread, maker rebates and daily rewards. The vault charges a 20% performance fee above a high-water mark; the rest goes to depositors.

## What problem does it solve?
Gemini's prediction markets have thin order books, and liquidity is Gemini's stated priority: it pays maker rebates, daily Liquidity Rewards and has dropped maker fees. Yet many events have reward pools with zero qualifying makers, and Gemini's market maker program is open to institutions only. Regular users can't access market-making yield. DEPTH pools capital on-chain so anyone with USDC can, and every Gemini trader gets tighter prices.

## How does it work?
1. Users deposit USDC into DepthVault on Arc Testnet and get dUSDC shares.
2. The operator moves idle USDC to the market-making account (pullForMM). The vault keeps it in totalAssets as deployed capital, so the share price stays correct.
3. The bot reads Gemini's Liquidity Rewards events and scores each one as daily pool / (qualifying makers + 1), skips events about to expire, and splits capital across the top 5.
4. In each event it quotes the contract closest to 50/50 with two maker-or-cancel orders (buy YES below mid, buy NO at 1 - ask), within the reward program's spread and size rules read from Gemini's API. Orders go to the Gemini sandbox.
5. The operator marks the market-making account to market on-chain (reportNAV) and returns USDC when needed (returnFromMM).
6. 20% of share-price gains above the high-water mark is minted as shares to the fee recipient. No fee on deposits, none after losses until the vault recovers. Users withdraw idle USDC at any time.
The vault logic is covered by 7 Foundry tests (fees, NAV, withdrawals, access control).

## What makes it unique?
Most prediction market projects build bots that bet. DEPTH sits on the other side of the book and supplies liquidity. It applies Hyperliquid's HLP vault model to a regulated prediction market, with transparent accounting on Arc (USDC as gas) and a per-share high-water-mark fee. The allocation rule targets reward pools that nobody is collecting.

## What would you build next?
Automatic NAV reporting from Gemini positions and payouts, dynamic spreads, on-chain risk limits and a pause switch, QuickNode Streams to resize quotes when deposits land, and a compliant structure (CFTC commodity pool rules). Longer term, deeper books make prediction markets usable for real hedging, e.g. a small manufacturer protecting itself against a copper price spike.

## Contract
DepthVault on Arc Testnet: 0xD551749570C93e5bb388Bedb7Cdf6E79A0a18732
https://testnet.arcscan.app/address/0xD551749570C93e5bb388Bedb7Cdf6E79A0a18732
USDC (Arc Testnet): 0x3600000000000000000000000000000000000000

## Built with
Solidity, OpenZeppelin (ERC-4626), Foundry, Arc Testnet, USDC, Python, web3.py, Gemini Prediction Markets API, ethers.js, HTML/JS
