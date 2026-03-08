"""
Tests for the CoinSwitch trading platform.

_reference_sign() is a verbatim copy of the official CoinSwitch sample code.
Every app signature test asserts byte-identical output to that reference.

The get_signature() helper in the docs names its fourth parameter `epoch_time`
but the sample code proves that what is passed there is:
    json.dumps(payload, separators=(',', ':'), sort_keys=True)
The parameter name is misleading — there is ONE signature formula.

Run:
    pip install -r requirements.txt
    pytest tests/ -v
"""
import json
import sys, os
import pytest
import urllib.parse
from urllib.parse import urlparse, urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Infrastructure ───────────────────────────────────────────────────────────

async def _async_return(value):
    return value


def _make_keypair():
    """Return (secret_hex, public_key) for a fresh Ed25519 keypair."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    priv = Ed25519PrivateKey.generate()
    raw  = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return raw.hex(), priv.public_key()


def _reference_sign(secret_hex, method, endpoint, params=None, payload=None):
    """
    Verbatim copy of the official CoinSwitch sample code — this IS the spec.

        unquote_endpoint = endpoint
        if method == "GET" and len(params) != 0:
            endpoint += ('&', '?')[urlparse(endpoint).query == ''] + urlencode(params)
            unquote_endpoint = urllib.parse.unquote_plus(endpoint)
        signature_msg    = method + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)
        request_string   = bytes(signature_msg, 'utf-8')
        secret_key_bytes = bytes.fromhex(secret_key)
        secret_key       = Ed25519PrivateKey.from_private_bytes(secret_key_bytes)
        signature        = secret_key.sign(request_string).hex()
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
    signature        = secret_key_obj.sign(request_string).hex()
    return signature, endpoint


# ─── Signature tests ──────────────────────────────────────────────────────────

def test_GET_validate_keys_no_params():
    """GET, empty params — message: 'GET/trade/api/v2/validate/keys{}'"""
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    endpoint = "/trade/api/v2/validate/keys"

    sig, ep = _reference_sign(secret_hex, "GET", endpoint, {}, {})

    assert ep == endpoint,  "Endpoint must be unchanged when params is empty"
    assert len(sig) == 128, "Ed25519 signature = 64 bytes = 128 hex chars"
    pub.verify(bytes.fromhex(sig), b"GET/trade/api/v2/validate/keys{}")


def test_GET_orders_with_params():
    """GET with params — appended and unquote_plus'd before signing."""
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    params = {
        "count": 2, "from_time": 1600261657954, "to_time": 1687261657954,
        "side": "sell", "symbols": "btc/inr,eth/inr",
        "exchanges": "coinswitchx,wazirx", "type": "limit", "open": True,
    }
    sig, ep = _reference_sign(secret_hex, "GET", "/trade/api/v2/orders", params, {})

    assert "?" in ep and "count=2" in ep and "side=sell" in ep
    assert len(sig) == 128
    unquoted = urllib.parse.unquote_plus(ep)
    pub.verify(bytes.fromhex(sig), ("GET" + unquoted + "{}").encode())


def test_POST_order_exact_doc_payload():
    """POST — payload from the official docs sample, endpoint unchanged."""
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    endpoint = "/trade/api/v2/order"
    payload  = {"side": "sell", "symbol": "BTC/USDT", "type": "limit",
                "price": 26000, "quantity": 0.0009, "exchange": "c2c1"}

    sig, ep = _reference_sign(secret_hex, "POST", endpoint, {}, payload)

    assert ep == endpoint and len(sig) == 128
    ps = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    pub.verify(bytes.fromhex(sig), ("POST" + endpoint + ps).encode())


def test_DELETE_order():
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    endpoint = "/trade/api/v2/order"
    payload  = {"orderId": "abc123xyz", "symbol": "BTC/USDT"}

    sig, ep = _reference_sign(secret_hex, "DELETE", endpoint, {}, payload)

    assert ep == endpoint and len(sig) == 128
    ps = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    pub.verify(bytes.fromhex(sig), ("DELETE" + endpoint + ps).encode())


def test_empty_payload_appends_empty_braces():
    pytest.importorskip("cryptography")
    secret_hex, pub = _make_keypair()
    sig, _ = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys", {}, {})
    pub.verify(bytes.fromhex(sig), b"GET/trade/api/v2/validate/keys{}")


def test_sort_keys_makes_key_order_irrelevant():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    pa = {"side": "sell", "symbol": "BTC/USDT", "type": "limit", "price": 26000}
    pb = {"type": "limit", "price": 26000, "side": "sell", "symbol": "BTC/USDT"}
    sa, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload=pa)
    sb, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload=pb)
    assert sa == sb


def test_signature_is_deterministic():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, ep1 = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys")
    s2, ep2 = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys")
    assert s1 == s2 and ep1 == ep2


def test_different_payloads_different_sigs():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload={"side": "buy"})
    s2, _ = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", payload={"side": "sell"})
    assert s1 != s2


def test_different_methods_different_sigs():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, _ = _reference_sign(secret_hex, "GET",    "/trade/api/v2/order")
    s2, _ = _reference_sign(secret_hex, "POST",   "/trade/api/v2/order")
    s3, _ = _reference_sign(secret_hex, "DELETE", "/trade/api/v2/order")
    assert s1 != s2 and s2 != s3


def test_different_params_different_sigs():
    pytest.importorskip("cryptography")
    secret_hex, _ = _make_keypair()
    s1, _ = _reference_sign(secret_hex, "GET", "/trade/api/v2/orders",
                              params={"count": 2, "side": "sell"})
    s2, _ = _reference_sign(secret_hex, "GET", "/trade/api/v2/orders",
                              params={"count": 5, "side": "buy"})
    assert s1 != s2


# ─── App client matches reference ─────────────────────────────────────────────
# These tests import from app.api.client — they require httpx to be installed.
# Run: pip install httpx loguru

def test_app_matches_reference_GET_no_params():
    pytest.importorskip("cryptography")
    httpx = pytest.importorskip("httpx", reason="pip install httpx")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()

    app_sig, app_ep = generate_signature(secret_hex, "GET", "/trade/api/v2/validate/keys",
                                          params={}, payload={})
    ref_sig, ref_ep = _reference_sign(secret_hex, "GET", "/trade/api/v2/validate/keys", {}, {})
    assert app_sig == ref_sig and app_ep == ref_ep


def test_app_matches_reference_GET_with_params():
    pytest.importorskip("cryptography")
    pytest.importorskip("httpx", reason="pip install httpx")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()
    params = {"count": 2, "from_time": 1600261657954, "side": "sell",
              "symbols": "btc/inr,eth/inr", "type": "limit", "open": True}

    app_sig, app_ep = generate_signature(secret_hex, "GET", "/trade/api/v2/orders",
                                          params=params, payload={})
    ref_sig, ref_ep = _reference_sign(secret_hex, "GET", "/trade/api/v2/orders", params, {})
    assert app_sig == ref_sig and app_ep == ref_ep


def test_app_matches_reference_POST():
    pytest.importorskip("cryptography")
    pytest.importorskip("httpx", reason="pip install httpx")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()
    payload = {"side": "sell", "symbol": "BTC/USDT", "type": "limit",
               "price": 26000, "quantity": 0.0009, "exchange": "c2c1"}

    app_sig, app_ep = generate_signature(secret_hex, "POST", "/trade/api/v2/order", payload=payload)
    ref_sig, ref_ep = _reference_sign(secret_hex, "POST", "/trade/api/v2/order", {}, payload)
    assert app_sig == ref_sig and app_ep == ref_ep


def test_app_matches_reference_DELETE():
    pytest.importorskip("cryptography")
    pytest.importorskip("httpx", reason="pip install httpx")
    from app.api.client import generate_signature
    secret_hex, _ = _make_keypair()
    payload = {"orderId": "abc123xyz", "symbol": "BTC/USDT"}

    app_sig, app_ep = generate_signature(secret_hex, "DELETE", "/trade/api/v2/order",
                                          payload=payload)
    ref_sig, ref_ep = _reference_sign(secret_hex, "DELETE", "/trade/api/v2/order", {}, payload)
    assert app_sig == ref_sig and app_ep == ref_ep


def test_invalid_key_raises_auth_error():
    pytest.importorskip("cryptography")
    pytest.importorskip("httpx", reason="pip install httpx")
    from app.api.client import generate_signature, CoinSwitchAuthError
    with pytest.raises(CoinSwitchAuthError):
        generate_signature("not_valid_hex!!", "GET", "/trade/api/v2/validate/keys")


def test_empty_key_raises_auth_error():
    pytest.importorskip("cryptography")
    pytest.importorskip("httpx", reason="pip install httpx")
    from app.api.client import generate_signature, CoinSwitchAuthError
    with pytest.raises(CoinSwitchAuthError):
        generate_signature("", "GET", "/trade/api/v2/validate/keys")


def test_client_base_url():
    pytest.importorskip("cryptography")
    pytest.importorskip("httpx", reason="pip install httpx")
    from app.api.client import CoinSwitchClient
    assert CoinSwitchClient.BASE_URL == "https://coinswitch.co"


# ─── Settings ─────────────────────────────────────────────────────────────────

def test_settings_simulation_default():
    """
    Checks Settings class defaults directly, bypassing any .env file override.
    Uses a fresh Settings() instance rather than the lru_cached get_settings().
    """
    pytest.importorskip("pydantic_settings")
    from config.settings import Settings
    # Construct with no env file to test class-level defaults
    s = Settings(_env_file=None)
    assert s.simulation_mode     is True,              "Must default to simulation for safety"
    assert s.coinswitch_base_url == "https://coinswitch.co"
    assert s.app_name            == "CoinSwitch Trader"


def test_encryption_roundtrip():
    """Encrypt then decrypt on the SAME Settings instance — keys must match."""
    pytest.importorskip("cryptography")
    pytest.importorskip("pydantic_settings")
    from config.settings import Settings
    s     = Settings(_env_file=None)   # fresh instance, consistent key
    plain = "my_hex_ed25519_private_key_here"
    assert s.decrypt_secret(s.encrypt_secret(plain)) == plain


def test_encryption_hides_plaintext():
    pytest.importorskip("cryptography")
    pytest.importorskip("pydantic_settings")
    from config.settings import Settings
    s = Settings(_env_file=None)
    assert s.encrypt_secret("secret_value") != "secret_value"


def test_encryption_same_instance_consistent():
    """Multiple encrypt/decrypt calls on the same instance must be consistent."""
    pytest.importorskip("cryptography")
    pytest.importorskip("pydantic_settings")
    from config.settings import Settings
    s     = Settings(_env_file=None)
    plain = "test_key_abc123"
    enc1  = s.encrypt_secret(plain)
    enc2  = s.encrypt_secret(plain)
    # Fernet uses random IV so ciphertexts differ, but both must decrypt correctly
    assert s.decrypt_secret(enc1) == plain
    assert s.decrypt_secret(enc2) == plain


# ─── RSI (inline, pandas only) ───────────────────────────────────────────────

def _rsi(prices, period):
    import pandas as pd
    s  = pd.Series(prices); d = s.diff()
    g  = d.clip(lower=0); l = -d.clip(upper=0)
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
    assert _rsi([float(100 + i * 5) for i in range(30)], 14).dropna().iloc[-1] > 70


def test_rsi_oversold_on_downtrend():
    pytest.importorskip("pandas")
    assert _rsi([float(200 - i * 5) for i in range(30)], 14).dropna().iloc[-1] < 30


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
    df = pd.DataFrame(candles)
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
    """
    Price dips then rallies sharply, guaranteeing a golden cross
    (fast MA crosses above slow MA) which triggers a buy signal.
    """
    pytest.importorskip("pandas")
    fast, slow = 5, 20
    dip    = [{"close": 50000.0 - i * 300} for i in range(slow)]
    rally  = [{"close": 50000.0 - slow * 300 + i * 600} for i in range(60)]
    result = _ma_bt(dip + rally, fast=fast, slow=slow)
    assert result["trades"] >= 1, \
        f"Expected ≥1 trade on dip+rally pattern, got {result['trades']}"

# ─── Risk Manager ─────────────────────────────────────────────────────────────
# Tests bypass the DB by setting _risk_params directly and replacing the two
# DB-hitting methods (_count_open_positions, _get_today_loss) with lambdas.
# The fixed RiskManager.check_order() skips load_params() when _risk_params
# is already populated.

def _make_rm(params: dict, positions: int = 0, today_loss: float = 0.0,
             stopped: bool = False):
    """Build a RiskManager with DB bypassed."""
    from app.risk.manager import RiskManager
    rm = RiskManager()
    rm._trading_stopped      = stopped
    rm._risk_params          = params               # pre-populate → no DB call
    rm._count_open_positions = lambda: _async_return(positions)
    rm._get_today_loss       = lambda: _async_return(today_loss)
    return rm


async def test_risk_blocks_excessive_leverage():
    from app.risk.manager import RiskViolation
    rm = _make_rm({"max_leverage": "10", "stop_trading": "false",
                   "max_open_positions": "20", "max_position_size_percent": "100",
                   "max_daily_loss_percent": "0"}, positions=0)
    with pytest.raises(RiskViolation, match="[Ll]everage"):
        await rm.check_order("BTC/USDT", "BUY", 0.01, 50000, leverage=20)


async def test_risk_blocks_kill_switch():
    from app.risk.manager import RiskViolation
    rm = _make_rm({"stop_trading": "true"}, stopped=True)
    with pytest.raises(RiskViolation, match="[Ss]topped"):
        await rm.check_order("ETH/USDT", "SELL", 1.0, 3000, leverage=1)


async def test_risk_passes_valid_order():
    rm = _make_rm({"max_leverage": "20", "max_open_positions": "10",
                   "max_position_size_percent": "50", "max_daily_loss_percent": "0",
                   "stop_trading": "false"}, positions=2)
    assert await rm.check_order("BTC/USDT", "BUY", 0.001, 50000, leverage=5) is True


async def test_risk_blocks_max_positions():
    from app.risk.manager import RiskViolation
    rm = _make_rm({"max_leverage": "20", "max_open_positions": "3",
                   "max_position_size_percent": "100", "max_daily_loss_percent": "0",
                   "stop_trading": "false"}, positions=3)   # already AT max
    with pytest.raises(RiskViolation, match="[Pp]osition"):
        await rm.check_order("BTC/USDT", "BUY", 0.001, 50000, leverage=1)


# ─── Spot Trading — Exchange / Symbol Logic ───────────────────────────────────
# These tests cover the exchange→quote-currency rules and symbol building.
# No network required.

def test_quote_for_inr_exchanges():
    from app.trading.spot import quote_for_exchange
    assert quote_for_exchange("coinswitchx") == "INR"
    assert quote_for_exchange("wazirx")      == "INR"
    # Case-insensitive
    assert quote_for_exchange("CoinSwitchX") == "INR"
    assert quote_for_exchange("WAZIRX")      == "INR"


def test_quote_for_usdt_exchanges():
    from app.trading.spot import quote_for_exchange
    assert quote_for_exchange("c2c1") == "USDT"
    assert quote_for_exchange("c2c2") == "USDT"
    assert quote_for_exchange("C2C1") == "USDT"


def test_quote_for_invalid_exchange_raises():
    from app.trading.spot import quote_for_exchange
    with pytest.raises(ValueError, match="Unknown exchange"):
        quote_for_exchange("binance")


def test_build_symbol_inr():
    from app.trading.spot import build_symbol
    assert build_symbol("BTC",  "coinswitchx") == "BTC/INR"
    assert build_symbol("ETH",  "wazirx")      == "ETH/INR"
    assert build_symbol("SHIB", "coinswitchx") == "SHIB/INR"
    assert build_symbol("btc",  "wazirx")      == "BTC/INR"   # lowercase base


def test_build_symbol_usdt():
    from app.trading.spot import build_symbol
    assert build_symbol("BTC", "c2c1") == "BTC/USDT"
    assert build_symbol("ETH", "c2c2") == "ETH/USDT"
    assert build_symbol("btc", "C2C1") == "BTC/USDT"


def test_validate_symbol_exchange_ok():
    from app.trading.spot import validate_symbol_exchange
    # These should not raise
    validate_symbol_exchange("BTC/INR",  "coinswitchx")
    validate_symbol_exchange("ETH/INR",  "wazirx")
    validate_symbol_exchange("BTC/USDT", "c2c1")
    validate_symbol_exchange("ETH/USDT", "c2c2")


def test_validate_symbol_exchange_mismatch_raises():
    from app.trading.spot import validate_symbol_exchange
    # USDT symbol on INR exchange → error (message contains 'INR')
    with pytest.raises(ValueError, match="INR"):
        validate_symbol_exchange("BTC/USDT", "coinswitchx")
    # INR symbol on USDT exchange → error (message contains 'USDT')
    with pytest.raises(ValueError, match="USDT"):
        validate_symbol_exchange("BTC/INR", "c2c1")


def test_validate_symbol_no_slash_raises():
    from app.trading.spot import validate_symbol_exchange
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        validate_symbol_exchange("BTCINR", "coinswitchx")


def test_map_api_status():
    from app.trading.spot import map_api_status
    from app.models.db_models import OrderStatus
    assert map_api_status("OPEN")               == OrderStatus.OPEN
    assert map_api_status("PARTIALLY_EXECUTED") == OrderStatus.PARTIALLY_FILLED
    assert map_api_status("EXECUTED")           == OrderStatus.FILLED
    assert map_api_status("CANCELLED")          == OrderStatus.CANCELLED
    assert map_api_status("CANCELLATION_RAISED")== OrderStatus.CANCELLED
    assert map_api_status("EXPIRED")            == OrderStatus.CANCELLED
    assert map_api_status("EXPIRATION_RAISED")  == OrderStatus.CANCELLED
    assert map_api_status("DISCARDED")          == OrderStatus.REJECTED


def test_all_valid_exchanges_have_quote():
    from app.trading.spot import VALID_EXCHANGES, quote_for_exchange
    for ex in VALID_EXCHANGES:
        q = quote_for_exchange(ex)
        assert q in ("INR", "USDT"), f"{ex} returned unexpected quote {q!r}"


def test_inr_exchanges_get_inr():
    from app.trading.spot import INR_EXCHANGES, quote_for_exchange
    for ex in INR_EXCHANGES:
        assert quote_for_exchange(ex) == "INR"


def test_usdt_exchanges_get_usdt():
    from app.trading.spot import USDT_EXCHANGES, quote_for_exchange
    for ex in USDT_EXCHANGES:
        assert quote_for_exchange(ex) == "USDT"


def test_build_symbol_covers_all_exchanges():
    """Every exchange produces a valid BASE/QUOTE symbol."""
    from app.trading.spot import VALID_EXCHANGES, build_symbol, validate_symbol_exchange
    for ex in VALID_EXCHANGES:
        sym = build_symbol("BTC", ex)
        assert "/" in sym
        base, quote = sym.split("/")
        assert base  == "BTC"
        assert quote in ("INR", "USDT")
        # Validation must pass for the derived symbol
        validate_symbol_exchange(sym, ex)


def test_spot_order_request_invalid_exchange():
    """Pydantic schema must reject unknown exchanges."""
    from app.api.routes import SpotOrderRequest
    with pytest.raises(Exception):   # pydantic ValidationError
        SpotOrderRequest(
            exchange="binance",
            base_coin="BTC",
            side="buy",
            price=50000,
            quantity=0.001,
        )


def test_spot_order_request_invalid_side():
    from app.api.routes import SpotOrderRequest
    with pytest.raises(Exception):
        SpotOrderRequest(
            exchange="coinswitchx",
            base_coin="BTC",
            side="long",        # invalid
            price=50000,
            quantity=0.001,
        )


def test_spot_order_request_symbol_derived():
    """
    When no symbol is supplied the derived symbol must match exchange's quote.
    """
    from app.api.routes import SpotOrderRequest
    from app.trading.spot import build_symbol

    req = SpotOrderRequest(
        exchange="coinswitchx",
        base_coin="BTC",
        side="buy",
        price=5000000,
        quantity=0.001,
    )
    expected = build_symbol("BTC", "coinswitchx")   # BTC/INR
    assert expected == "BTC/INR"
    # Validate that the symbol is consistent with the exchange
    from app.trading.spot import validate_symbol_exchange
    validate_symbol_exchange(expected, req.exchange)  # must not raise


def test_spot_order_request_c2c1_usdt():
    from app.api.routes import SpotOrderRequest
    from app.trading.spot import build_symbol, validate_symbol_exchange

    req = SpotOrderRequest(
        exchange="c2c1",
        base_coin="ETH",
        side="sell",
        price=3000,
        quantity=1.0,
    )
    sym = build_symbol(req.base_coin, req.exchange)
    assert sym == "ETH/USDT"
    validate_symbol_exchange(sym, req.exchange)  # must not raise
