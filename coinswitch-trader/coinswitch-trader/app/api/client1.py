"""
CoinSwitch Trading API Client — Ed25519 Authentication.

There is ONE signature scheme. The official docs show it two ways:

  ① get_signature helper (parameter name is misleading):
        signature_msg = method + unquote_endpoint + epoch_time
        # "epoch_time" receives json.dumps(payload,...) — NOT a timestamp

  ② Sample code (explicit):
        signature_msg = method + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)

  Both are identical. The full algorithm:

        unquote_endpoint = endpoint
        if method == "GET" and len(params) != 0:
            endpoint += ('&', '?')[urlparse(endpoint).query == ''] + urlencode(params)
            unquote_endpoint = urllib.parse.unquote_plus(endpoint)
        signature_msg    = method + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)
        request_string   = bytes(signature_msg, 'utf-8')
        secret_key_bytes = bytes.fromhex(secret_key)
        secret_key       = Ed25519PrivateKey.from_private_bytes(secret_key_bytes)
        signature        = secret_key.sign(request_string).hex()

Headers for every authenticated request:
    Content-Type    : application/json
    X-AUTH-APIKEY   : <api_key>
    X-AUTH-SIGNATURE: <signature>

Base URL: https://coinswitch.co
"""

import json
import httpx
import urllib.parse
from urllib.parse import urlparse, urlencode
from typing import Optional
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from loguru import logger
from config.settings import get_settings

settings = get_settings()


# ── Exceptions ────────────────────────────────────────────────────────────────

class CoinSwitchAuthError(Exception):
    """Raised when credentials are missing or the Ed25519 key is malformed."""


class CoinSwitchAPIError(Exception):
    """Raised when the API returns a 4xx / 5xx response."""

    def __init__(self, message: str, status_code: int = 0, response: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response    = response or {}


# ── Signature ─────────────────────────────────────────────────────────────────

def generate_signature(
    secret_key_hex: str,
    method:         str,
    endpoint:       str,
    params:         dict = None,
    payload:        dict = None,
) -> tuple[str, str]:
    """
    Generate the Ed25519 request signature.

    Transcribed verbatim from the official CoinSwitch sample code:

        unquote_endpoint = endpoint
        if method == "GET" and len(params) != 0:
            endpoint += ('&', '?')[urlparse(endpoint).query == ''] + urlencode(params)
            unquote_endpoint = urllib.parse.unquote_plus(endpoint)
        signature_msg    = method + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)
        request_string   = bytes(signature_msg, 'utf-8')
        secret_key_bytes = bytes.fromhex(secret_key)
        secret_key       = Ed25519PrivateKey.from_private_bytes(secret_key_bytes)
        signature        = secret_key.sign(request_string).hex()

    Note — misleading parameter name in the docs:
        The get_signature() helper is defined as get_signature(method, endpoint, params, epoch_time)
        and builds:  signature_msg = method + unquote_endpoint + epoch_time
        The argument called "epoch_time" receives json.dumps(payload, ...) — it is NOT a timestamp.
        The formula is identical to the sample code above.

    Args:
        secret_key_hex : 64-char hex string — the raw Ed25519 private key from CoinSwitch.
        method         : "GET", "POST", or "DELETE".
        endpoint       : Path only, e.g. "/trade/api/v2/order".
        params         : Query-string dict (GET requests). Ignored for POST / DELETE.
        payload        : JSON body dict (POST / DELETE). Empty dict → signs as "{}".

    Returns:
        (signature_hex, final_endpoint)
        signature_hex : 128-char hex — the value for X-AUTH-SIGNATURE.
        final_endpoint: endpoint with query string appended (GET + params only).
    """
    if not secret_key_hex:
        raise CoinSwitchAuthError(
            "Ed25519 secret key (hex) is required. "
            "Set COINSWITCH_API_SECRET in .env or via the Settings page."
        )

    params  = params  or {}
    payload = payload or {}

    # Step 1 — build endpoint + query string (GET only)
    unquote_endpoint = endpoint
    final_endpoint   = endpoint

    if method.upper() == "GET" and len(params) != 0:
        separator      = ('&', '?')[urlparse(endpoint).query == '']
        final_endpoint = endpoint + separator + urlencode(params)
        unquote_endpoint = urllib.parse.unquote_plus(final_endpoint)

    # Step 2 — build the message (payload JSON is the trailing component)
    signature_msg  = method.upper() + unquote_endpoint + json.dumps(payload, separators=(',', ':'), sort_keys=True)
    request_string = bytes(signature_msg, 'utf-8')

    # Step 3 — Ed25519 sign
    try:
        secret_key_bytes = bytes.fromhex(secret_key_hex)
        secret_key_obj   = Ed25519PrivateKey.from_private_bytes(secret_key_bytes)
        signature        = secret_key_obj.sign(request_string).hex()
    except (ValueError, TypeError) as exc:
        raise CoinSwitchAuthError(f"Invalid Ed25519 key: {exc}") from exc

    return signature, final_endpoint


# ── HTTP Client ───────────────────────────────────────────────────────────────

class CoinSwitchClient:
    """
    Async HTTP client for the CoinSwitch Trading API.

      Base URL : https://coinswitch.co
      Headers  : Content-Type: application/json
                 X-AUTH-APIKEY: <api_key>
                 X-AUTH-SIGNATURE: <ed25519_signature>

    The URL delivered to the server is ALWAYS byte-for-byte identical to the
    URL included in the signature message — we never use httpx's params= kwarg
    (which could percent-encode things differently).

    Usage:
        async with CoinSwitchClient(api_key, secret_hex) as client:
            resp = await client.get("/trade/api/v2/validate/keys")
            resp = await client.post("/trade/api/v2/order", payload={...})
    """

    BASE_URL = "https://coinswitch.co"

    def __init__(
        self,
        api_key:        str  = "",
        secret_key_hex: str  = "",
        simulation:     bool = None,
    ):
        self.api_key        = api_key        or settings.coinswitch_api_key
        self.secret_key_hex = secret_key_hex or settings.coinswitch_api_secret
        self.simulation     = simulation if simulation is not None else settings.simulation_mode
        self._client: Optional[httpx.AsyncClient] = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        return self

    async def __aexit__(self, *args):
        await self.close()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=30.0)
        return self._client

    # ── Auth headers ──────────────────────────────────────────────────────────

    def _build_auth_headers(
        self,
        method:   str,
        endpoint: str,
        params:   dict = None,
        payload:  dict = None,
    ) -> tuple[dict, str]:
        """
        Return (auth_headers_dict, final_endpoint).
        The caller MUST use final_endpoint as the request URL.
        """
        if not self.api_key or not self.secret_key_hex:
            raise CoinSwitchAuthError(
                "API key and Ed25519 secret key are required. "
                "Add them in the Settings page or set "
                "COINSWITCH_API_KEY / COINSWITCH_API_SECRET in .env"
            )

        signature, final_endpoint = generate_signature(
            secret_key_hex = self.secret_key_hex,
            method         = method,
            endpoint       = endpoint,
            params         = params  or {},
            payload        = payload or {},
        )

        return {
            "Content-Type":     "application/json",
            "X-AUTH-APIKEY":    self.api_key,
            "X-AUTH-SIGNATURE": signature,
        }, final_endpoint

    # ── Core dispatcher ───────────────────────────────────────────────────────

    @retry(
        stop  = stop_after_attempt(3),
        wait  = wait_exponential(multiplier=1, min=1, max=10),
        retry = retry_if_exception_type(httpx.TimeoutException),
        reraise = True,
    )
    async def _request(
        self,
        method:        str,
        endpoint:      str,
        params:        dict = None,
        payload:       dict = None,
        authenticated: bool = True,
    ) -> dict:
        """
        Execute one HTTP request with retry-on-timeout.

        GET:      query params are baked into the URL (not passed as httpx params=).
        POST/DELETE: payload is sent as the JSON body.
        In both cases the URL and body sent to the server are identical to what
        was included in the signature message.
        """
        client  = self._get_client()
        params  = params  or {}
        payload = payload or {}

        if authenticated:
            headers, final_endpoint = self._build_auth_headers(
                method, endpoint, params, payload
            )
        else:
            headers = {"Content-Type": "application/json"}
            if method.upper() == "GET" and params:
                sep            = ('&', '?')[urlparse(endpoint).query == '']
                final_endpoint = endpoint + sep + urlencode(params)
            else:
                final_endpoint = endpoint

        # Send the same JSON that was included in the signature
        body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()

        try:
            response = await client.request(
                method  = method.upper(),
                url     = final_endpoint,
                headers = headers,
                content = body,
            )
            logger.debug(f"API {method} {final_endpoint} → {response.status_code}")

            if response.status_code == 429:
                raise CoinSwitchAPIError("Rate limit exceeded — back off and retry", 429)

            try:
                data = response.json()
            except Exception:
                data = {"raw": response.text}

            if response.status_code >= 400:
                msg = data.get("msg") or data.get("message") or "API Error"
                raise CoinSwitchAPIError(msg, response.status_code, data)

            return data

        except httpx.TimeoutException:
            logger.error(f"Timeout: {method} {final_endpoint}")
            raise
        except CoinSwitchAPIError:
            raise
        except Exception as exc:
            logger.error(f"Request failed: {exc}")
            raise

    # ── Public request methods ────────────────────────────────────────────────

    async def get(self, endpoint: str, params: dict = None, authenticated: bool = True) -> dict:
        """GET — params are URL-encoded, appended to endpoint, and included in the signature."""
        return await self._request("GET", endpoint, params=params, authenticated=authenticated)

    async def post(self, endpoint: str, payload: dict = None, authenticated: bool = True) -> dict:
        """POST — payload is JSON-serialised with sort_keys=True and included in the signature."""
        return await self._request("POST", endpoint, payload=payload, authenticated=authenticated)

    async def delete(self, endpoint: str, payload: dict = None, authenticated: bool = True) -> dict:
        """DELETE — payload is JSON-serialised with sort_keys=True and included in the signature."""
        return await self._request("DELETE", endpoint, payload=payload, authenticated=authenticated)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Convenience helpers ───────────────────────────────────────────────────

    async def validate_keys(self) -> dict:
        """Call the official key-validation endpoint."""
        return await self.get("/trade/api/v2/validate/keys")

    async def place_order(
        self,
        symbol:     str,
        side:       str,
        order_type: str,
        quantity:   float,
        price:      float = None,
        exchange:   str   = "c2c1",
    ) -> dict:
        """
        Place a spot order. Mirrors the official POST /trade/api/v2/order sample:

            payload = {"side":"sell","symbol":"BTC/USDT","type":"limit",
                       "price":26000,"quantity":0.0009,"exchange":"c2c1"}
        """
        payload: dict = {
            "side":     side.lower(),
            "symbol":   symbol,
            "type":     order_type.lower(),
            "quantity": quantity,
            "exchange": exchange,
        }
        if price is not None:
            payload["price"] = price
        return await self.post("/trade/api/v2/order", payload=payload)


# ── Module-level singleton ────────────────────────────────────────────────────

_client_instance: Optional[CoinSwitchClient] = None


async def get_api_client(
    api_key:        str = None,
    secret_key_hex: str = None,
) -> CoinSwitchClient:
    """Return (or lazily create) the module-level singleton."""
    global _client_instance
    if _client_instance is None or api_key:
        _client_instance = CoinSwitchClient(
            api_key        = api_key        or "",
            secret_key_hex = secret_key_hex or "",
        )
    return _client_instance
