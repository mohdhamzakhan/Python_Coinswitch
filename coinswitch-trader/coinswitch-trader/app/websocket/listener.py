"""
CoinSwitch WebSocket listener — disabled until credentials are confirmed.

The HFT WebSocket at wss://hft.coinswitch.co requires a valid authenticated
session.  Until the API client has working credentials the listener stays
idle and does NOT reconnect-loop, which was flooding the logs with 503 errors.

Once credentials are saved via the Settings page, restart the server and
the listener will attempt a connection.
"""
import asyncio
import json
import time
from typing import Callable, Optional
from loguru import logger
from config.settings import get_settings

settings = get_settings()

# ── Shared in-memory price / orderbook cache ──────────────────────────────────
_price_cache:     dict[str, float] = {}
_orderbook_cache: dict[str, dict]  = {}


def get_cached_price(symbol: str) -> Optional[float]:
    return _price_cache.get(symbol)


def get_cached_orderbook(symbol: str) -> Optional[dict]:
    return _orderbook_cache.get(symbol)


class WebSocketListener:
    """
    Connects to CoinSwitch HFT WebSocket for real-time market data.

    Will NOT start unless both api_key and api_secret are present.
    Backs off exponentially (max 60 s) on repeated failures.
    """

    WS_URL = "wss://hft.coinswitch.co/hft"

    def __init__(self):
        self._running  = False
        self._ws       = None
        self._callbacks: list[Callable] = []
        self._reconnect_delay = 5.0

    def on_message(self, callback: Callable):
        self._callbacks.append(callback)

    async def start(self):
        """Start the listener. Silently skips if no credentials are available."""
        api_key    = settings.coinswitch_api_key
        api_secret = settings.coinswitch_api_secret

        if not api_key or not api_secret:
            logger.info(
                "WebSocket listener not started — no API credentials configured. "
                "Add credentials in Settings to enable real-time data."
            )
            return

        self._running = True
        logger.info("Starting WebSocket listener…")

        while self._running:
            try:
                await self._connect_and_listen(api_key, api_secret)
                self._reconnect_delay = 5.0          # reset on clean disconnect
            except Exception as exc:
                logger.warning(
                    f"WebSocket error: {exc}. "
                    f"Reconnecting in {self._reconnect_delay:.0f}s…"
                )

            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def _connect_and_listen(self, api_key: str, api_secret: str):
        try:
            import websockets
        except ImportError:
            logger.error("websockets package not installed — run: pip install websockets")
            self._running = False
            return

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        # Build Ed25519 auth signature
        secret_bytes = bytes.fromhex(api_secret)
        private_key  = Ed25519PrivateKey.from_private_bytes(secret_bytes)
        epoch_ms     = str(int(time.time() * 1000))
        msg_bytes    = bytes(epoch_ms, "utf-8")
        signature    = private_key.sign(msg_bytes).hex()

        auth_payload = {
            "method": "auth",
            "params": {
                "api_key":   api_key,
                "signature": signature,
                "timestamp": epoch_ms,
            },
        }

        async with websockets.connect(
            self.WS_URL,
            ping_interval = 20,
            ping_timeout  = 10,
            close_timeout = 5,
        ) as ws:
            self._ws              = ws
            self._reconnect_delay = 5.0
            logger.info("WebSocket connected to CoinSwitch HFT")

            await ws.send(json.dumps(auth_payload))

            # Subscribe to BTC ticker (expand as needed)
            await ws.send(json.dumps({
                "method": "subscribe",
                "params": {"channels": ["BTCINR@ticker", "BTCUSDT@ticker"]},
            }))

            async for raw in ws:
                if not self._running:
                    break
                await self._process(raw)

    async def _process(self, raw: str):
        try:
            data  = json.loads(raw)
            etype = str(data.get("e") or data.get("type") or "").lower()

            if "ticker" in etype:
                sym   = data.get("s", "")
                price = data.get("c") or data.get("lastPrice")
                if sym and price:
                    # Normalise BTCUSDT → BTC/USDT
                    if "/" not in sym and len(sym) >= 6:
                        sym = sym[:-4] + "/" + sym[-4:]
                    _price_cache[sym] = float(price)

            elif "depth" in etype or "book" in etype:
                sym = data.get("s", "")
                if sym:
                    if "/" not in sym and len(sym) >= 6:
                        sym = sym[:-4] + "/" + sym[-4:]
                    _orderbook_cache[sym] = {
                        "bids":      data.get("b", []),
                        "asks":      data.get("a", []),
                        "timestamp": data.get("T", int(time.time() * 1000)),
                    }

            for cb in self._callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(data)
                    else:
                        cb(data)
                except Exception as exc:
                    logger.error(f"WS callback error: {exc}")

        except json.JSONDecodeError:
            pass
        except Exception as exc:
            logger.error(f"WS process error: {exc}")

    async def stop(self):
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        logger.info("WebSocket listener stopped")


# Module singleton
ws_listener = WebSocketListener()
