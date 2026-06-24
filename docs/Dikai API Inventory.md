---
title: Dikai API Inventory
tags:
  - dikai
  - api
  - server
  - oracle
updated: 2026-06-24
---

# Dikai API Inventory

Source files:
- API routes: `server/app/routes.py`
- Query catalog: `server/app/queries.py`
- Oracle execution: `server/app/repository.py`
- LPN counter execution: `server/app/lpn.py`

This note lists every API path, the query or store action it uses, the purpose, expected input, and output.

## API Endpoint Map

| API path | Query / action | Purpose | Input | Output |
|---|---|---|---|---|
| `POST /api/v1/auth/login` | No SQL. Uses configured device secrets from `DIKAI_DEVICES_RAW`. | Exchange a provisioned device secret for an API bearer token. | JSON body `{device_id, secret}`. | `{token, expires_at}`. |
| `POST /api/v1/auth/refresh` | No SQL. Uses JWT/security service. | Refresh a still-valid bearer token. | Header `Authorization: Bearer <token>`. | Fresh `{token, expires_at}`. |
| `GET /api/v1/health` | `HEALTH_SELECT` in Oracle mode; mock health in mock mode. | Check server and database/pool health. | No token and no body. | `{ok, mode, server_time, db}`. |
| `GET /api/v1/brands` | No SQL. Reads `settings.brands`. | Return brand, plant/org code, and inventory org mappings for the GUI. | Bearer token. | List of `{brand, org_code, inv_code}`. |
| `GET /api/v1/items` | `FETCH_ITEMS`. | Load selectable item codes and tile sizes for a selected organization. | Query `org_id`, for example `481`. | List of `{code, size, desc}`. |
| `GET /api/v1/sample-config` | `FETCH_SAMPLE_CONFIG`. | Load sample carton defaults for a tile size. | Query `size_code`, for example `60X60`. | `{normal_pcs, sample_pcs, conversion}`. |
| `GET /api/v1/uom-pcs-per-ctn` | `FETCH_UOM_PCS_PER_CTN`. | Load live PCS-per-CTN conversion for an item. | Query `org_id`, `item_code`. | `{pcs_per_ctn}`. |
| `GET /api/v1/lpn/peek` | `READ_LPN_COUNTER`. Startup may run `CREATE_LPN_COUNTER_TABLE`. | Preview the next server-owned LPN without consuming it. | Bearer token. | `{lpn_id, lpn_num}`. |
| `POST /api/v1/lpn/next` | `UPSERT_INCREMENT_LPN_COUNTER`, then `READ_LPN_COUNTER`. | Atomically consume the next daily LPN. | Bearer token; optional `Idempotency-Key`. | `{lpn_id, lpn_num}`. |
| `POST /api/v1/cartons` | `NEXT_CARTON_ID`, `INSERT_CARTON`. | Persist one printed carton row. | `CartonRow` JSON body; optional `Idempotency-Key`. | `{ok, carton_id, carton_code, created_at}`. |
| `DELETE /api/v1/cartons/{carton_code}` | `SOFT_DELETE_CARTON`. | Soft-delete a carton while preserving audit history. | Path `carton_code`; JSON body `{by_user}`. | `{ok: true}` or error. |
| `POST /api/v1/cartons/{carton_code}/reprint` | `MARK_REPRINT`, `SELECT_REPRINT_STATUS`. | Mark an operator reprint by bumping `STATUS` to `PRINTED-RPn`. | Path `carton_code`; JSON body `{by_user}`. | `{ok: true, new_status}`. |
| `GET /api/v1/cartons/count` | Printer TCP `Request Status` (`S`) and `PCUM(10)` parsing. | Read printer-owned TODAY/ALL print counts for the dashboard. | Query `scope=today` or `scope=total`. | `{count, scope}`. |
| `GET /api/v1/cartons/{carton_code}` | `GET_CARTON` using `CARTON_SELECT_COLUMNS`. | Fetch one carton row for inspection, delete confirmation, or reprint loading. | Path `carton_code`. | Full carton row JSON. |
| `GET /api/v1/cartons` | `QUERY_CARTONS` using `CARTON_SELECT_COLUMNS`; `COUNT_QUERY_CARTONS` when `include_total=true`. | Search carton history with GUI filters. | Optional query filters: `date_from`, `date_to`, `brand`, `item_code_like`, `lot_like`, `shift`, `grade`, `status`, `lpn_like`, `include_deleted`, `offset`, `limit`, `include_total`. | Newest matching carton rows, or `{rows,total}` when requested. |
| `GET /api/v1/batches/status` | `BATCH_STATUS`. | Check whether a batch exists and whether it is finalized. | Query `batch_no`; optional `org_id`. | `{exists, finalized}`. |
| `GET /api/v1/batches` | `QUERY_BATCHES`; `COUNT_QUERY_BATCHES` when `include_total=true`. | Search batch summary rows. | Optional query filters: `date_from`, `date_to`, `brand`, `shift`, `grade`, `status`, `item_code_like`, `lot_like`, `batch_no_like`, `offset`, `limit`, `include_total`. | Newest matching batch rows, or `{rows,total}` when requested. |
| `POST /api/v1/batches` | `INSERT_INITIAL_BATCH`, `SELECT_LATEST_BATCH_ID`. | Fallback insert of an initial `STATUS='N'` batch row when trigger-managed batch master is unavailable. | Carton-like JSON body. | `{ok, batch_id}`. |
| `POST /api/v1/batches/{batch_no}/finalize` | `FINALIZE_BATCH`. | Fallback aggregate finalize into batch master. | Path `batch_no`; JSON body `{org_id}`. | `{ok, rows_inserted}`. |
| `GET /api/v1/device/{device_id}/config` | No SQL. Uses in-memory `device_registry`. | Let a device pull server-owned printer, QR, and message config. | Path `device_id`; matching bearer token. | `{device_id, config, updated_at}`. |
| `PATCH /api/v1/device/{device_id}/config` | No SQL. Uses in-memory `device_registry`. | Update server-owned config for a device. | Path `device_id`; JSON body `{config: {...partial sections...}}`; matching bearer token. | Merged `{device_id, config, updated_at}`. |
| `POST /api/v1/device/{device_id}/heartbeat` | No SQL. Uses in-memory `device_registry`. | Record liveness for fleet/load-balancer monitoring. | Path `device_id`; JSON body `{state, fw_version, ip}`; matching bearer token. | `{ok, device_id, server_time}`. |

## Full API Examples With Selected Values

Selected values used in these examples:

| Variable | Selected value |
|---|---|
| `base_url` | `http://localhost:8000` |
| `device_id` | `stm32-test` |
| `secret` | `test` |
| `token` | `{{token}}` from `POST /api/v1/auth/login` |
| `brand` | `Monalisa` |
| `org_id` / `inv_code` | `481` |
| `plant_code` / `org_code` | `089` |
| `item_code` | `MCPGVT6301` |
| `size_code` | `60X60` |
| `carton_code` | `LPN-C-260624000001` |
| `batch_no` | `24JUN26MCPGVT6301M` |
| URL-encoded `batch_no` | `24JUN26MCPGVT6301M` |

Use Postman Authorization type `Bearer Token` and paste only the token value, or set header `Authorization: Bearer {{token}}`.

| # | API example | Auth | Body / notes |
|---|---|---|---|
| 1 | `POST http://localhost:8000/api/v1/auth/login` | None | Body: `{"device_id":"stm32-test","secret":"test"}`. Save returned `token`. |
| 2 | `POST http://localhost:8000/api/v1/auth/refresh` | Bearer `{{token}}` | No body. Returns fresh token. |
| 3 | `GET http://localhost:8000/api/v1/health` | None | No body. Checks server and Oracle pool. |
| 4 | `GET http://localhost:8000/api/v1/brands` | Bearer `{{token}}` | No body. Returns Monalisa, X Monica, Alexander, X Tiles, Venus. |
| 5 | `GET http://localhost:8000/api/v1/items?org_id=481` | Bearer `{{token}}` | No body. Returns Monalisa item rows. |
| 6 | `GET http://localhost:8000/api/v1/sample-config?size_code=60X60` | Bearer `{{token}}` | No body. Returns sample and normal carton defaults. |
| 7 | `GET http://localhost:8000/api/v1/uom-pcs-per-ctn?org_id=481&item_code=MCPGVT6301` | Bearer `{{token}}` | No body. Live example returned `{"pcs_per_ctn":4.0}`. |
| 8 | `GET http://localhost:8000/api/v1/lpn/peek` | Bearer `{{token}}` | No body. Previews next LPN without consuming. |
| 9 | `POST http://localhost:8000/api/v1/lpn/next` | Bearer `{{token}}` | Optional header `Idempotency-Key: <uuid>`. Consumes next LPN. |
| 10 | `POST http://localhost:8000/api/v1/cartons` | Bearer `{{token}}` | Body: use the `Example CartonRow Body` below. Optional `Idempotency-Key`. |
| 11 | `DELETE http://localhost:8000/api/v1/cartons/LPN-C-260624000001` | Bearer `{{token}}` | Body: `{"by_user":"stm32-test"}`. Soft-deletes the carton. |
| 12 | `POST http://localhost:8000/api/v1/cartons/LPN-C-260624000001/reprint` | Bearer `{{token}}` | Body: `{"by_user":"stm32-test"}`. Returns `PRINTED-RP1`, `PRINTED-RP2`, etc. |
| 13 | `GET http://localhost:8000/api/v1/cartons/count?scope=total` | Bearer `{{token}}` | No body. Reads printer TCP `PCUM` cumulative count. |
| 14 | `GET http://localhost:8000/api/v1/cartons/count?scope=today` | Bearer `{{token}}` | No body. Reads printer TCP `PCUM` minus today's baseline. |
| 15 | `GET http://localhost:8000/api/v1/cartons/LPN-C-260624000001` | Bearer `{{token}}` | No body. Loads one carton by code. |
| 16 | `GET http://localhost:8000/api/v1/cartons?brand=Monalisa&item_code_like=MCPGVT6301&lot_like=LOT-API&shift=M&grade=A&status=PRINTED&lpn_like=LPN-C-&include_deleted=false&offset=0&limit=30&include_total=true` | Bearer `{{token}}` | No body. Searches carton history with selected filters and returns `{rows,total}`. |
| 17 | `GET http://localhost:8000/api/v1/batches/status?batch_no=24JUN26MCPGVT6301M&org_id=481` | Bearer `{{token}}` | No body. Checks selected batch status. |
| 18 | `GET http://localhost:8000/api/v1/batches?brand=Monalisa&shift=M&grade=A&status=Y&item_code_like=MCPGVT6301&lot_like=LOT-API&batch_no_like=MCPGVT6301&offset=0&limit=30&include_total=true` | Bearer `{{token}}` | No body. Searches batch summary rows and returns `{rows,total}`. |
| 19 | `POST http://localhost:8000/api/v1/batches` | Bearer `{{token}}` | Body: use the `Example CartonRow Body` below. Fallback only when DB trigger is not managing batch master. |
| 20 | `POST http://localhost:8000/api/v1/batches/24JUN26MCPGVT6301M/finalize` | Bearer `{{token}}` | Body: `{"org_id":"481"}`. Fallback aggregate finalize. |
| 21 | `GET http://localhost:8000/api/v1/device/stm32-test/config` | Bearer `{{token}}` | No body. Gets server-owned device config. |
| 22 | `PATCH http://localhost:8000/api/v1/device/stm32-test/config` | Bearer `{{token}}` | Body: `{"config":{"qr":{"max_dots":28}}}`. Merges device config. |
| 23 | `POST http://localhost:8000/api/v1/device/stm32-test/heartbeat` | Bearer `{{token}}` | Body: `{"state":"ready","fw_version":"test","ip":"127.0.0.1"}`. Records liveness. |

> Note: There are 22 route definitions, but count is shown with both selected scopes (`total` and `today`) as separate practical Postman examples.

### Example CartonRow Body

Use this body for `POST /api/v1/cartons`. Use the same shape for fallback `POST /api/v1/batches`.

```json
{
  "CARTON_CODE": "LPN-C-260624000001",
  "QR_CODE": "MCPGVT6301|LOT-API|M|24 Jun 26 08:00 AM|1",
  "ORGANIZATION_ID": "481",
  "PLANT_CODE": "089",
  "INVENTORY_ITEM_ID": "",
  "ITEM_CODE": "MCPGVT6301",
  "ITEM_DESC": "",
  "LOT_NUMBER": "LOT-API",
  "CARTON_QTY": 1.0,
  "UOM_CODE": "CTN",
  "LPN_ID": 260624000001,
  "LPN_CONTEXT": "CARTON",
  "BATCH_NO": "24JUN26MCPGVT6301M",
  "BATCH_DATE": "2026-06-24T08:00:00",
  "LOT_NO": "LOT-API",
  "BRAND": "Monalisa",
  "GRADE": "A",
  "SHIFT": "M",
  "BATCH_TIME": "08:00 AM",
  "STATUS": "PRINTED",
  "SIZE_CODE": "60X60",
  "GRADE_CODE": "A",
  "NO_PCS": 4,
  "CTN_TYPE": "Regular",
  "TOTAL_PLANNED_QTY": 100,
  "CREATED_BY": "stm32-test"
}
```

## Query Catalog

| Query | SQL / action shape | Purpose | Input | Output |
|---|---|---|---|---|
| `HEALTH_SELECT` | `SELECT 1 FROM DUAL` | Verify that a borrowed Oracle connection can execute SQL. | No binds. | One row containing `1`. |
| `FETCH_ITEMS` | Select distinct `Item_Code`, `CAT5` from `APPS.XXFG_ORG_ITEMS`. | Load item codes and tile sizes for a selected brand/org. | `:oid = ORGANIZATION_ID`. | Rows mapped to `{code, size}`. |
| `FETCH_SAMPLE_CONFIG` | Select `NORMAL_PCS_CTN`, `SAMPLE_PCS_CTN`, `CONVERSION_CTN` from `APPS.XXFG_SAMPLE_CARTON_CONFIG`. | Load sample-carton sizing defaults for a tile size. | `:s = SIZE_CODE`. | `{normal_pcs, sample_pcs, conversion}`. |
| `FETCH_UOM_PCS_PER_CTN` | Select active `CONVERSION_RATE` from `APPS.XXFG_UOM_CONVERSIONS_V`. | Find active PCS-per-CTN conversion for selected item. | `:org = ORGANIZATION_ID`, `:code = ITEM_CODE`. | One conversion value. |
| `NEXT_CARTON_ID` | `SELECT NVL(MAX(CARTON_ID),0)+1 FROM {carton_table}`. | Allocate the next legacy carton id. | Trusted `carton_table` setting. | One numeric `CARTON_ID`. |
| `INSERT_CARTON` | Insert dynamic trusted column list into `{carton_table}`. | Persist one printed carton row. | Carton column binds plus allocated `CARTON_ID`. | Committed carton row. |
| `SOFT_DELETE_CARTON` | Update `{carton_table}` setting `STATUS='DELETED'`. | Soft-delete one carton for audit-safe removal. | `:cc = CARTON_CODE`, `:u = user/device`. | Updated row or rowcount `0`. |
| `MARK_REPRINT` | Update `{carton_table}` to append or increment `-RPn` suffix. | Track explicit operator reprints. | `:cc = CARTON_CODE`, `:u = user/device`. | Updated row status. |
| `SELECT_REPRINT_STATUS` | Select `STATUS` from `{carton_table}` by carton code. | Read the status after reprint update. | `:cc = CARTON_CODE`. | Status string, for example `PRINTED-RP1`. |
| `CARTON_SELECT_COLUMNS` | Shared carton select column list. | Keep single-carton and carton-list output aligned. | Used by `GET_CARTON` and `QUERY_CARTONS`. | Consistent carton JSON fields. |
| `GET_CARTON` | `CARTON_SELECT_COLUMNS FROM {carton_table} WHERE CARTON_CODE=:cc`. | Look up one carton. | `:cc = CARTON_CODE`. | Full carton read model. |
| `QUERY_CARTONS` | `CARTON_SELECT_COLUMNS FROM {carton_table} {where_sql} ORDER BY CREATION_DATE DESC FETCH FIRST :lim ROWS ONLY`. | List carton history using GUI filters. | Optional date, brand, shift, grade, status, item, lot, LPN, deleted flag, and limit binds. | Newest matching carton rows. |
| `COUNT_TODAY` | Count rows where `TRUNC(CREATION_DATE)=TRUNC(SYSDATE)` and status is not deleted. | Legacy Oracle carton-row count helper; not used by `/cartons/count`. | No binds. | Integer count. |
| `COUNT_TOTAL` | Count rows where status is not deleted. | Legacy Oracle carton-row count helper; not used by `/cartons/count`. | No binds. | Integer count. |
| `QUERY_BATCHES` | Select batch columns from `{batch_table}` with filters and newest-first limit. | List batch summary rows. | Optional date, brand, shift, grade, status, item, lot, batch, and limit binds. | Newest matching batch rows. |
| `BATCH_STATUS` | Aggregate `MAX(STATUS='Y')` and `COUNT(*)` from `{batch_table}`. | Check whether a batch exists and is finalized. | `:bn = BATCH_NO`, `:org = ORGANIZATION_ID` or empty. | `exists` and `finalized` booleans. |
| `INSERT_INITIAL_BATCH` | Insert `STATUS='N'` batch row using `{batch_seq}.NEXTVAL`. | Fallback initial batch-master row when trigger is unavailable. | Carton-derived binds such as org, batch no, item, brand, planned qty. | Inserted batch row. |
| `SELECT_LATEST_BATCH_ID` | Select newest `BATCH_ID` by `BATCH_NO`. | Return id after fallback batch insert. | `:bn = BATCH_NO`. | One `BATCH_ID`. |
| `FINALIZE_BATCH` | Insert aggregate `STATUS='Y'` summary rows from carton master into batch master. | Fallback finalize when trigger-managed batch master is unavailable. | `:bn = BATCH_NO`, `:org = ORGANIZATION_ID` or empty. | Inserted finalized summary rows. |
| `CREATE_LPN_COUNTER_TABLE` | PL/SQL block creating `{counter_table}` if missing. | Ensure server-owned daily LPN counter table exists. | Trusted `counter_table` setting. | Table with `COUNTER_DATE` primary key and `COUNTER_VALUE`. |
| `READ_LPN_COUNTER` | Select `COUNTER_VALUE` from `{counter_table}` by date. | Read today's LPN counter without incrementing. | `:d = YYYY-MM-DD`. | Current counter or no row. |
| `UPSERT_INCREMENT_LPN_COUNTER` | `MERGE INTO {counter_table}` to insert or increment today's row. | Atomically consume next daily LPN number. | `:d = YYYY-MM-DD`. | Counter row incremented; repository reads value afterward. |

## Notes

- There are 22 live API endpoints under `/api/v1`.
- Executable SQL is centralized in `server/app/queries.py`.
- Route functions contain API docs and validation only.
- Device config and heartbeat are currently in-memory server state, not Oracle-backed.
- Dashboard TODAY/ALL counts in the GUI are printer-counter based, not carton-table based.
