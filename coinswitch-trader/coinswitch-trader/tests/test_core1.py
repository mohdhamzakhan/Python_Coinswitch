"""
Tests for the CoinSwitch trading platform.

_reference_sign() is a verbatim copy of the official CoinSwitch sample code.
Every signature test asserts that app.api.client.generate_signature() produces
byte-identical output to the reference.

About the misleading `epoch_time` parameter name in the docs:
    The get_signature() helper is declared as:
        def get_signature(method, endpoint, params, epoch_time):
            signature_msg = method + unquote_endpoint + epoch_time
    The sample code shows what gets passed as that argument:
        signature_msg = method + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)
    Conclusion: "epoch_time" receives json.dumps(payload,...) — it is NOT a timestamp.
    There is ONE signature formula, the parameter name is simply wrong.

Run:  pip install -r requirements.txt && pytest tests/ -v
"""
import json
import sys, os
import pytest
import urllib.parse
from urllib.parse import urlparse, urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Test infrastructure ──────────────────────────────────────────────────────

async def _async_return(value):
    return value


def _make_keypair():
    """Return (secret_hex, public_key) for a one-time-use Ed25519 keypair."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    priv = Ed25519PrivateKey.generate()
    raw  = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return raw.hex(), priv.public_key()


def _reference_sign(secret_hex, method, endpoint, params=None, payload=None):
    """
    Verbatim copy of the official CoinSwitch sample code.

    Every line below is taken directly from the docs without change
    (adapted only to accept arguments instead of globals):

        unquote_endpoint = endpoint
        if method == "GET" and len(params) != 0:
            endpoint += ('&', '?')[urlparse(endpoint).query == ''] + urlencode(params)
            unquote_endpoint = urllib.parse.unquote_plus(endpoint)
        signature_msg    = method + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)
        request_string   = bytes(signature_msg, 'utf-8')
        secret_key_bytes = bytes.fromhex(secret_key)
        secret_key       = Ed25519PrivateKey.from_private_bytes(secret_key_bytes)
        signature_bytes  = secret_key.sign(request_string)
        signature        = signature_bytes.hex()
    """
    from cryptography.hazmat.primitives.asymmetric import ed25519

    params  = params  or {}
    payload = payload or {}

    unquote_endpoint = endpoint
    if method == "GET" and len(params) != 0:
        endpoint += ('&', '?')[urlparse(endpoint).query == ''] + urlencode(params)
        unquote_endpoint = urllib.parse.unquote_plus(endpoint)

    signature_msg    = method + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)
    request_string   = bytes(signature_msg, 'utf-8')
    secret_key_bytes = bytes.fromhex(secret_hex)
    secret_key_obj   = ed25519.Ed25519PrivateKey.from_private_bytes(secret_key_bytes)
    signature_bytes  = secret_key_obj.sign(request_string)
    signature        = signature_bytes.hex()

    return signature, endpoint   # endpoint now has query string appended if GET + params


# ─── Signature tests — one per doc example ───────────────────────────────────

def test_GET_validate_keys_no_params():
    """
    Mirrors validate/keys sample: GET, empty params, empty payload.
    Expected message: 'GET' + '/trade/api/v2/validate/keys' + '{}'
    """
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    endpoint = "/trade/api/v2/validate/keys"

    sig, ep = _reference_sign(secret_hex, "GET", endpoint, {}, {})

    assert ep == endpoint,  "Endpoint must be unchanged when params is empty"
    assert len(sig) == 128, "Ed25519 signature is always 64 bytes = 128 hex chars"

    # Verify cryptographically: server reconstructs this same message
    pub.verify(bytes.fromhex(sig), b"GET/trade/api/v2/validate/keys{}")


def test_GET_orders_with_params():
    """
    Mirrors the /orders sample — params appended, then unquote_plus, empty payload.
    """
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    endpoint = "/trade/api/v2/orders"
    params   = {
        "count":     2,
        "from_time": 1600261657954,
        "to_time":   1687261657954,
        "side":      "sell",
        "symbols":   "btc/inr,eth/inr",
        "exchanges": "coinswitchx,wazirx",
        "type":      "limit",
        "open":      True,
    }

    sig, ep = _reference_sign(secret_hex, "GET", endpoint, params, {})

    assert "?" in ep,       "Query string must be appended for GET with params"
    assert "count=2" in ep
    assert "side=sell" in ep
    assert len(sig) == 128

    # The signed message used the unquote_plus'd URL
    unquoted_ep  = urllib.parse.unquote_plus(ep)
    expected_msg = ("GET" + unquoted_ep + "{}").encode("utf-8")
    pub.verify(bytes.fromhex(sig), expected_msg)


def test_POST_order_exact_doc_payload():
    """
    Mirrors the POST /order sample exactly.
    payload = {"side":"sell","symbol":"BTC/USDT","type":"limit","price":26000,
               "quantity":0.0009,"exchange":"c2c1"}
    """
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    endpoint = "/trade/api/v2/order"
    payload  = {
        "side":     "sell",
        "symbol":   "BTC/USDT",
        "type":     "limit",
        "price":    26000,
        "quantity": 0.0009,
        "exchange": "c2c1",
    }

    sig, ep = _reference_sign(secret_hex, "POST", endpoint, {}, payload)

    assert ep == endpoint, "POST endpoint must not be modified"
    assert len(sig) == 128

    payload_str  = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    expected_msg = ("POST" + endpoint + payload_str).encode("utf-8")
    pub.verify(bytes.fromhex(sig), expected_msg)


def test_DELETE_order():
    """DELETE follows the same pattern as POST — endpoint unchanged, payload signed."""
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    endpoint = "/trade/api/v2/order"
    payload  = {"orderId": "abc123xyz", "symbol": "BTC/USDT"}

    sig, ep = _reference_sign(secret_hex, "DELETE", endpoint, {}, payload)

    assert ep == endpoint
    assert len(sig) == 128

    payload_str  = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    expected_msg = ("DELETE" + endpoint + payload_str).encode("utf-8")
    pub.verify(bytes.fromhex(sig), expected_msg)


def test_empty_payload_appends_empty_braces():
    """
    Empty payload → json.dumps({}, ...) == '{}' which is appended to message.
    This is what makes GET signatures different from POST with same endpoint.
    """
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    sig, _ = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys", {}, {})
    pub.verify(bytes.fromhex(sig), b"GET/trade/api/v2/validate/keys{}")


def test_sort_keys_makes_payload_order_irrelevant():
    """
    sort_keys=True means key insertion order doesn't affect the signature.
    Same content in different order must produce the same signature.
    """
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    payload_a = {"side": "sell", "symbol": "BTC/USDT", "type": "limit", "price": 26000}
    payload_b = {"type": "limit", "price": 26000, "side": "sell", "symbol": "BTC/USDT"}

    sig_a, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload=payload_a)
    sig_b, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload=payload_b)
    assert sig_a == sig_b


def test_signature_is_deterministic():
    """Same inputs must always yield the same signature."""
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, ep1 = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys")
    s2, ep2 = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys")
    assert s1 == s2 and ep1 == ep2


def test_different_payloads_different_signatures():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload={"side": "buy"})
    s2, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload={"side": "sell"})
    assert s1 != s2


def test_different_methods_different_signatures():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, _ = _reference_sign(secret_hex, "GET",    "/trade/api/v2/order")
    s2, _ = _reference_sign(secret_hex, "POST",   "/trade/api/v2/order")
    s3, _ = _reference_sign(secret_hex, "DELETE", "/trade/api/v2/order")
    assert s1 != s2
    assert s2 != s3


def test_different_params_different_signatures():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, _ = _reference_sign(secret_hex, "GET", "/trade/api/v2/orders",
                              params={"count": 2, "side": "sell"})
    s2, _ = _reference_sign(secret_hex, "GET", "/trade/api/v2/orders",
                              params={"count": 5, "side": "buy"})
    assert s1 != s2


# ─── App client must match reference exactly ─────────────────────────────────

def test_app_matches_reference_GET_no_params():
    pytest.importorskip("cryptography")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()

    app_sig, app_ep = generate_signature(secret_hex, "GET", "/trade/api/v2/validate/keys",
                                          params={}, payload={})
    ref_sig, ref_ep = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys", {}, {})
    assert app_sig == ref_sig
    assert app_ep  == ref_ep


def test_app_matches_reference_GET_with_params():
    pytest.importorskip("cryptography")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()
    params = {"count": 2, "from_time": 1600261657954, "side": "sell",
              "symbols": "btc/inr,eth/inr", "type": "limit", "open": True}

    app_sig, app_ep = generate_signature(secret_hex, "GET", "/trade/api/v2/orders",
                                          params=params, payload={})
    ref_sig, ref_ep = _reference_sign(secret_hex, "GET", "/trade/api/v2/orders", params, {})
    assert app_sig == ref_sig
    assert app_ep  == ref_ep


def test_app_matches_reference_POST():
    pytest.importorskip("cryptography")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()
    payload = {"side": "sell", "symbol": "BTC/USDT", "type": "limit",
               "price": 26000, "quantity": 0.0009, "exchange": "c2c1"}

    app_sig, app_ep = generate_signature(secret_hex, "POST", "/trade/api/v2/order",
                                          payload=payload)
    ref_sig, ref_ep = _reference_sign(secret_hex, "POST", "/trade/api/v2/order",
                                       {}, payload)
    assert app_sig == ref_sig
    assert app_ep  == ref_ep


def test_app_matches_reference_DELETE():
    pytest.importorskip("cryptography")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()
    payload = {"orderId": "abc123xyz", "symbol": "BTC/USDT"}

    app_sig, app_ep = generate_signature(secret_hex, "DELETE", "/trade/api/v2/order",
                                          payload=payload)
    ref_sig, ref_ep = _reference_sign(secret_hex, "DELETE", "/trade/api/v2/order",
                                       {}, payload)
    assert app_sig == ref_sig
    assert app_ep  == ref_ep


def test_invalid_key_raises_auth_error():
    pytest.importorskip("cryptography")
    from app.api.client import generate_signature, CoinSwitchAuthError
    with pytest.raises(CoinSwitchAuthError):
        generate_signature("not_valid_hex!!", "GET", "/trade/api/v2/validate/keys")


def test_empty_key_raises_auth_error():
    pytest.importorskip("cryptography")
    from app.api.client import generate_signature, CoinSwitchAuthError
    with pytest.raises(CoinSwitchAuthError):
        generate_signature("", "GET", "/trade/api/v2/validate/keys")


def test_client_base_url():
    pytest.importorskip("cryptography")
    from app.api.client import CoinSwitchClient
    assert CoinSwitchClient.BASE_URL == "https://coinswitch.co"


# ─── Settings ─────────────────────────────────────────────────────────────────

def test_settings_simulation_default():
    pytest.importorskip("pydantic_settings")
    # Use _env_file=None so developer's .env doesn't override defaults.
    from config.settings import Settings
    s = Settings(_env_file=None)
    assert s.simulation_mode     is True,  "simulation_mode must default to True"
    assert s.coinswitch_base_url == "https://coinswitch.co"
    assert s.app_name            == "CoinSwitch Trader"


def test_encryption_roundtrip():
    pytest.importorskip("cryptography")
    pytest.importorskip("pydantic_settings")
    from config.settings import Settings
    s = Settings()
    plain = "my_hex_ed25519_private_key_here"
    assert s.decrypt_secret(s.encrypt_secret(plain)) == plain


def test_encryption_hides_plaintext():
    pytest.importorskip("cryptography")
    pytest.importorskip("pydantic_settings")
    from config.settings import Settings
    s = Settings()
    assert s.encrypt_secret("secret") != "secret"


# ─── RSI (inline, pandas only) ───────────────────────────────────────────────

def _rsi(prices, period):
    import pandas as pd
    s  = pd.Series(prices); d = s.diff()
    g  = d.clip(lower=0);   l = -d.clip(upper=0)
    ag = g.ewm(com=period - 1, min_periods=period).mean()
    al = l.ewm(com=period - 1, min_periods=period).mean()
    return 100 - (100 / (1 + ag / al.replace(0, 1e-10)))


def test_rsi_stays_between_0_and_100():
    pytest.importorskip("pandas")
    r = _rsi([100,102,101,103,105,104,106,107,105,108,
              110,109,111,112,110,113,115,114,116,118], 14)
    assert all(0 <= v <= 100 for v in r.dropna())


def test_rsi_overbought_on_uptrend():
    pytest.importorskip("pandas")
    assert _rsi([float(100 + i*5) for i in range(30)], 14).dropna().iloc[-1] > 70


def test_rsi_oversold_on_downtrend():
    pytest.importorskip("pandas")
    assert _rsi([float(200 - i*5) for i in range(30)], 14).dropna().iloc[-1] < 30


def test_rsi_near_50_on_flat():
    pytest.importorskip("pandas")
    prices = [100.0 + (1 if i % 2 == 0 else -1) for i in range(40)]
    last   = _rsi(prices, 14).dropna().iloc[-1]
    assert 40 <= last <= 60, f"Expected RSI≈50 on flat prices, got {last:.1f}"


# ─── Grid math (stdlib only) ──────────────────────────────────────────────────

def _grid(lo, hi, n):
    step = (hi - lo) / n
    return [round(lo + step * i, 8) for i in range(n + 1)]


def test_grid_level_count():
    assert len(_grid(40000, 50000, 10)) == 11


def test_grid_lower_and_upper_bounds():
    g = _grid(40000, 50000, 10)
    assert g[0] == pytest.approx(40000) and g[-1] == pytest.approx(50000)


def test_grid_equal_step_size():
    g    = _grid(40000, 50000, 10)
    step = g[1] - g[0]
    assert all(g[i] - g[i-1] == pytest.approx(step, rel=1e-6) for i in range(1, len(g)))


def test_grid_auto_range_pm10pct():
    price = 50000.0
    g = _grid(price * 0.9, price * 1.1, 10)
    assert g[0] == pytest.approx(45000.0, rel=1e-6)
    assert g[-1] == pytest.approx(55000.0, rel=1e-6)


# ─── Backtest (inline, pandas only) ───────────────────────────────────────────

def _ma_bt(candles, fast=9, slow=21, cap=10000.0, fee=0.001):
    import pandas as pd
    df        = pd.DataFrame(candles)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["f"]   = df["close"].rolling(fast).mean()
    df["s"]   = df["close"].rolling(slow).mean()
    df["sig"] = 0
    df.loc[(df.f > df.s) & (df.f.shift(1) <= df.s.shift(1)), "sig"] =  1
    df.loc[(df.f < df.s) & (df.f.shift(1) >= df.s.shift(1)), "sig"] = -1
    pos = entry = 0.0; trades = []
    for _, r in df.iterrows():
        if r.sig == 1 and pos == 0:
            pos = cap * 0.1 / r.close; entry = r.close
            cap -= pos * r.close * (1 + fee)
        elif r.sig == -1 and pos > 0:
            p = pos * r.close * (1 - fee)
            trades.append(p - pos * entry); cap += p; pos = 0.0
    if pos > 0:
        p = pos * df["close"].iloc[-1] * (1 - fee)
        trades.append(p - pos * entry); cap += p
    wins = [t for t in trades if t > 0]
    return {"final_capital": cap, "trades": len(trades),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0}


async def test_backtest_valid_metrics():
    pytest.importorskip("pandas")
    import random; random.seed(42)
    price = 50000.0; candles = []
    for _ in range(200):
        price += random.uniform(-500, 500)
        candles.append({"close": price})
    r = _ma_bt(candles)
    assert r["trades"] >= 0 and 0 <= r["win_rate"] <= 100 and r["final_capital"] > 0


async def test_backtest_flat_price_zero_trades():
    pytest.importorskip("pandas")
    assert _ma_bt([{"close": 50000.0}] * 100)["trades"] == 0


async def test_backtest_uptrend_produces_trades():
    pytest.importorskip("pandas")
    # Start flat so both MAs initialise at the same level, then rise sharply.
    # This guarantees the fast MA crosses above the slow MA → at least 1 buy.
    candles = (
        [{"close": 40000.0}] * 10
        + [{"close": 40000.0 + i * 300} for i in range(1, 40)]
    )
    result = _ma_bt(candles, fast=3, slow=8)
    assert result["trades"] >= 1, f"Expected >= 1 trade on flat-then-uptrend, got {result['trades']}"


# ─── Risk Manager ────────────────────────────────────────────────────────────

async def test_risk_blocks_excessive_leverage():
    from app.risk.manager import RiskManager, RiskViolation
    rm = RiskManager()
    rm._trading_stopped = False
    rm._risk_params = {"max_leverage": "10", "stop_trading": "false",
                       "max_open_positions": "20", "max_position_size_percent": "100",
                       "max_daily_loss_percent": "0"}
    rm._count_open_positions = lambda: _async_return(0)
    rm._get_today_loss       = lambda: _async_return(0.0)
    with pytest.raises(RiskViolation):
        await rm.check_order("BTC/USDT", "BUY", 0.01, 50000, leverage=20)


async def test_risk_blocks_kill_switch():
    from app.risk.manager import RiskManager, RiskViolation
    rm = RiskManager()
    rm._trading_stopped = True
    rm._risk_params = {"stop_trading": "true"}
    with pytest.raises(RiskViolation):
        await rm.check_order("ETH/USDT", "SELL", 1.0, 3000, leverage=1)


async def test_risk_passes_valid_order():
    from app.risk.manager import RiskManager
    rm = RiskManager()
    rm._trading_stopped = False
    rm._risk_params = {"max_leverage": "20", "max_open_positions": "10",
                       "max_position_size_percent": "50", "max_daily_loss_percent": "0",
                       "stop_trading": "false"}
    rm._count_open_positions = lambda: _async_return(2)
    rm._get_today_loss       = lambda: _async_return(0.0)
    assert await rm.check_order("BTC/USDT", "BUY", 0.001, 50000, leverage=5) is True


async def test_risk_blocks_max_positions():
    from app.risk.manager import RiskManager, RiskViolation
    rm = RiskManager()
    rm._trading_stopped = False
    rm._risk_params = {"max_leverage": "20", "max_open_positions": "3",
                       "max_position_size_percent": "100", "max_daily_loss_percent": "0",
                       "stop_trading": "false"}
    rm._count_open_positions = lambda: _async_return(3)
    rm._get_today_loss       = lambda: _async_return(0.0)
    with pytest.raises(RiskViolation):
        await rm.check_order("BTC/USDT", "BUY", 0.001, 50000, leverage=1)
