"""Pydantic request / response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    device_id: str
    secret: str


class LoginResponse(BaseModel):
    token: str
    expires_at: int


class RefreshResponse(LoginResponse):
    pass


class HealthResponse(BaseModel):
    ok: bool
    mode: str
    server_time: str
    db: Dict[str, Any]


class BrandRow(BaseModel):
    brand: str
    org_code: str
    inv_code: int


class ItemRow(BaseModel):
    code: str
    size: str
    desc: str = ""


class SampleConfigRow(BaseModel):
    normal_pcs: int
    sample_pcs: int
    conversion: float


class UomPcsRow(BaseModel):
    pcs_per_ctn: float


class LpnResponse(BaseModel):
    lpn_id: str
    lpn_num: int


class CartonRow(BaseModel):
    """Mirrors APPS.XXFG_CARTON_MASTER columns the device writes.
    All fields except CARTON_CODE are optional (NULL-able in Oracle)."""
    CARTON_CODE: str
    QR_CODE: Optional[str] = None
    ORGANIZATION_ID: Optional[str] = None
    PLANT_CODE: Optional[str] = None
    INVENTORY_ITEM_ID: Optional[int] = None
    ITEM_CODE: Optional[str] = None
    ITEM_DESC: Optional[str] = None
    LOT_NUMBER: Optional[str] = None
    CARTON_QTY: Optional[float] = None
    UOM_CODE: Optional[str] = "CTN"
    LPN_ID: Optional[int] = None
    LPN_CONTEXT: Optional[str] = "CARTON"
    BATCH_NO: Optional[str] = None
    BATCH_DATE: Optional[datetime] = None
    LOT_NO: Optional[str] = None
    BRAND: Optional[str] = None
    GRADE: Optional[str] = None
    SHIFT: Optional[str] = None
    BATCH_TIME: Optional[str] = None
    STATUS: Optional[str] = "PRINTED"
    SIZE_CODE: Optional[str] = None
    GRADE_CODE: Optional[str] = None
    NO_PCS: Optional[int] = None
    CTN_TYPE: Optional[str] = None
    TOTAL_PLANNED_QTY: Optional[float] = None
    CREATED_BY: Optional[str] = None


class InsertCartonResponse(BaseModel):
    ok: bool
    message: str = ""
    carton_id: Optional[int] = None
    carton_code: Optional[str] = None
    created_at: Optional[str] = None


class SimpleOkResponse(BaseModel):
    ok: bool
    message: str = ""


class ReprintResponse(BaseModel):
    ok: bool
    message: str = ""
    new_status: Optional[str] = None


class CountResponse(BaseModel):
    count: int
    scope: str


class BatchStatusResponse(BaseModel):
    exists: bool
    finalized: bool


class FinalizeBatchRequest(BaseModel):
    org_id: str = ""


class SoftDeleteRequest(BaseModel):
    by_user: str = "DIKAI_GUI"


class ReprintRequest(BaseModel):
    by_user: str = "DIKAI_GUI"


class BatchInsertResponse(BaseModel):
    ok: bool
    batch_id: Optional[int] = None


class FinalizeBatchResponse(BaseModel):
    ok: bool
    rows_inserted: int


class DeviceConfigResponse(BaseModel):
    device_id: str
    config: Dict[str, Any]
    updated_at: str


class DeviceConfigPatchRequest(BaseModel):
    config: Dict[str, Any]


class HeartbeatRequest(BaseModel):
    state: str
    fw_version: str = ""
    ip: str = ""


class HeartbeatResponse(BaseModel):
    ok: bool
    device_id: str
    server_time: str
