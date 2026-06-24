"""Documented SQL catalog for the Dikai server.

Every Oracle statement used by an API operation lives here instead of in
the route layer. Each query spec records purpose, expected data, output,
and a concrete example so the API contract and the SQL stay readable
when someone audits the server later.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuerySpec:
    """One documented SQL operation used by the repository layer."""

    purpose: str
    expects: str
    output: str
    example: str
    sql: str


HEALTH_SELECT = QuerySpec(
    purpose="Verify that a borrowed Oracle connection can execute SQL.",
    expects="No binds.",
    output="One row containing the numeric value 1.",
    example="GET /api/v1/health -> SELECT 1 FROM DUAL.",
    sql="SELECT 1 FROM DUAL",
)

FETCH_ITEMS = QuerySpec(
    purpose="Load item codes and tile sizes for the selected brand/org.",
    expects=":oid = ORGANIZATION_ID, for example 481 for Monalisa.",
    output="Rows of Item_Code and CAT5 mapped to API fields code and size.",
    example="GET /api/v1/items?org_id=481 returns selectable item codes.",
    sql="""
        SELECT DISTINCT Item_Code, CAT5 FROM APPS.XXFG_ORG_ITEMS
         WHERE ORGANIZATION_ID = :oid AND Item_Code IS NOT NULL
         ORDER BY Item_Code
    """,
)

FETCH_SAMPLE_CONFIG = QuerySpec(
    purpose="Load sample-carton sizing defaults for a tile size.",
    expects=":s = SIZE_CODE, for example '60X60'.",
    output="NORMAL_PCS_CTN, SAMPLE_PCS_CTN, and CONVERSION_CTN.",
    example="GET /api/v1/sample-config?size_code=60X60 -> normal/sample pcs.",
    sql="""
        SELECT NORMAL_PCS_CTN, SAMPLE_PCS_CTN, CONVERSION_CTN
          FROM APPS.XXFG_SAMPLE_CARTON_CONFIG
         WHERE SIZE_CODE = :s AND ROWNUM = 1
    """,
)

FETCH_UOM_PCS_PER_CTN = QuerySpec(
    purpose="Find the active PCS-per-CTN conversion for a selected item.",
    expects=":org = ORGANIZATION_ID and :code = ITEM_CODE.",
    output="One active conversion row; CONVERSION_RATE is returned as pcs_per_ctn.",
    example="GET /api/v1/uom-pcs-per-ctn?org_id=481&item_code=MNL60601.",
    sql="""
        SELECT CONVERSION_RATE, PRIMARY_UOM_CODE, TARGET_UOM_CODE
          FROM APPS.XXFG_UOM_CONVERSIONS_V
         WHERE ORGANIZATION_ID = :org AND ITEM_CODE = :code
           AND (DISABLE_DATE IS NULL OR DISABLE_DATE > SYSDATE)
         ORDER BY DISABLE_DATE NULLS FIRST
         FETCH FIRST 1 ROWS ONLY
    """,
)

NEXT_CARTON_ID = QuerySpec(
    purpose="Allocate the next CARTON_ID using the legacy MAX+1 pattern.",
    expects="No binds; {carton_table} is injected from trusted settings.",
    output="One number: NVL(MAX(CARTON_ID),0)+1.",
    example="POST /api/v1/cartons calls this before INSERT.",
    sql="SELECT NVL(MAX(CARTON_ID),0)+1 FROM {carton_table}",
)

INSERT_CARTON = QuerySpec(
    purpose="Persist one printed carton row into XXFG_CARTON_MASTER.",
    expects="All INSERTED_COLUMNS as named binds, CARTON_ID already set.",
    output="A committed carton row; repository returns the CARTON_ID.",
    example="POST /api/v1/cartons with CARTON_CODE='LPN-C-260623000001'.",
    sql="INSERT INTO {carton_table} ({cols}) VALUES ({placeholders})",
)

SOFT_DELETE_CARTON = QuerySpec(
    purpose="Soft-delete one carton while preserving the row for audit.",
    expects=":cc = CARTON_CODE and :u = user/device id performing delete.",
    output="Updated STATUS='DELETED' row or rowcount 0 when not found.",
    example="DELETE /api/v1/cartons/LPN-C-260623000001 body {'by_user':'DIKAI_GUI'}.",
    sql="""
        UPDATE {carton_table}
           SET STATUS = 'DELETED',
               LAST_UPDATED_BY = :u,
               LAST_UPDATE_DATE = SYSDATE
         WHERE CARTON_CODE = :cc
    """,
)

MARK_REPRINT = QuerySpec(
    purpose="Bump a carton STATUS from PRINTED to PRINTED-RPn for explicit reprints.",
    expects=":cc = CARTON_CODE and :u = user/device id performing reprint.",
    output="Updated row; repository reads STATUS afterward for the response.",
    example="POST /api/v1/cartons/LPN-C-260623000001/reprint -> PRINTED-RP1.",
    sql="""
        UPDATE {carton_table}
           SET STATUS = CASE
                          WHEN STATUS LIKE '%-RP%'
                          THEN REGEXP_REPLACE(STATUS, '-RP(\\d+)$', '-RP' ||
                               (TO_NUMBER(REGEXP_SUBSTR(STATUS, '\\d+$')) + 1))
                          ELSE NVL(STATUS, 'PRINTED') || '-RP1'
                        END,
               LAST_UPDATED_BY = :u,
               LAST_UPDATE_DATE = SYSDATE
         WHERE CARTON_CODE = :cc
    """,
)

SELECT_REPRINT_STATUS = QuerySpec(
    purpose="Read the new STATUS after mark-reprint succeeds.",
    expects=":cc = CARTON_CODE.",
    output="One STATUS string such as PRINTED-RP2.",
    example="Repository returns new_status in /cartons/{code}/reprint response.",
    sql="SELECT STATUS FROM {carton_table} WHERE CARTON_CODE = :cc",
)

CARTON_SELECT_COLUMNS = """
    SELECT CARTON_CODE AS LPN_DISPLAY, CARTON_CODE, BRAND, ITEM_CODE,
           LOT_NUMBER, SHIFT, GRADE, SIZE_CODE, BATCH_NO, BATCH_DATE,
           BATCH_TIME, STATUS, QR_CODE,
           CARTON_ID, ORGANIZATION_ID, INVENTORY_ITEM_ID, ITEM_DESC,
           CARTON_QTY, UOM_CODE, LPN_ID, LPN_CONTEXT, LOT_NO,
           GRADE_CODE, NO_PCS, CTN_TYPE, CREATED_BY, CREATION_DATE
"""

GET_CARTON = QuerySpec(
    purpose="Look up one carton for history/reprint/delete confirmation.",
    expects=":cc = CARTON_CODE.",
    output="Full carton read model fields.",
    example="GET /api/v1/cartons/LPN-C-260623000001.",
    sql="{select_cols} FROM {carton_table} WHERE CARTON_CODE = :cc",
)

QUERY_CARTONS = QuerySpec(
    purpose="List carton history using the filter set exposed by the GUI.",
    expects="Optional date, brand, shift, grade, status, item, lot, LPN, offset and limit binds.",
    output="Newest matching carton rows, excluding DELETED unless requested.",
    example="GET /api/v1/cartons?date_from=2026-06-23&brand=Monalisa&offset=30&limit=30.",
    sql="""
        {select_cols}
          FROM {carton_table}
          {where_sql}
         ORDER BY CREATION_DATE DESC
         OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
    """,
)

COUNT_QUERY_CARTONS = QuerySpec(
    purpose="Count carton history rows that match the GUI's current filters.",
    expects="Same optional filter binds as QUERY_CARTONS, without offset/limit.",
    output="One integer total used by the paged View table.",
    example="GET /api/v1/cartons?brand=Monalisa&include_total=true.",
    sql="""
        SELECT COUNT(*)
          FROM {carton_table}
          {where_sql}
    """,
)

COUNT_TODAY = QuerySpec(
    purpose="Count non-deleted carton rows created on the Oracle server date.",
    expects="No binds.",
    output="One integer count.",
    example="GET /api/v1/cartons/count?scope=today.",
    sql="""
        SELECT COUNT(*) FROM {carton_table}
         WHERE TRUNC(CREATION_DATE)=TRUNC(SYSDATE)
           AND (STATUS IS NULL OR STATUS <> 'DELETED')
    """,
)

COUNT_TOTAL = QuerySpec(
    purpose="Count all non-deleted carton rows in XXFG_CARTON_MASTER.",
    expects="No binds.",
    output="One integer count.",
    example="GET /api/v1/cartons/count?scope=total.",
    sql="""
        SELECT COUNT(*) FROM {carton_table}
         WHERE (STATUS IS NULL OR STATUS <> 'DELETED')
    """,
)

QUERY_BATCHES = QuerySpec(
    purpose="List batch summary rows maintained by the Oracle trigger.",
    expects="Optional date, brand, shift, grade, status, item, lot, batch, offset and limit binds.",
    output="Newest matching XXFG_CARTON_BATCH_MASTER rows.",
    example="GET /api/v1/batches?status=Y&offset=30&limit=30.",
    sql="""
        SELECT BATCH_ID, ORGANIZATION_ID, BATCH_NO, PRODUCTION_DATE,
               ITEM_CODE, PRODUCT_TYPE, SIZE_CODE, UOM_CODE, BATCH_DATE,
               LOT_NO, BRAND, GRADE, SHIFT, STATUS,
               PRODUCTION_QTY, PRODUCED_CARTON_QTY
          FROM {batch_table}
          {where_sql}
         ORDER BY BATCH_ID DESC
         OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
    """,
)

COUNT_QUERY_BATCHES = QuerySpec(
    purpose="Count batch summary rows that match the GUI's current filters.",
    expects="Same optional filter binds as QUERY_BATCHES, without offset/limit.",
    output="One integer total used by the paged Batches table.",
    example="GET /api/v1/batches?status=Y&include_total=true.",
    sql="""
        SELECT COUNT(*)
          FROM {batch_table}
          {where_sql}
    """,
)

BATCH_STATUS = QuerySpec(
    purpose="Check whether a batch exists and whether any row is finalized.",
    expects=":bn = BATCH_NO and :org = ORGANIZATION_ID or empty string.",
    output="finalized flag and row_count used to produce exists/finalized.",
    example="GET /api/v1/batches/status?batch_no=23JUN26XN&org_id=481.",
    sql="""
        SELECT MAX(CASE WHEN STATUS='Y' THEN 1 ELSE 0 END) AS finalized,
               COUNT(*) AS row_count
          FROM {batch_table}
         WHERE BATCH_NO=:bn AND (:org='' OR ORGANIZATION_ID=:org)
    """,
)

INSERT_INITIAL_BATCH = QuerySpec(
    purpose="Fallback insert of an initial STATUS='N' batch row when no DB trigger exists.",
    expects="Carton-derived binds such as :org, :bn, :item, :brand, :pqty.",
    output="Inserted batch row; repository then reads back latest BATCH_ID.",
    example="POST /api/v1/batches with a CartonRow body.",
    sql="""
        INSERT INTO {batch_table} (
          BATCH_ID, ORGANIZATION_ID, BATCH_NO, PRODUCTION_DATE,
          ITEM_CODE, PRODUCT_TYPE, SIZE_CODE, UOM_CODE, BATCH_DATE,
          LOT_NO, BRAND, GRADE, SHIFT, STATUS,
          PRODUCTION_QTY, PRODUCED_CARTON_QTY
        ) VALUES (
          {batch_seq}.NEXTVAL, :org, :bn, TRUNC(:pdate),
          :item, :ptype, :sz_c, :uom_c, TRUNC(:bdate),
          :lot, :brand, :grade, :shift, 'N', :pqty, :cqty
        )
    """,
)

SELECT_LATEST_BATCH_ID = QuerySpec(
    purpose="Return the newest BATCH_ID after fallback batch insert.",
    expects=":bn = BATCH_NO.",
    output="One BATCH_ID value, newest first.",
    example="Repository uses this to fill POST /api/v1/batches response.",
    sql="""
        SELECT BATCH_ID FROM {batch_table}
         WHERE BATCH_NO=:bn ORDER BY BATCH_ID DESC FETCH FIRST 1 ROWS ONLY
    """,
)

FINALIZE_BATCH = QuerySpec(
    purpose="Fallback aggregate finalize from carton rows into batch master.",
    expects=":bn = BATCH_NO and :org = ORGANIZATION_ID or empty string.",
    output="Inserted STATUS='Y' summary rows; rowcount becomes rows_inserted.",
    example="POST /api/v1/batches/23JUN26XN/finalize body {'org_id':'481'}.",
    sql="""
        INSERT INTO {batch_table} (
          BATCH_ID, ORGANIZATION_ID, BATCH_NO, PRODUCTION_DATE,
          ITEM_CODE, PRODUCT_TYPE, SIZE_CODE, UOM_CODE, BATCH_DATE,
          LOT_NO, BRAND, GRADE, SHIFT, STATUS,
          PRODUCTION_QTY, PRODUCED_CARTON_QTY
        )
        SELECT {batch_seq}.NEXTVAL, x.organization_id, x.batch_no, x.production_date,
               x.item_code, x.product_type, x.size_code, x.uom_code,
               x.batch_date, x.lot_no, x.brand, x.grade, x.shift,
               x.status, x.production_qty, x.produced_carton_qty
          FROM (
            SELECT organization_id, batch_no, TRUNC(batch_date) production_date,
                   item_code, ctn_type product_type, size_code, uom_code,
                   TRUNC(batch_date) batch_date, lot_number lot_no,
                   brand, grade, shift, 'Y' status,
                   NVL(SUM(NVL(no_pcs,0) * (1 + CASE WHEN REGEXP_LIKE(status, '-RP[0-9]+$')
                       THEN TO_NUMBER(REGEXP_SUBSTR(status, '[0-9]+$')) ELSE 0 END)), 0) production_qty,
                   NVL(SUM(NVL(carton_qty,0) * (1 + CASE WHEN REGEXP_LIKE(status, '-RP[0-9]+$')
                       THEN TO_NUMBER(REGEXP_SUBSTR(status, '[0-9]+$')) ELSE 0 END)), 0) produced_carton_qty
              FROM {carton_table}
             WHERE status LIKE 'PRINTED%' AND status NOT LIKE '%DELETED%'
               AND batch_no = :bn AND (:org = '' OR organization_id = :org)
             GROUP BY organization_id, batch_no, TRUNC(batch_date),
                      item_code, ctn_type, size_code, uom_code,
                      lot_number, brand, grade, shift
          ) x
    """,
)

CREATE_LPN_COUNTER_TABLE = QuerySpec(
    purpose="Create the server-owned daily LPN counter table if missing.",
    expects="{counter_table} injected from trusted settings.",
    output="Table exists with COUNTER_DATE primary key and COUNTER_VALUE.",
    example="Server startup runs this before accepting LPN requests.",
    sql="""
        BEGIN
          EXECUTE IMMEDIATE
            'CREATE TABLE {counter_table} (
               COUNTER_DATE VARCHAR2(10) PRIMARY KEY,
               COUNTER_VALUE NUMBER NOT NULL
             )';
        EXCEPTION WHEN OTHERS THEN
          IF SQLCODE NOT IN (-955) THEN RAISE; END IF;
        END;
    """,
)

READ_LPN_COUNTER = QuerySpec(
    purpose="Read today's LPN counter without incrementing it.",
    expects=":d = ISO date string YYYY-MM-DD.",
    output="Current COUNTER_VALUE row or no row when today has not printed yet.",
    example="GET /api/v1/lpn/peek reads and returns current+1.",
    sql="SELECT COUNTER_VALUE FROM {counter_table} WHERE COUNTER_DATE = :d",
)

UPSERT_INCREMENT_LPN_COUNTER = QuerySpec(
    purpose="Atomically create or increment today's server-owned LPN counter.",
    expects=":d = ISO date string YYYY-MM-DD.",
    output="Updated counter row; repository reads it immediately afterward.",
    example="POST /api/v1/lpn/next returns LPN-C-YYMMDD000001 on first call.",
    sql="""
        MERGE INTO {counter_table} t
        USING (SELECT :d AS COUNTER_DATE FROM DUAL) s
           ON (t.COUNTER_DATE = s.COUNTER_DATE)
        WHEN MATCHED THEN UPDATE SET COUNTER_VALUE = COUNTER_VALUE + 1
        WHEN NOT MATCHED THEN INSERT (COUNTER_DATE, COUNTER_VALUE)
                               VALUES (s.COUNTER_DATE, 1)
    """,
)
