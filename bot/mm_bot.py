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


def get_active_markets() -> list[dict[str, Any]]:
    """Fetch markets with Liquidity Rewards (public endpoint)."""
    try:
        r = requests.get(
            f"{GEMINI_BASE}/v1/prediction-markets/events",
            params={"status": "active", "limit": 20},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:  # noqa: BLE001
        print(f"Error fetching markets: {e}")
        return []


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
        f"ASK {_format_price(ask)} x {_format_qty(size)}  outcome={QUOTE_OUTCOME}"
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
        bid_resp = place_maker_order(symbol, "buy", bid, size)
        _track(bid_resp, "bid")
        ask_resp = place_maker_order(symbol, "sell", ask, size)
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


def main() -> None:
    print("DEPTH MM Bot started")
    op = _operator_address()
    if op:
        print(f"Operator: {op}")
    else:
        print("Operator: (not configured)")
    print(f"Vault: {VAULT or '(not set)'}")
    print(
        f"Quoting: size={QUOTE_SIZE} max={MAX_QUOTE_SIZE} spread={QUOTE_SPREAD} "
        f"outcome={QUOTE_OUTCOME} dry_run={DRY_RUN} nonce={NONCE_MODE}"
    )
    print(f"Gemini base: {GEMINI_BASE}")

    while True:
        markets = get_active_markets()
        print(f"Found {len(markets)} active markets")
        for m in markets[:5]:  # limit for MVP
            title = m.get("title", "unknown")
            contracts = m.get("contracts", [])
            if not contracts:
                continue
            contract = contracts[0]
            symbol = contract.get("instrumentSymbol", "")
            mid = contract.get("lastPrice") or contract.get("markPrice") or 0.5
            try:
                mid_f = float(mid)
            except (TypeError, ValueError):
                mid_f = 0.5
            print(f"→ {title}: {symbol}")
            quote_market(symbol, mid_price=mid_f)
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
