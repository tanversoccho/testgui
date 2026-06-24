"""Auth + per-device rate limiting + idempotency cache.

These are the three pieces of "industrial load management" that protect
Oracle from the device fleet:

  1. Bearer token auth — every endpoint except /health and /auth/login.
  2. Token-bucket rate limit per device — caps a misbehaving device.
  3. Idempotency-Key cache — safe retries from devices that lost a reply.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, Header, HTTPException

from .config import settings


# ============================================================
# Bearer tokens (in-memory, TTL-based)
# ============================================================
class _TokenStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_token: Dict[str, Dict[str, Any]] = {}

    def issue(self, device_id: str) -> Tuple[str, int]:
        tok = secrets.token_urlsafe(32)
        exp = int(time.time()) + settings.AUTH_TOKEN_TTL_SECONDS
        with self._lock:
            self._by_token[tok] = {"device_id": device_id, "exp": exp}
        return tok, exp

    def lookup(self, tok: str) -> Optional[str]:
        with self._lock:
            entry = self._by_token.get(tok)
            if not entry:
                return None
            if entry["exp"] < int(time.time()):
                self._by_token.pop(tok, None)
                return None
            return entry["device_id"]


tokens = _TokenStore()


def authenticate_device(device_id: str, secret: str) -> Optional[Tuple[str, int]]:
    expected = settings.devices.get(device_id)
    if expected is None:
        return None
    if not secrets.compare_digest(expected, secret):
        return None
    return tokens.issue(device_id)


def refresh_device_token(device_id: str) -> Tuple[str, int]:
    """Issue a fresh bearer token for an already-authenticated device."""
    return tokens.issue(device_id)


def require_token(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    tok = authorization.split(" ", 1)[1].strip()
    device_id = tokens.lookup(tok)
    if not device_id:
        raise HTTPException(401, "Invalid or expired token")
    return device_id


# ============================================================
# Rate limiter — per-device token bucket
# ============================================================
class _RateLimiter:
    def __init__(self, rate_per_s: float, burst: int) -> None:
        self._rate = rate_per_s
        self._burst = float(burst)
        self._lock = threading.Lock()
        self._state: Dict[str, Tuple[float, float]] = {}

    def check(self, device_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            avail, last = self._state.get(device_id, (self._burst, now))
            avail = min(self._burst, avail + (now - last) * self._rate)
            if avail < 1.0:
                self._state[device_id] = (avail, now)
                return False
            self._state[device_id] = (avail - 1.0, now)
            return True


rate_limiter = _RateLimiter(settings.RATE_LIMIT_PER_SECOND, settings.RATE_LIMIT_BURST)


def enforce_rate_limit(device_id: str = Depends(require_token)) -> str:
    if not rate_limiter.check(device_id):
        raise HTTPException(429, "Rate limit exceeded — back off and retry")
    return device_id


# ============================================================
# Idempotency-Key cache (LRU with TTL)
# ============================================================
class _IdempotencyCache:
    def __init__(self, ttl: int, max_entries: int) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._lock = threading.Lock()
        self._store: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()

    def get(self, key: Optional[str]) -> Optional[Any]:
        if not key:
            return None
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            ts, value = entry
            if time.time() - ts > self._ttl:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    def put(self, key: Optional[str], value: Any) -> None:
        if not key:
            return
        with self._lock:
            self._store[key] = (time.time(), value)
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)


idempotency = _IdempotencyCache(
    settings.IDEMPOTENCY_TTL_SECONDS, settings.IDEMPOTENCY_MAX_ENTRIES
)


def idempotency_key_header(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Optional[str]:
    return idempotency_key
