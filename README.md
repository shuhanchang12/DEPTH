# DEPTH

A USDC vault on Arc that provides liquidity to Gemini Prediction Markets.

You deposit USDC into the vault. A bot uses that capital to quote both sides of Gemini prediction markets, and earns the spread plus the rewards Gemini pays to market makers. Depositors keep 80% of the profit; 20% goes to the protocol as a performance fee.

Built for the University Blockchain Conference 2026 hackathon (DeFi & Onchain Apps track, Arc, Gemini Prediction Markets bounty).

## Why

Gemini launched prediction markets in December 2025 and its order books are still thin. To fix that, Gemini pays makers in several ways: maker rebates on filled orders, daily Liquidity Rewards for resting orders (even if they never fill), and zero maker fees. When we looked at the public rewards endpoint, a lot of events had a reward pool and no qualifying maker at all.

Gemini's market maker program is open to institutions only. A regular user with a few hundred dollars has no way to earn that yield. DEPTH pools capital on-chain so they can, and every trader on Gemini gets tighter prices as a side effect.

## How it works

```
 depositors ──USDC──▶  DepthVault (Arc)  ──pullForMM──▶  operator / Gemini account
     ▲                     │    ▲                              │
     │                     │    └────── reportNAV / returnFromMM ┘
     └──── dUSDC shares ───┘                                    │
                                                    mm_bot.py quotes on Gemini
```

1. Users deposit USDC into `DepthVault` and receive `dUSDC` shares (ERC-4626).
2. The operator moves part of the idle USDC to the market-making account with `pullForMM`. The vault keeps counting it in `totalAssets` as `deployedAssets`, so the share price doesn't move when capital leaves.
3. The bot reads which Gemini events pay Liquidity Rewards, ranks them and quotes the best ones (details below).
4. The operator marks the market-making account to market on-chain with `reportNAV` (positions, cash, rebates and rewards), and sends USDC back with `returnFromMM` when needed.
5. When the share price goes above its previous high, 20% of the gain is minted as shares to the fee recipient. There is no fee on deposits, and no fee while the vault is below its high-water mark.
6. Users can withdraw whatever USDC is idle in the vault at any time.

### Choosing markets

The bot ranks every event in Gemini's Liquidity Rewards program with:

```
score = daily reward pool / (qualifying makers + 1)
```

A $100/day pool with nobody quoting is worth more to us than the same pool shared with nine other makers. Events ending in less than 30 minutes are skipped. Capital is split across the top 5 events in proportion to their score.

In each event the bot picks the contract with a mid price closest to 0.50 and places two maker-or-cancel orders: buy YES below mid and buy NO at `1 - ask` (the same exposure as offering YES at `ask`, without needing inventory). Spread and size follow the reward program's rules, which the bot reads from `/v1/prediction-markets/liquidity-rewards/config` (max 10 cents, min 100 contracts at the time of writing).

## Repo

```
contracts/
  src/DepthVault.sol        vault (ERC-4626, NAV tracking, 20% fee above HWM)
  test/DepthVault.t.sol     Foundry tests
  script/Deploy.s.sol       deployment to Arc Testnet
bot/
  mm_bot.py                 ranking, allocation, quoting, NAV reporting
frontend/
  index.html                deposit / withdraw and vault stats
```

## Run it

### Contracts

```bash
cd contracts
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts@v5.1.0 --no-git
forge test
```

Deploy to Arc Testnet (get test USDC at https://faucet.circle.com):

```bash
export FEE_RECIPIENT=0x...   # defaults to the deployer
export OPERATOR=0x...        # defaults to the deployer
forge script script/Deploy.s.sol --rpc-url arc_testnet --private-key $PRIVATE_KEY --broadcast
```

Or directly. The constructor takes three arguments: USDC, fee recipient, operator.

```bash
forge create src/DepthVault.sol:DepthVault \
  --rpc-url https://rpc.testnet.arc.network \
  --private-key $PRIVATE_KEY --broadcast \
  --constructor-args 0x3600000000000000000000000000000000000000 $FEE_RECIPIENT $OPERATOR
```

USDC on Arc Testnet: `0x3600000000000000000000000000000000000000` (6 decimals).

### Bot

```bash
cd bot
cp .env.example .env
pip install -r requirements.txt

python mm_bot.py rank             # live ranking of Gemini reward events
python mm_bot.py                  # quoting loop (DRY_RUN=true prints orders only)
python mm_bot.py report-nav 1150  # mark the MM account at 1150 USDC on the vault
```

Market data comes from Gemini production (public endpoints). Orders go to the Gemini sandbox, which needs its own account and API key. Set `DRY_RUN=false` and fill `GEMINI_API_KEY` / `GEMINI_API_SECRET` to send real sandbox orders.

### Frontend

```bash
python3 -m http.server 8080 --directory frontend
# open http://127.0.0.1:8080/?vault=0xYOUR_VAULT
```

Connect MetaMask (Arc Testnet, chain id 5042002, added automatically), then deposit or withdraw. The page shows TVL, share price, idle USDC and capital deployed to market making.

## Deployment

| | Address |
|---|---|
| DepthVault (Arc Testnet) | [`0xD551749570C93e5bb388Bedb7Cdf6E79A0a18732`](https://testnet.arcscan.app/address/0xD551749570C93e5bb388Bedb7Cdf6E79A0a18732) |
| USDC (Arc Testnet) | `0x3600000000000000000000000000000000000000` |

Test transactions on Arc Testnet:

- Deploy: [0x65e6d8b6…](https://testnet.arcscan.app/tx/0x65e6d8b63690e04cc1db2f29b0777c8b885e691970f0044369e67074b55aabf2)
- Approve USDC: [0x54e49998…](https://testnet.arcscan.app/tx/0x54e499984fdd0f4c328513691a2bc75ee381d42907a5c1fd8e02687626e7558f)
- Deposit 10 USDC: [0x64698b4a…](https://testnet.arcscan.app/tx/0x64698b4aba20d70c653663ca32ee8342c9e3786830d628b16a0454211e688af0)
- pullForMM 4 USDC: [0x145a4545…](https://testnet.arcscan.app/tx/0x145a45458b06ec5e4a264c08c3d9e6f6cf1b2f196b27235f8f4243b55bdbe43e)

After these, the vault holds 10 USDC of assets: 6 idle and 4 deployed to the market-making account.

## How DEPTH uses Arc

All of DEPTH's onchain accounting lives on Arc: deposits, vault shares, capital sent to market making, NAV reports and the performance fee. Arc uses USDC as gas, so depositors hold and pay in a single asset and fees stay predictable in dollars. The vault talks to Arc's USDC through its ERC-20 interface (`0x3600…0000`, 6 decimals) and was built and tested with Foundry.

## Risks and limits

- Inventory and adverse selection: if the market moves against our quotes, or better-informed traders pick us off, the vault loses money. We limit size per market and stay two-sided.
- Incentives: part of the yield comes from Gemini's reward programs, which Gemini can change or end.
- Custody: capital deployed to market making sits in a Gemini account controlled by the operator, and its value reaches the chain through `reportNAV`. Depositors trust the operator for that part.
- Regulation: pooling money to trade CFTC-regulated event contracts is a commodity pool in the US. A real launch would need a registered operator or partner.
- The MVP quotes on the Gemini sandbox. NAV is reported by the operator, not proven on-chain.

## Next

- Read positions and reward payouts from Gemini and report NAV automatically.
- Dynamic spreads based on volatility and inventory.
- On-chain risk limits (max deployed share, per-market caps) and a pause switch.
- QuickNode Streams on Arc to resize quotes as soon as deposits or withdrawals land.
- Longer term, deeper books make prediction markets usable for real hedging, for example a small manufacturer protecting itself against a copper price spike.

## Team

Ryan, Shuhan (Shelly) Chang and Mark-Killian Zinenberg, Kryptosphere.
