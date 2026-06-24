# Dikai Carton Printer — Server

FastAPI gateway that fronts Oracle for the STM32 carton-printer fleet.
Implements the spec in `../docs/Dikai_Server_API_Spec.md`.

## Quick start (MOCK mode — no Oracle needed)

```cmd
cd C:\Users\amits\Downloads\Dikai_GUI_final\server
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open Swagger UI at **http://localhost:8000/docs** to explore every
endpoint, or hit it from Postman / curl using the calls below.

## Switch to live Oracle

```cmd
copy .env.example .env
```

Edit `.env`:
```
DIKAI_USE_MOCK_DB=false
DIKAI_DB_HOST=192.168.20.20
DIKAI_DB_PORT=1601
DIKAI_DB_SERVICE=FUNC80
DIKAI_DB_USER=apps
DIKAI_DB_PASSWORD=apps123
DIKAI_ORACLE_INSTANT_CLIENT_PATH=C:/Users/amits/Downloads/Dikai_GUI_final/instantclient_23_0
```

Restart `uvicorn`. The pool will be created at startup. Watch the logs.

## Provisioned devices (for testing)

| device_id        | secret          |
|------------------|-----------------|
| `stm32-test`     | `test`          |
| `stm32-line-A-01`| `dev-secret-1`  |
| `stm32-line-A-02`| `dev-secret-2`  |

Override in `.env` via `DIKAI_DEVICES_RAW`.

## Industrial load-management built in

| Feature | Implementation |
|---|---|
| Oracle connection pool | `oracledb.create_pool(min, max, increment, wait_timeout)` — sized in `.env` |
| Pool wait timeout | Returns **HTTP 503** instead of hanging the device under overload |
| Statement timeout | `conn.call_timeout` per borrowed connection |
| Per-device rate limit | Token-bucket: 20 req/s, burst 40 (configurable) — returns **HTTP 429** |
| Idempotency-Key | LRU+TTL cache, 24 h — safe retries on `POST /cartons` and `POST /lpn/next` |
| Atomic LPN counter | `MERGE`-based UPSERT on `DIKAI_LPN_COUNTER` table + Python lock |
| Atomic CARTON_ID | Python lock around `MAX+1` + INSERT pair |
| Health/pool stats | `GET /health` exposes `opened / busy / max / min` |
| Graceful shutdown | Pool closed in `lifespan` exit |
| Structured logs | Every request: `METHOD PATH -> STATUS (ms)` |

## File layout

```
server/
├─ requirements.txt
├─ .env.example
├─ README.md
└─ app/
   ├─ __init__.py
   ├─ config.py        # Pydantic Settings
   ├─ db.py            # Oracle pool + mock store + health
   ├─ device_registry.py # Device config + heartbeat state
   ├─ lpn.py           # Atomic LPN counter
   ├─ security.py      # Auth + rate limit + idempotency
   ├─ models.py        # Pydantic request/response shapes
   ├─ queries.py       # Documented SQL specs
   ├─ repository.py    # Query execution (mock / live dispatch)
   ├─ routes.py        # FastAPI route definitions + API comments
   └─ main.py          # FastAPI app bootstrap
```
