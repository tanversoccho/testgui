"""HTTP routes for the Dikai server.

Route functions contain API documentation and validation only. They call
repository/device/security services for the real work; SQL lives in
queries.py and Oracle execution lives in repository.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from . import db, device_registry, lpn, printer_counter, repository, security
from .config import settings
from .models import (
    BatchInsertResponse, BatchStatusResponse, BrandRow, CartonRow,
    CountResponse, DeviceConfigPatchRequest, DeviceConfigResponse,
    FinalizeBatchRequest, FinalizeBatchResponse, HealthResponse,
    HeartbeatRequest, HeartbeatResponse, InsertCartonResponse, ItemRow,
    LoginRequest, LoginResponse, LpnResponse, RefreshResponse,
    ReprintRequest, ReprintResponse, SampleConfigRow, SimpleOkResponse,
    SoftDeleteRequest, UomPcsRow,
)


router = APIRouter(prefix="/api/v1")


@router.post("/auth/login", response_model=LoginResponse)
def auth_login(req: LoginRequest):
    """Purpose: exchange a provisioned device secret for a bearer token.

    Expects: JSON `{device_id, secret}`.
    Output: `{token, expires_at}` where `expires_at` is a Unix timestamp.
    Example: `POST /api/v1/auth/login` with `{"device_id":"stm32-test","secret":"test"}`.
    """
    res = security.authenticate_device(req.device_id, req.secret)
    if not res:
        raise HTTPException(401, "Invalid device_id or secret")
    tok, exp = res
    return LoginResponse(token=tok, expires_at=exp)


@router.post("/auth/refresh", response_model=RefreshResponse)
def auth_refresh(device_id: str = Depends(security.require_token)):
    """Purpose: refresh a valid bearer token before expiry.

    Expects: `Authorization: Bearer <token>`.
    Output: a fresh `{token, expires_at}` pair.
    Example: `POST /api/v1/auth/refresh` from a logged-in device.
    """
    tok, exp = security.refresh_device_token(device_id)
    return RefreshResponse(token=tok, expires_at=exp)


@router.get("/health", response_model=HealthResponse)
def get_health():
    """Purpose: verify server and Oracle pool health.

    Expects: no input or token.
    Output: `{ok, mode, server_time, db}` including pool stats in live mode.
    Example: `GET /api/v1/health`.
    """
    h = db.health()
    return HealthResponse(
        ok=bool(h.get("db_alive")),
        mode=h.get("mode", "unknown"),
        server_time=datetime.utcnow().isoformat() + "Z",
        db=h,
    )


@router.get("/brands", response_model=List[BrandRow])
def list_brands(_: str = Depends(security.enforce_rate_limit)):
    """Purpose: return configured brand/org mappings for the form.

    Expects: bearer token.
    Output: list of `{brand, org_code, inv_code}`.
    Example: `GET /api/v1/brands`.
    """
    return settings.brands


@router.get("/items", response_model=List[ItemRow])
def list_items(
    org_id: int = Query(..., description="Numeric ORGANIZATION_ID, e.g. 481 for Monalisa"),
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: list item codes and tile sizes for a brand/org.

    Expects: query `org_id`.
    Output: list of `{code, size, desc}` rows.
    Example: `GET /api/v1/items?org_id=481`.
    """
    return repository.fetch_items(org_id)


@router.get("/sample-config", response_model=SampleConfigRow)
def get_sample_config(
    size_code: str = Query(..., description="Tile size, e.g. 60X60"),
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: fetch sample carton defaults for the selected tile size.

    Expects: query `size_code`.
    Output: `{normal_pcs, sample_pcs, conversion}`.
    Example: `GET /api/v1/sample-config?size_code=60X60`.
    """
    row = repository.fetch_sample_config(size_code)
    if not row:
        raise HTTPException(404, "Size not in sample config")
    return SampleConfigRow(**row)


@router.get("/uom-pcs-per-ctn", response_model=UomPcsRow)
def get_uom_pcs(
    org_id: int = Query(...),
    item_code: str = Query(...),
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: fetch live PCS-per-CTN conversion for an item.

    Expects: query `org_id` and `item_code`.
    Output: `{pcs_per_ctn}`.
    Example: `GET /api/v1/uom-pcs-per-ctn?org_id=481&item_code=MNL60601`.
    """
    val = repository.fetch_uom_pcs_per_ctn(org_id, item_code)
    if val is None:
        raise HTTPException(404, "No UOM conversion for this item")
    return UomPcsRow(pcs_per_ctn=val)


@router.get("/lpn/peek", response_model=LpnResponse)
def lpn_peek(_: str = Depends(security.enforce_rate_limit)):
    """Purpose: preview the next server-owned LPN without consuming it.

    Expects: bearer token.
    Output: `{lpn_id, lpn_num}`.
    Example: `GET /api/v1/lpn/peek` -> `LPN-C-260623000001`.
    """
    code, num = lpn.peek_next()
    return LpnResponse(lpn_id=code, lpn_num=num)


@router.post("/lpn/next", response_model=LpnResponse)
def lpn_next(
    device_id: str = Depends(security.enforce_rate_limit),
    idem_key: Optional[str] = Depends(security.idempotency_key_header),
):
    """Purpose: atomically consume the next daily LPN.

    Expects: bearer token and optional `Idempotency-Key` for retry safety.
    Output: `{lpn_id, lpn_num}`.
    Example: `POST /api/v1/lpn/next` with `Idempotency-Key: <uuid>`.
    """
    cached = security.idempotency.get(idem_key)
    if cached is not None:
        return cached
    code, num = lpn.consume_next()
    resp = LpnResponse(lpn_id=code, lpn_num=num)
    security.idempotency.put(idem_key, resp)
    return resp


@router.post("/cartons", response_model=InsertCartonResponse)
def insert_carton(
    row: CartonRow,
    device_id: str = Depends(security.enforce_rate_limit),
    idem_key: Optional[str] = Depends(security.idempotency_key_header),
):
    """Purpose: persist one printed carton row.

    Expects: a `CartonRow` body with at least `CARTON_CODE`; optional idempotency key.
    Output: `{ok, carton_id, carton_code, created_at}`.
    Example: `POST /api/v1/cartons` with `CARTON_CODE='LPN-C-260623000001'`.
    """
    cached = security.idempotency.get(idem_key)
    if cached is not None:
        return cached
    payload = row.model_dump()
    if not payload.get("CREATED_BY"):
        payload["CREATED_BY"] = device_id
    ok, msg, cid = repository.insert_carton(payload)
    if not ok:
        raise HTTPException(500, msg)
    resp = InsertCartonResponse(
        ok=True,
        carton_id=cid,
        carton_code=row.CARTON_CODE,
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    security.idempotency.put(idem_key, resp)
    return resp


@router.delete("/cartons/{carton_code}", response_model=SimpleOkResponse)
def delete_carton(
    carton_code: str,
    req: SoftDeleteRequest,
    device_id: str = Depends(security.enforce_rate_limit),
):
    """Purpose: soft-delete a carton by setting STATUS='DELETED'.

    Expects: path `carton_code` and body `{by_user}`.
    Output: `{ok:true}` or 404 when the carton is absent.
    Example: `DELETE /api/v1/cartons/LPN-C-260623000001`.
    """
    ok, msg = repository.soft_delete_carton(carton_code, req.by_user or device_id)
    if not ok:
        raise HTTPException(404 if "not found" in msg.lower() else 500, msg)
    return SimpleOkResponse(ok=True)


@router.post("/cartons/{carton_code}/reprint", response_model=ReprintResponse)
def reprint_carton(
    carton_code: str,
    req: ReprintRequest,
    device_id: str = Depends(security.enforce_rate_limit),
):
    """Purpose: mark an explicit operator reprint.

    Expects: path `carton_code` and body `{by_user}`.
    Output: `{ok:true, new_status:'PRINTED-RPn'}`.
    Example: `POST /api/v1/cartons/LPN-C-260623000001/reprint`.
    """
    ok, msg, new_status = repository.mark_reprint(carton_code, req.by_user or device_id)
    if not ok:
        raise HTTPException(404 if "not found" in msg.lower() else 500, msg)
    return ReprintResponse(ok=True, new_status=new_status)


@router.get("/cartons/count", response_model=CountResponse)
def count_cartons(
    scope: str = Query("today", pattern="^(today|total)$"),
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: read printer-owned TODAY/ALL print counts for the dashboard.

    Expects: query `scope=today|total`.
    Output: `{count, scope}`.
    Example: `GET /api/v1/cartons/count?scope=total`.
    """
    return CountResponse(count=printer_counter.count_for_scope(scope), scope=scope)


@router.get("/cartons/{carton_code}")
def get_carton(carton_code: str, _: str = Depends(security.enforce_rate_limit)):
    """Purpose: fetch one carton row for inspection or reprint loading.

    Expects: path `carton_code`.
    Output: full carton row fields as JSON.
    Example: `GET /api/v1/cartons/LPN-C-260623000001`.
    """
    r = repository.get_carton(carton_code)
    if not r:
        raise HTTPException(404, "Carton not found")
    return r


@router.get("/cartons")
def list_cartons(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    brand: Optional[str] = None,
    item_code_like: Optional[str] = None,
    lot_like: Optional[str] = None,
    shift: Optional[str] = None,
    grade: Optional[str] = None,
    status: Optional[str] = None,
    lpn_like: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    include_total: bool = False,
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: search carton history with the GUI's filter set.

    Expects: optional filter query parameters plus `offset` and `limit`.
    Output: newest matching carton rows; with `include_total=true`, `{rows,total}`.
    Example: `GET /api/v1/cartons?brand=Monalisa&offset=30&limit=30`.
    """
    filters = {
        "date_from": date_from, "date_to": date_to,
        "brand": brand, "item_code_like": item_code_like,
        "lot_like": lot_like, "shift": shift, "grade": grade,
        "status": status, "lpn_like": lpn_like,
        "include_deleted": include_deleted, "limit": limit, "offset": offset,
    }
    rows = repository.query_cartons(filters)
    if include_total:
        return {"rows": rows, "total": repository.count_query_cartons(filters)}
    return rows


@router.get("/batches/status", response_model=BatchStatusResponse)
def get_batch_status(
    batch_no: str = Query(...),
    org_id: str = Query(""),
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: check whether a batch exists and whether it is finalized.

    Expects: query `batch_no` and optional `org_id`.
    Output: `{exists, finalized}`.
    Example: `GET /api/v1/batches/status?batch_no=23JUN26XN&org_id=481`.
    """
    return BatchStatusResponse(**repository.batch_status(batch_no, org_id))


@router.get("/batches")
def list_batches(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    brand: Optional[str] = None,
    shift: Optional[str] = None,
    grade: Optional[str] = None,
    status: Optional[str] = None,
    item_code_like: Optional[str] = None,
    lot_like: Optional[str] = None,
    batch_no_like: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    include_total: bool = False,
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: search batch summary rows.

    Expects: optional filter query parameters plus `offset` and `limit`.
    Output: newest matching batch rows; with `include_total=true`, `{rows,total}`.
    Example: `GET /api/v1/batches?status=Y&offset=30&limit=30`.
    """
    filters = {
        "date_from": date_from, "date_to": date_to,
        "brand": brand, "shift": shift, "grade": grade,
        "status": status, "item_code_like": item_code_like,
        "lot_like": lot_like, "batch_no_like": batch_no_like,
        "limit": limit, "offset": offset,
    }
    rows = repository.query_batches(filters)
    if include_total:
        return {"rows": rows, "total": repository.count_query_batches(filters)}
    return rows


@router.post("/batches", response_model=BatchInsertResponse)
def create_initial_batch(
    row: CartonRow,
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: fallback insert of an initial STATUS='N' batch row.

    Expects: carton-like body used to derive batch fields.
    Output: `{ok, batch_id}`.
    Example: `POST /api/v1/batches` when trigger-managed batch master is unavailable.
    """
    ok, msg, bid = repository.insert_initial_batch(row.model_dump())
    if not ok:
        raise HTTPException(500, msg)
    return BatchInsertResponse(ok=True, batch_id=bid)


@router.post("/batches/{batch_no}/finalize", response_model=FinalizeBatchResponse)
def finalize_batch_endpoint(
    batch_no: str,
    req: FinalizeBatchRequest,
    _: str = Depends(security.enforce_rate_limit),
):
    """Purpose: fallback aggregate finalize into batch master.

    Expects: path `batch_no` and body `{org_id}`.
    Output: `{ok, rows_inserted}`.
    Example: `POST /api/v1/batches/23JUN26XN/finalize`.
    """
    ok, msg, rows = repository.finalize_batch(batch_no, req.org_id)
    if not ok:
        raise HTTPException(500, msg)
    return FinalizeBatchResponse(ok=True, rows_inserted=rows)


@router.get("/device/{device_id}/config", response_model=DeviceConfigResponse)
def get_device_config(
    device_id: str,
    requester: str = Depends(security.enforce_rate_limit),
):
    """Purpose: let a device pull server-owned printer/QR/message config.

    Expects: path `device_id` matching the authenticated device.
    Output: `{device_id, config, updated_at}`.
    Example: `GET /api/v1/device/stm32-test/config`.
    """
    if requester != device_id:
        raise HTTPException(403, "Token does not match requested device")
    return DeviceConfigResponse(**device_registry.get_config(device_id))


@router.patch("/device/{device_id}/config", response_model=DeviceConfigResponse)
def patch_device_config(
    device_id: str,
    req: DeviceConfigPatchRequest,
    requester: str = Depends(security.enforce_rate_limit),
):
    """Purpose: update server-owned config for a provisioned device.

    Expects: path `device_id` and body `{config:{...partial sections...}}`.
    Output: the merged config object and timestamp.
    Example: `PATCH /api/v1/device/stm32-test/config` with `{"config":{"qr":{"max_dots":28}}}`.
    """
    if requester != device_id:
        raise HTTPException(403, "Token does not match requested device")
    return DeviceConfigResponse(**device_registry.patch_config(device_id, req.config))


@router.post("/device/{device_id}/heartbeat", response_model=HeartbeatResponse)
def device_heartbeat(
    device_id: str,
    req: HeartbeatRequest,
    requester: str = Depends(security.enforce_rate_limit),
):
    """Purpose: record liveness for fleet/load-balancer monitoring.

    Expects: path `device_id` and body `{state, fw_version, ip}`.
    Output: `{ok, device_id, server_time}`.
    Example: `POST /api/v1/device/stm32-test/heartbeat` with `{"state":"ready"}`.
    """
    if requester != device_id:
        raise HTTPException(403, "Token does not match requested device")
    row = device_registry.record_heartbeat(device_id, req.model_dump())
    return HeartbeatResponse(ok=True, device_id=device_id, server_time=row["seen_at"])
