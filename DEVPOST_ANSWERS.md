# DEPTH Devpost Answers

## What did you build?
DEPTH is a USDC liquidity vault on Arc that market-makes Gemini Prediction Markets. Users deposit USDC; an off-chain bot quotes two-sided liquidity on selected markets to earn maker rebates and daily Liquidity Rewards. The vault takes a 20% performance fee; the rest accrues to depositors.

## What problem does it solve?
Gemini Prediction Markets have thin order books. Many markets have zero makers collecting the daily Liquidity Rewards pools. Institutional market-maker programs are restricted. DEPTH opens this yield opportunity to anyone by pooling capital on-chain and running a compliant quoting strategy.

## How does it work?
1. Users deposit USDC into the DEPTH vault on Arc (ERC-4626 style).
2. The vault allocates capital to a dedicated market-making account.
3. An off-chain bot monitors markets with active Liquidity Rewards, places two-sided maker orders within max-spread and min-size rules, and refreshes them.
4. Earned rebates and rewards are swept back to the vault.
5. 20% of net profit is taken as a performance fee; the remainder increases the share price.

Economic design and risk assumptions: Capital is exposed to inventory risk when markets move. The strategy is intentionally simple (fixed spread, size caps, two-sided only) to stay eligible for Gemini Liquidity Rewards. No leverage. Withdrawals use a short lockup to protect the quoting strategy.

## What makes it unique?
First public vault that systematically harvests Gemini’s Liquidity Rewards and maker rebates. Modeled after Hyperliquid’s HLP but purpose-built for prediction markets. Fully on-chain capital accounting on Arc (USDC-native gas) with a transparent performance fee.

## What would you build next?
Multi-strategy allocation, dynamic spreads, on-chain risk parameters and circuit breakers, and integration with real-world hedging use cases once Gemini books are deeper.
