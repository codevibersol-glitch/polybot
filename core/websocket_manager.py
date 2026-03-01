"""
core/websocket_manager.py
─────────────────────────
Manages two persistent WebSocket connections to the Polymarket CLOB:

  1. Market WS  – wss://ws-subscriptions-clob.polymarket.com/ws/market
     Public, no auth.  Subscribe by sending:
       {"assets_ids": ["<token_id>", …], "type": "market"}

  2. User WS    – wss://ws-subscriptions-clob.polymarket.com/ws/user
     Authenticated via L2 HTTP headers in the WS upgrade request.
     No subscription message needed – server pushes fill events automatically.

Both connections:
  • Reconnect with exponential back-off (2 → 4 → 8 … → 60 s)
  • Back-off resets only if the connection was stable for > 30 s
  • Delay ALWAYS fires after disconnect (not just on exception)
  • Heartbeat ping every 30 s
"""

import base64
import hashlib
import hmac
import json
import threading
import time
from typing import Callable, Optional

from utils.logger import get_logger

log = get_logger(__name__)

_MARKET_WS          = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_USER_WS            = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
_HEARTBEAT_INTERVAL = 30    # seconds between WS pings
_RECONNECT_BASE     = 3     # initial reconnect delay in seconds
_RECONNECT_MAX      = 60    # maximum reconnect delay cap
_STABLE_THRESHOLD   = 30    # seconds: if connected > this, reset back-off on disconnect


class _BaseWsWorker(threading.Thread):
    """
    Daemon thread that maintains a single, always-reconnecting WebSocket.
    Subclasses implement _get_headers(), _subscribe(), _handle_message().
    """

    def __init__(self, url: str, name: str) -> None:
        super().__init__(name=name, daemon=True)
        self._url              = url
        self._ws               = None
        self._stop_event       = threading.Event()
        self._reconnect_delay  = _RECONNECT_BASE

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def stop(self) -> None:
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def run(self) -> None:
        """
        Reconnect loop.  The delay ALWAYS fires after a disconnect so we
        never hammer the server, regardless of whether the disconnect was
        clean or caused by an exception.
        """
        while not self._stop_event.is_set():
            t_start = time.time()
            try:
                self._connect_and_run()
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                log.warning("[%s] WS exception: %s", self.name, exc)

            if self._stop_event.is_set():
                break

            # Reset back-off only when the connection was stable
            if time.time() - t_start > _STABLE_THRESHOLD:
                self._reconnect_delay = _RECONNECT_BASE

            log.debug("[%s] Reconnecting in %ds…", self.name, self._reconnect_delay)
            self._stop_event.wait(timeout=self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, _RECONNECT_MAX)

    def _connect_and_run(self) -> None:
        import websocket as ws_lib

        headers = self._get_headers()   # dict for websocket-client ≥ 1.x

        app = ws_lib.WebSocketApp(
            self._url,
            header=headers,            # passed as HTTP upgrade headers
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws = app
        log.info("[%s] Connecting…", self.name)
        # run_forever blocks until the socket closes
        app.run_forever(ping_interval=_HEARTBEAT_INTERVAL, ping_timeout=10)

    # ── Overrideable hooks ────────────────────────────────────────────────────
    def _get_headers(self) -> dict:
        return {}   # override to add auth headers

    def _subscribe(self, ws) -> None:
        pass        # override to send a subscription message on connect

    def _handle_message(self, msg: dict) -> None:
        pass        # override to process incoming messages

    # ── Callbacks (websocket-client) ──────────────────────────────────────────
    def _on_open(self, ws) -> None:
        log.info("[%s] Connected.", self.name)
        self._subscribe(ws)

    def _on_message(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
            self._handle_message(msg)
        except (json.JSONDecodeError, Exception):
            pass  # ignore malformed frames

    def _on_error(self, ws, error) -> None:
        # Demote to DEBUG – error is re-raised into run() anyway
        log.debug("[%s] WS error event: %s", self.name, error)

    def _on_close(self, ws, code, reason) -> None:
        if code:
            log.info("[%s] WS closed (code=%s reason=%s).", self.name, code, reason)
        else:
            log.debug("[%s] WS closed (server-side).", self.name)

    def send(self, payload: dict) -> None:
        """Thread-safe JSON send.  Silently drops if not connected."""
        if self._ws:
            try:
                self._ws.send(json.dumps(payload))
            except Exception as exc:
                log.debug("[%s] send failed: %s", self.name, exc)


# ── Market WS (public, no auth) ───────────────────────────────────────────────
class MarketWsWorker(_BaseWsWorker):
    """
    Subscribes to real-time price / book events for watched token IDs.
    Subscribe to additional tokens at any time via subscribe_token().
    """

    def __init__(self) -> None:
        super().__init__(_MARKET_WS, "MarketWS")
        self._token_ids: set[str]    = set()
        self._callbacks: list[Callable] = []
        self._ids_lock = threading.Lock()

    def subscribe_token(self, token_id: str) -> None:
        with self._ids_lock:
            if token_id in self._token_ids:
                return
            self._token_ids.add(token_id)
        # Dynamically extend the subscription if already connected
        self.send({"assets_ids": [token_id], "type": "market"})

    def unsubscribe_token(self, token_id: str) -> None:
        with self._ids_lock:
            self._token_ids.discard(token_id)

    def add_callback(self, cb: Callable) -> None:
        self._callbacks.append(cb)

    def _subscribe(self, ws) -> None:
        with self._ids_lock:
            ids = list(self._token_ids)
        if ids:
            ws.send(json.dumps({"assets_ids": ids, "type": "market"}))
            log.debug("[MarketWS] Subscribed to %d tokens.", len(ids))

    def _handle_message(self, msg) -> None:
        items = msg if isinstance(msg, list) else [msg]
        for item in items:
            for cb in self._callbacks:
                try:
                    cb(item)
                except Exception as exc:
                    log.debug("MarketWS callback error: %s", exc)


# ── User WS (L2-authenticated) ────────────────────────────────────────────────
class UserWsWorker(_BaseWsWorker):
    """
    Authenticated WebSocket for fill / order-status events.

    Auth is done entirely via HMAC-SHA256 HTTP headers in the upgrade request.
    No subscription message is needed – the server pushes events for the
    authenticated user automatically once the handshake succeeds.
    """

    def __init__(self) -> None:
        super().__init__(_USER_WS, "UserWS")
        self._api_key:        Optional[str] = None
        self._api_secret:     Optional[str] = None
        self._api_passphrase: Optional[str] = None
        self._fill_callbacks: list[Callable] = []

    def set_credentials(self, api_key: str, api_secret: str, passphrase: str) -> None:
        self._api_key        = api_key
        self._api_secret     = api_secret
        self._api_passphrase = passphrase

    def add_fill_callback(self, cb: Callable) -> None:
        self._fill_callbacks.append(cb)

    def _get_headers(self) -> dict:
        """
        Build the L2 auth headers for the WebSocket upgrade request.
        Returns an empty dict if credentials are not yet set.

        Polymarket signature:
          message   = timestamp + "GET" + "/ws/user"
          signature = base64( HMAC-SHA256( api_secret, message ) )
        """
        if not self._api_key or not self._api_secret:
            log.debug("[UserWS] No credentials – skipping auth headers.")
            return {}
        try:
            ts      = str(int(time.time()))
            message = ts + "GET" + "/ws/user"
            sig     = base64.b64encode(
                hmac.new(
                    self._api_secret.encode(),
                    message.encode(),
                    hashlib.sha256,
                ).digest()
            ).decode()
            return {
                "POLY-API-KEY":    self._api_key,
                "POLY-SIGNATURE":  sig,
                "POLY-TIMESTAMP":  ts,
                "POLY-PASSPHRASE": self._api_passphrase,
            }
        except Exception as exc:
            log.warning("[UserWS] Failed to build auth headers: %s", exc)
            return {}

    # No _subscribe override – auth via headers is sufficient.
    # The server starts streaming fill events immediately after the handshake.

    def _handle_message(self, msg) -> None:
        items = msg if isinstance(msg, list) else [msg]
        for item in items:
            if isinstance(item, dict) and item.get("type") in ("trade", "order", "fill"):
                for cb in self._fill_callbacks:
                    try:
                        cb(item)
                    except Exception as exc:
                        log.debug("UserWS callback error: %s", exc)


# ── Public façade ─────────────────────────────────────────────────────────────
class WebSocketManager:
    """
    Singleton façade: owns both WS workers and exposes a clean API.

    Usage:
        mgr = WebSocketManager.instance()
        mgr.start(api_key, api_secret, passphrase)
        mgr.subscribe_market_token(token_id)
        mgr.on_book_update(callback)
        mgr.on_fill(callback)
        mgr.stop()
    """

    _instance: "WebSocketManager | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._market_ws = MarketWsWorker()
        self._user_ws   = UserWsWorker()
        self._running   = False

    @classmethod
    def instance(cls) -> "WebSocketManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self, api_key: str, api_secret: str, passphrase: str) -> None:
        if self._running:
            return
        self._user_ws.set_credentials(api_key, api_secret, passphrase)
        self._market_ws.start()
        self._user_ws.start()
        self._running = True
        log.info("WebSocket manager started (market + user feeds).")

    def stop(self) -> None:
        self._market_ws.stop()
        self._user_ws.stop()
        self._running = False
        log.info("WebSocket manager stopped.")

    def subscribe_market_token(self, token_id: str) -> None:
        self._market_ws.subscribe_token(token_id)

    def unsubscribe_market_token(self, token_id: str) -> None:
        self._market_ws.unsubscribe_token(token_id)

    def on_book_update(self, callback: Callable) -> None:
        """callback(msg: dict) – called on every market WS message."""
        self._market_ws.add_callback(callback)

    def on_fill(self, callback: Callable) -> None:
        """callback(fill: dict) – called on every user fill event."""
        self._user_ws.add_fill_callback(callback)

    @property
    def running(self) -> bool:
        return self._running
