# DEPTH – Gemini Liquidity Vault on Arc

**GitHub:** https://github.com/shuhanchang12/DEPTH

A USDC vault on Arc that market-makes Gemini Prediction Markets and shares maker rebates + Liquidity Rewards with depositors.

## Problem

Gemini Prediction Markets pay daily Liquidity Rewards and maker rebates for two-sided quoting. Many markets currently have zero or very few makers collecting these rewards. Institutional market-maker programs are restricted.

## Solution

DEPTH pools USDC on Arc and runs a simple rules-compliant market-making bot. Depositors earn the net yield after a 20% performance fee.

## Architecture

- **Vault (Arc)**: ERC-4626 style USDC vault. Tracks shares, collects 20% performance fee, and exposes capital for market-making.
- **Market-making Bot**: Off-chain service that reads Gemini Liquidity Rewards pools, places and refreshes two-sided maker orders, and sweeps rewards back to the vault.
- **Frontend**: Simple deposit / withdraw interface plus live TVL and active markets.

## Economic Design & Risk Assumptions

- Deposit asset: USDC
- Performance fee: 20% of net profit (high-water mark)
- No management fee
- Risk: inventory risk on prediction contracts. Strategy is intentionally simple (fixed spread, size caps, two-sided only) to remain eligible for Gemini Liquidity Rewards.
- Withdrawals: short configurable lockup to protect quoting.

## Quick Start

### 1. Deploy Vault on Arc Testnet

```bash
cd contracts
forge create src/DepthVault.sol:DepthVault \
  --rpc-url https://rpc.testnet.arc.network \
  --private-key $PRIVATE_KEY \
  --constructor-args 0x3600000000000000000000000000000000000000 YOUR_FEE_RECIPIENT \
  --broadcast
```

USDC (ERC-20 interface) on Arc Testnet: `0x3600000000000000000000000000000000000000` (6 decimals)

Get test USDC: https://faucet.circle.com (select Arc Testnet)

### 2. Run Market-Making Bot

```bash
cd bot
cp .env.example .env
# Fill GEMINI_API_KEY + GEMINI_API_SECRET for live sandbox orders.
# Leave DRY_RUN=true to log two-sided maker quotes without private API calls.
pip install -r requirements.txt
python mm_bot.py
```

Authenticated quoting uses Gemini HMAC REST (`POST /v1/prediction-markets/order` with `makerOrCancel=true`). Defaults: fixed spread, size cap, two-sided only.

### 3. Frontend

Single-page wallet UI (`frontend/index.html`, ethers via CDN).

```bash
# from repo / store root
python3 -m http.server 8080 --directory frontend
# open http://127.0.0.1:8080/?vault=0xYOUR_DEPLOYED_VAULT
```

1. Deploy the vault and copy its address.
2. Open the page with `?vault=0x…`, paste into **VAULT_ADDRESS**, or set the `CONFIG.VAULT_ADDRESS` constant in the HTML.
3. Connect MetaMask (Arc Testnet is added/switched automatically: chain `5042002`, RPC `https://rpc.testnet.arc.network`).
4. Deposit: approves USDC then `deposit`. Withdraw: `withdraw(assets, you, you)`.
5. Stats (TVL, shares, share price) read on-chain; refresh every 15s.

Test USDC: https://faucet.circle.com (Arc Testnet).

## Contracts

- Vault: deploy and record address
- USDC: `0x3600000000000000000000000000000000000000`

## Tracks & Bounties

- Main track: DeFi and Onchain Applications
- Chain award: Best use of Arc
- Bounty: Gemini Prediction Markets (do not opt into QuickNode Streams unless a Stream is implemented)

## What’s Next

Multi-market allocation, dynamic spreads, on-chain risk parameters, automated circuit breakers, and deeper real-world hedging integrations.
