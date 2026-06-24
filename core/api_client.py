"""HTTP client for the Dikai server API.

This keeps the desktop GUI from opening Oracle directly when
config_app.USE_SERVER_API is enabled. The rest of the GUI continues to
call core.database / core.lpn_generator; those facades delegate here.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime
from typing import Any, Dict, Optional
from urllib import error, parse, request

from config import config_app


class ApiError(RuntimeError):
    """Raised when the server API cannot satisfy a request."""


_TOKEN: Optional[str] = None
_TOKEN_EXPIRES_AT: int = 0


def enabled() -> bool:
    return bool(getattr(config_app, "USE_SERVER_API", False))


def _base_url() -> str:
    return str(getattr(config_app, "SERVER_API_BASE_URL", "")).rstrip("/")


def _timeout() -> float:
    try:
        return float(getattr(config_app, "SERVER_API_TIMEOUT", 5.0))
    except (TypeError, ValueError):
        return 5.0


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def reset() -> None:
    global _TOKEN, _TOKEN_EXPIRES_AT
    _TOKEN = None
    _TOKEN_EXPIRES_AT = 0


def _decode_response(resp) -> Any:
    raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _open(method: str, path: str, body: Any = None, *,
          token: Optional[str] = None,
          idempotency_key: Optional[str] = None) -> Any:
    if not _base_url():
        raise ApiError("Server API base URL is not configured.")
    url = _base_url() + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, default=_json_default).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=_timeout()) as resp:
            return _decode_response(resp)
    except error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            msg = payload.get("message") or payload
        except Exception:
            msg = e.reason
        raise ApiError(f"HTTP {e.code}: {msg}") from e
    except error.URLError as e:
        raise ApiError(f"Server API unreachable: {e.reason}") from e
    except TimeoutError as e:
        raise ApiError("Server API request timed out.") from e


def _ensure_token() -> str:
    global _TOKEN, _TOKEN_EXPIRES_AT
    now = int(time.time())
    if _TOKEN and _TOKEN_EXPIRES_AT - now > 60:
        return _TOKEN
    payload = {
        "device_id": getattr(config_app, "SERVER_DEVICE_ID", "stm32-test"),
        "secret": getattr(config_app, "SERVER_DEVICE_SECRET", "test"),
    }
    res = _open("POST", "/auth/login", payload)
    _TOKEN = str(res["token"])
    _TOKEN_EXPIRES_AT = int(res["expires_at"])
    return _TOKEN


def call(method: str, path: str, body: Any = None, *,
         params: Optional[Dict[str, Any]] = None,
         idempotent: bool = False) -> Any:
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            path = path + "?" + parse.urlencode(clean, doseq=True)
    token = _ensure_token()
    idem_key = str(uuid.uuid4()) if idempotent else None
    try:
        return _open(method, path, body, token=token, idempotency_key=idem_key)
    except ApiError as e:
        if "HTTP 401" not in str(e):
            raise
        reset()
        token = _ensure_token()
        return _open(method, path, body, token=token, idempotency_key=idem_key)


def health() -> Any:
    return _open("GET", "/health")
