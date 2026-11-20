"""
DEPTH Market-Making Bot for Gemini Prediction Markets

Simple two-sided maker quoting (fixed spread, size caps) via authenticated
Gemini REST. Public discovery uses List Events; private place/cancel uses
HMAC-SHA384 per Gemini private API invocation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

GEMINI_BASE = os.getenv("GEMINI_BASE_URL", "https://api.sandbox.gemini.com").rstrip("/")
API_KEY = os.getenv("GEMINI_API_KEY")
API_SECRET = os.getenv("GEMINI_API_SECRET")
VAULT = os.getenv("VAULT_ADDRESS")
OPERATOR_KEY = os.getenv("OPERATOR_PRIVATE_KEY")
RPC = os.getenv("ARC_RPC")

QUOTE_SIZE = float(os.getenv("QUOTE_SIZE", "50"))
MAX_QUOTE_SIZE = float(os.getenv("MAX_QUOTE_SIZE", "100"))
QUOTE_SPREAD = float(os.getenv("QUOTE_SPREAD", "0.04"))
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "60"))
QUOTE_OUTCOME = os.getenv("QUOTE_OUTCOME", "yes").lower()
# incremental (ms, strictly increasing) | time-based (unix seconds)
NONCE_MODE = os.getenv("GEMINI_NONCE_MODE", "incremental").lower()
# When true, log intended orders but never call private endpoints
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
# yes_no: bid = buy YES, ask = buy NO at (1 - ask). Two-sided without holding inventory.
# sell:   bid = buy YES, ask = sell YES (needs an existing YES position).
QUOTE_STYLE = os.getenv("QUOTE_STYLE", "yes_no").lower()

# Public market data (reward pools, live books) is read from production by default;
# orders go to GEMINI_BASE_URL (sandbox by default).
GEMINI_PUBLIC = os.getenv("GEMINI_PUBLIC_URL", "https://api.gemini.com").rstrip("/")
CAPITAL_USDC = float(os.getenv("CAPITAL_USDC", "1000"))
MAX_MARKETS = int(os.getenv("MAX_MARKETS", "5"))
MIN_MINUTES_LEFT = int(os.getenv("MIN_MINUTES_LEFT", "30"))

_nonce_lock = threading.Lock()
_last_nonce = 0

# symbol -> open order ids from the last successful quote cycle
_open_orders: dict[str, list[int]] = {}


def _operator_address() -> str | None:
    """Optional Arc operator identity; bot quoting does not require it."""
    if not OPERATOR_KEY or not RPC:
        return None
    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(RPC))
        return w3.eth.account.from_key(OPERATOR_KEY).address
    except Exception as exc:  # noqa: BLE001 — soft-fail for MVP without chain keys
        print(f"Warning: could not derive operator address: {exc}")
        return None


def _next_nonce() -> int:
    """Gemini nonce: strictly increasing (ms) or time-based (unix seconds)."""
    global _last_nonce
    with _nonce_lock:
        if NONCE_MODE == "time-based":
            nonce = int(time.time())
            if nonce <= _last_nonce:
                nonce = _last_nonce + 1
        else:
            nonce = int(time.time() * 1000)
            if nonce <= _last_nonce:
                nonce = _last_nonce + 1
        _last_nonce = nonce
        return nonce


def _require_credentials() -> None:
    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "GEMINI_API_KEY and GEMINI_API_SECRET must be set for authenticated orders"
        )


def gemini_private_request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Authenticated Gemini private REST call.

    Payload fields are merged with `request` + `nonce`, Base64-encoded into
    X-GEMINI-PAYLOAD, HMAC-SHA384 signed into X-GEMINI-SIGNATURE. HTTP body
    is empty (Content-Length: 0) per Gemini private API invocation.
    """
    _require_credentials()
    body: dict[str, Any] = {"request": path, "nonce": _next_nonce()}
    if payload:
        body.update(payload)

    encoded = base64.b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    secret = API_SECRET.encode("utf-8") if isinstance(API_SECRET, str) else API_SECRET
    signature = hmac.new(secret, encoded, hashlib.sha384).hexdigest()

    headers = {
        "Content-Type": "text/plain",
        "Content-Length": "0",
        "Cache-Control": "no-cache",
        "X-GEMINI-APIKEY": API_KEY,
        "X-GEMINI-PAYLOAD": encoded.decode("utf-8"),
        "X-GEMINI-SIGNATURE": signature,
    }
    url = f"{GEMINI_BASE}{path}"
    resp = requests.post(url, headers=headers, data=b"", timeout=30)
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(f"Non-JSON Gemini response ({resp.status_code}): {resp.text[:200]}")

    if resp.status_code >= 400:
        msg = data.get("message") or data.get("reason") or data
        raise RuntimeError(f"Gemini {path} failed ({resp.status_code}): {msg}")
    return data


def _clamp_size(size: float) -> float:
    return min(max(size, 0.0), MAX_QUOTE_SIZE)


def _format_price(price: float) -> str:
    """Prediction market prices are in [0, 1]; keep two decimal places for MVP."""
    p = max(0.01, min(0.99, round(price, 2)))
    return f"{p:.2f}"


def _format_qty(size: float) -> str:
    # Prefer whole contracts for MVP; fall back to 2dp if fractional size is set
    if float(size).is_integer():
        return str(int(size))
    return f"{size:.2f}"


def place_maker_order(
    symbol: str,
    side: str,
    price: float,
    size: float,
    outcome: str = QUOTE_OUTCOME,
) -> dict[str, Any]:
    """
    Place a single maker-or-cancel limit order on a prediction contract.

    REST: POST /v1/prediction-markets/order with makerOrCancel=true
    (WS equivalent is timeInForce=MOC).
    """
    payload = {
        "symbol": symbol,
        "orderType": "limit",
        "side": side.lower(),
        "quantity": _format_qty(size),
        "price": _format_price(price),
        "outcome": outcome.lower(),
        "timeInForce": "good-til-cancel",
        "makerOrCancel": True,
    }
    if DRY_RUN:
        print(f"[DRY_RUN] place {payload}")
        return {"orderId": None, "status": "dry_run", **payload}
    return gemini_private_request("/v1/prediction-markets/order", payload)


def cancel_orders(order_ids: list[int]) -> dict[str, Any] | None:
    """Cancel one or more open orders (batch when >1)."""
    ids = [int(oid) for oid in order_ids if oid is not None]
    if not ids:
        return None
    if DRY_RUN:
        print(f"[DRY_RUN] cancel {ids}")
        return {"results": [{"orderId": i, "result": "ok"} for i in ids]}
    if len(ids) == 1:
        return gemini_private_request(
            "/v1/prediction-markets/order/cancel",
            {"orderId": ids[0]},
        )
    return gemini_private_request(
        "/v1/prediction-markets/order/batch/cancel",
        {"orderIds": ids},
    )


def quote_market(
    symbol: str,
    mid_price: float = 0.5,
    size: float | None = None,
    spread: float | None = None,
) -> bool:
    """
    Place two-sided maker orders around mid with a fixed spread and size cap.

    Bid = buy outcome @ mid - spread/2
    Ask = sell outcome @ mid + spread/2
    Both use makerOrCancel so they never take liquidity (Liquidity Rewards style).
    Prior open quotes for this symbol are cancelled before replacing.
    """
    if not symbol:
        print("Skipping quote: empty symbol")
        return False

    size = _clamp_size(QUOTE_SIZE if size is None else size)
    spread = QUOTE_SPREAD if spread is None else spread
    if size <= 0:
        print(f"Skipping {symbol}: size capped to 0 (check QUOTE_SIZE / MAX_QUOTE_SIZE)")
        return False

    bid = mid_price - spread / 2
    ask = mid_price + spread / 2
    if bid <= 0 or ask >= 1 or bid >= ask:
        print(
            f"Skipping {symbol}: invalid book bid={bid} ask={ask} "
            f"mid={mid_price} spread={spread}"
        )
        return False

    print(
        f"[QUOTE] {symbol}  BID {_format_price(bid)} x {_format_qty(size)}  |  "
        f"ASK {_format_price(ask)} x {_format_qty(size)}  style={QUOTE_STYLE}"
    )

    if not DRY_RUN and (not API_KEY or not API_SECRET):
        print(
            "Skipping live placement: GEMINI_API_KEY / GEMINI_API_SECRET not set "
            "(set DRY_RUN=true to log-only)"
        )
        return False

    # Cancel previous cycle's resting orders for this symbol before replacing
    prev = _open_orders.pop(symbol, [])
    if prev:
        try:
            cancel_orders(prev)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: cancel prior orders {prev} failed: {exc}")

    new_ids: list[int] = []

    def _track(resp: dict[str, Any], label: str) -> None:
        oid = resp.get("orderId")
        status = resp.get("status")
        cancelled = resp.get("is_cancelled") or status == "cancelled"
        if oid is not None and not cancelled:
            new_ids.append(int(oid))
            print(f"  {label} orderId={oid} status={status}")
        else:
            print(f"  {label} not resting (maker-or-cancel / dry-run): {resp}")

    try:
        bid_resp = place_maker_order(symbol, "buy", bid, size, outcome="yes")
        _track(bid_resp, "bid")
        if QUOTE_STYLE == "sell":
            ask_resp = place_maker_order(symbol, "sell", ask, size, outcome="yes")
        else:
            # Buying NO at (1 - ask) is the same exposure as offering YES at `ask`.
            ask_resp = place_maker_order(symbol, "buy", 1 - ask, size, outcome="no")
        _track(ask_resp, "ask")
    except Exception as exc:  # noqa: BLE001
        print(f"Order placement failed for {symbol}: {exc}")
        if new_ids:
            try:
                cancel_orders(new_ids)
            except Exception:  # noqa: BLE001
                pass
        return False

    if new_ids:
        _open_orders[symbol] = new_ids
    # Two-sided only: both resting (or dry-run). If MOC cancelled one side, report soft failure.
    if DRY_RUN:
        return True
    if len(new_ids) < 2:
        print(f"Warning: {symbol} not fully two-sided ({len(new_ids)}/2 resting)")
        return False
    return True


# ---------------------------------------------------------------------------
# Public data: reward pools, rules, event books
# ---------------------------------------------------------------------------


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    r = requests.get(f"{GEMINI_PUBLIC}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _to_float(x: Any, default: float | None = None) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def get_reward_rules() -> dict[str, float]:
    """Liquidity Rewards config: max spread (cents) and min size to qualify."""
    rules = {"max_spread": 0.10, "min_size": 100.0}
    try:
        cfg = _get("/v1/prediction-markets/liquidity-rewards/config")
        if cfg.get("enabled"):
            rules["max_spread"] = _to_float(cfg.get("max_spread_cents"), 10) / 100
            rules["min_size"] = _to_float(cfg.get("min_size_threshold"), 100)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not read rewards config, using defaults: {exc}")
    return rules


def get_reward_events(limit: int = 100) -> list[dict[str, Any]]:
    """Events currently paying Liquidity Rewards (public endpoint)."""
    try:
        data = _get(
            "/v1/prediction-markets/liquidity-rewards/events",
            {"sort": "daily_pool_desc", "limit": limit},
        )
        return data.get("data", data if isinstance(data, list) else [])
    except Exception as exc:  # noqa: BLE001
        print(f"Error fetching reward events: {exc}")
        return []


def _minutes_left(ev: dict[str, Any]) -> float | None:
    end = _first(ev, "endsAt", "ends_at", "endTime", "end_time", "expiryDate")
    if not end:
        return None
    try:
        from datetime import datetime, timezone

        if isinstance(end, (int, float)):
            ts = end / 1000 if end > 1e12 else end
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return None


def rank_reward_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Score = daily reward pool / (qualifying makers + 1).

    A $100/day pool with no maker is worth more to us than a $100/day pool
    shared with 9 other makers. Events about to expire are skipped.
    """
    ranked = []
    for ev in events:
        pool = _to_float(_first(ev, "dailyPool", "daily_pool", "dailyPoolUsd", "daily_pool_usd"), 0.0)
        makers = int(_to_float(_first(ev, "qualifyingMakerCount", "qualifying_maker_count",
                                       "qualifyingMakers", "makerCount"), 0) or 0)
        left = _minutes_left(ev)
        if pool <= 0 or (left is not None and left < MIN_MINUTES_LEFT):
            continue
        ranked.append({
            "ticker": _first(ev, "eventTicker", "event_ticker", "ticker"),
            "title": ev.get("title", ""),
            "pool": pool,
            "makers": makers,
            "minutes_left": left,
            "score": pool / (makers + 1),
        })
    ranked.sort(key=lambda e: e["score"], reverse=True)
    return ranked


def allocate(ranked: list[dict[str, Any]], capital: float = CAPITAL_USDC,
             max_markets: int = MAX_MARKETS) -> list[dict[str, Any]]:
    """Split vault capital across the top events, proportional to score."""
    top = ranked[:max_markets]
    total = sum(e["score"] for e in top) or 1.0
    for e in top:
        e["capital"] = round(capital * e["score"] / total, 2)
    return top


def _book(contract: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Best bid / best ask / last for the YES side, whatever the field naming."""
    src = contract.get("prices") or contract
    bid = _to_float(_first(src, "bestBid", "best_bid", "bid", "buyYes"))
    ask = _to_float(_first(src, "bestAsk", "best_ask", "ask", "sellYes"))
    last = _to_float(_first(src, "lastTradePrice", "lastPrice", "markPrice", "last"))
    return bid, ask, last


def pick_contract(event_ticker: str) -> tuple[str, float] | None:
    """Pick the contract whose mid is closest to 0.50 (most two-way interest)."""
    try:
        ev = _get(f"/v1/prediction-markets/events/{event_ticker}")
    except Exception as exc:  # noqa: BLE001
        print(f"  could not load {event_ticker}: {exc}")
        return None
    ev = ev.get("data", ev) if isinstance(ev, dict) else ev
    best = None
    for c in ev.get("contracts", []):
        symbol = c.get("instrumentSymbol")
        if not symbol:
            continue
        bid, ask, last = _book(c)
        if bid and ask:
            mid = (bid + ask) / 2
        else:
            mid = last if last else 0.5
        if not 0.05 <= mid <= 0.95:
            continue
        if best is None or abs(mid - 0.5) < abs(best[1] - 0.5):
            best = (symbol, mid)
    return best


# ---------------------------------------------------------------------------
# Arc: mark deployed capital to market on the vault
# ---------------------------------------------------------------------------

VAULT_ABI = [
    {"type": "function", "name": "reportNAV", "stateMutability": "nonpayable",
     "inputs": [{"name": "newDeployedAssets", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "totalAssets", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
]


def report_nav(deployed_usdc: float) -> str:
    """Push the marked-to-market value of the MM account to the vault on Arc."""
    if not (VAULT and OPERATOR_KEY and RPC):
        raise RuntimeError("Set VAULT_ADDRESS, OPERATOR_PRIVATE_KEY and ARC_RPC")
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(RPC))
    acct = w3.eth.account.from_key(OPERATOR_KEY)
    vault = w3.eth.contract(address=Web3.to_checksum_address(VAULT), abi=VAULT_ABI)
    amount = int(round(deployed_usdc * 1_000_000))  # USDC has 6 decimals
    tx = vault.functions.reportNAV(amount).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": w3.eth.chain_id,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    return tx_hash.hex()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def print_ranking() -> list[dict[str, Any]]:
    ranked = rank_reward_events(get_reward_events())
    plan = allocate(ranked)
    print(f"{len(ranked)} events paying Liquidity Rewards. Top {len(plan)}:")
    for e in plan:
        left = f"{e['minutes_left']:.0f} min" if e["minutes_left"] is not None else "-"
        print(f"  {e['ticker']:<28} pool ${e['pool']:>7.2f}/day  makers {e['makers']:>2}  "
              f"score {e['score']:>7.2f}  ends in {left:>8}  -> ${e['capital']:.2f}")
    return plan


def run_cycle(rules: dict[str, float]) -> None:
    spread = min(QUOTE_SPREAD, rules["max_spread"])
    plan = print_ranking()
    for e in plan:
        picked = pick_contract(e["ticker"])
        if not picked:
            continue
        symbol, mid = picked
        # Contracts we can afford on each side with this market's capital, at least the
        # minimum size that qualifies for rewards, never above MAX_QUOTE_SIZE.
        affordable = e["capital"] / 2 / max(mid, 0.01)
        size = max(rules["min_size"], min(QUOTE_SIZE, affordable))
        print(f"→ {e['title']} | {symbol} mid={mid:.2f}")
        quote_market(symbol, mid_price=mid, size=size, spread=spread)


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "rank":
        print_ranking()
        return
    if len(sys.argv) > 2 and sys.argv[1] == "report-nav":
        print("reportNAV tx:", report_nav(float(sys.argv[2])))
        return

    print("DEPTH MM Bot started")
    op = _operator_address()
    print(f"Operator: {op or '(not configured)'}")
    print(f"Vault: {VAULT or '(not set)'}")
    print(f"Orders → {GEMINI_BASE} | data ← {GEMINI_PUBLIC} | dry_run={DRY_RUN} style={QUOTE_STYLE}")
    rules = get_reward_rules()
    print(f"Reward rules: max spread {rules['max_spread']:.2f}, min size {rules['min_size']:.0f}")
    while True:
        run_cycle(rules)
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
