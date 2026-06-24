"""Smoke-test the server API surface through HTTP.

Runs in server mock mode so it validates routes, auth, idempotency, LPN,
carton CRUD, batch reads, device config, and heartbeat without touching
the live Oracle database.
"""
from __future__ import annotations

import os
import sys
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DIKAI_USE_MOCK_DB"] = "true"
os.environ.setdefault("DIKAI_DEVICES_RAW", "stm32-test:test")


class Response:
    """Small response wrapper for the standard-library HTTP smoke client."""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text) if self.text else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base_url: str, method: str, path: str, *, headers=None, json_body=None, params=None) -> Response:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    body = None
    request_headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(
        f"{base_url}{path}{query}",
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("utf-8")
            return Response(resp.status, text)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        return Response(exc.code, text)


def main() -> int:
    import uvicorn

    from server.app.main import app

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        for _ in range(50):
            try:
                health = _request(base_url, "GET", "/api/v1/health")
                if health.status_code == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("server did not start in time")

        health = _request(base_url, "GET", "/api/v1/health")
        assert health.status_code == 200, health.text
        assert health.json()["ok"] is True, health.text

        login = _request(
            base_url,
            "POST",
            "/api/v1/auth/login",
            json_body={"device_id": "stm32-test", "secret": "test"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        refresh = _request(base_url, "POST", "/api/v1/auth/refresh", headers=headers)
        assert refresh.status_code == 200, refresh.text
        headers = {"Authorization": f"Bearer {refresh.json()['token']}"}

        brands = _request(base_url, "GET", "/api/v1/brands", headers=headers)
        assert brands.status_code == 200, brands.text
        assert brands.json(), "brand list should not be empty"

        items = _request(base_url, "GET", "/api/v1/items", params={"org_id": "481"}, headers=headers)
        assert items.status_code == 200, items.text

        sample = _request(base_url, "GET", "/api/v1/sample-config", params={"size_code": "60X60"}, headers=headers)
        assert sample.status_code == 200, sample.text
        assert sample.json()["normal_pcs"] == 4

        uom = _request(
            base_url,
            "GET",
            "/api/v1/uom-pcs-per-ctn",
            params={"org_id": "481", "item_code": "MNL60601"},
            headers=headers,
        )
        assert uom.status_code == 200, uom.text
        assert uom.json()["pcs_per_ctn"] == 4.0

        peek = _request(base_url, "GET", "/api/v1/lpn/peek", headers=headers)
        assert peek.status_code == 200, peek.text
        nxt = _request(
            base_url,
            "POST",
            "/api/v1/lpn/next",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert nxt.status_code == 200, nxt.text
        lpn_code = nxt.json()["lpn_id"]

        carton = {
            "CARTON_CODE": lpn_code,
            "QR_CODE": "MNL60601|LOT-A|M|23 Jun 26 08:00 AM|1",
            "ORGANIZATION_ID": "481",
            "PLANT_CODE": "089",
            "ITEM_CODE": "MNL60601",
            "ITEM_DESC": "",
            "LOT_NUMBER": "LOT-A",
            "CARTON_QTY": 1.0,
            "UOM_CODE": "CTN",
            "LPN_ID": nxt.json()["lpn_num"],
            "LPN_CONTEXT": "CARTON",
            "BATCH_NO": "23JUN26MNL60601M",
            "BATCH_DATE": "2026-06-23T08:00:00",
            "LOT_NO": "LOT-A",
            "BRAND": "Monalisa",
            "GRADE": "A",
            "SHIFT": "M",
            "BATCH_TIME": "08:00 AM",
            "STATUS": "PRINTED",
            "SIZE_CODE": "60X60",
            "GRADE_CODE": "A",
            "NO_PCS": 4,
            "CTN_TYPE": "Regular",
            "TOTAL_PLANNED_QTY": 500,
            "CREATED_BY": "smoke-test",
        }
        inserted = _request(
            base_url,
            "POST",
            "/api/v1/cartons",
            json_body=carton,
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert inserted.status_code == 200, inserted.text
        assert inserted.json()["ok"] is True

        listed = _request(base_url, "GET", "/api/v1/cartons", params={"brand": "Monalisa"}, headers=headers)
        assert listed.status_code == 200, listed.text
        assert any(r["carton_code"] == lpn_code for r in listed.json())

        single = _request(base_url, "GET", f"/api/v1/cartons/{lpn_code}", headers=headers)
        assert single.status_code == 200, single.text
        assert single.json()["carton_code"] == lpn_code

        count = _request(base_url, "GET", "/api/v1/cartons/count", params={"scope": "total"}, headers=headers)
        assert count.status_code == 200, count.text
        assert isinstance(count.json()["count"], int)

        reprint = _request(
            base_url,
            "POST",
            f"/api/v1/cartons/{lpn_code}/reprint",
            json_body={"by_user": "smoke-test"},
            headers=headers,
        )
        assert reprint.status_code == 200, reprint.text
        assert reprint.json()["new_status"] == "PRINTED-RP1"

        batch_post = _request(base_url, "POST", "/api/v1/batches", json_body=carton, headers=headers)
        assert batch_post.status_code == 200, batch_post.text
        batches = _request(base_url, "GET", "/api/v1/batches", params={"brand": "Monalisa"}, headers=headers)
        assert batches.status_code == 200, batches.text
        status = _request(
            base_url,
            "GET",
            "/api/v1/batches/status",
            params={"batch_no": carton["BATCH_NO"], "org_id": "481"},
            headers=headers,
        )
        assert status.status_code == 200, status.text
        assert status.json()["exists"] is True

        device_cfg = _request(base_url, "GET", "/api/v1/device/stm32-test/config", headers=headers)
        assert device_cfg.status_code == 200, device_cfg.text
        patched = _request(
            base_url,
            "PATCH",
            "/api/v1/device/stm32-test/config",
            json_body={"config": {"qr": {"max_dots": 28}}},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["config"]["qr"]["max_dots"] == 28

        heartbeat = _request(
            base_url,
            "POST",
            "/api/v1/device/stm32-test/heartbeat",
            json_body={"state": "ready", "fw_version": "test", "ip": "127.0.0.1"},
            headers=headers,
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["ok"] is True

        deleted = _request(
            base_url,
            "DELETE",
            f"/api/v1/cartons/{lpn_code}",
            json_body={"by_user": "smoke-test"},
            headers=headers,
        )
        assert deleted.status_code == 200, deleted.text

    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print("SERVER API SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
