-- ============================================================
-- Load / Upsert Script for PO_DB
-- Works for BOTH the initial full dump AND every incremental
-- run (customer only sends new/changed rows from run 2 onward).
--
-- How it works:
--   1. Data is COPY'd into a temporary staging table that
--      mirrors the target table.
--   2. Rows are INSERTed into the real table; if the primary
--      key already exists, the row is UPDATED instead
--      (PostgreSQL "upsert" via ON CONFLICT).
--   3. Staging table is dropped at the end of each run.
--
-- Run order matters (parents before children):
--   vendor_master -> item_master -> po_header -> po_details
--
-- Usage:
--   psql -U your_user -d PO_DB -f load_csv_data.sql
--
-- Adjust the file paths below to wherever the customer's CSVs
-- are placed before running.
-- ============================================================

\set vendor_csv 'vendor_master.csv'
\set item_csv   'item_master.csv'
\set header_csv 'po_header.csv'
\set details_csv 'po_details.csv'

BEGIN;

-- ------------------------------------------------------------
-- vendor_master
-- ------------------------------------------------------------
CREATE TEMP TABLE stg_vendor_master (LIKE vendor_master INCLUDING ALL) ON COMMIT DROP;

\copy stg_vendor_master FROM :'vendor_csv' WITH (FORMAT csv, HEADER true)

INSERT INTO vendor_master
SELECT * FROM stg_vendor_master
ON CONFLICT (vendor_id) DO UPDATE SET
    vendor_name        = EXCLUDED.vendor_name,
    gst_tax_number      = EXCLUDED.gst_tax_number,
    tin_number          = EXCLUDED.tin_number,
    vendor_address_1    = EXCLUDED.vendor_address_1,
    vendor_address_2    = EXCLUDED.vendor_address_2,
    vendor_address_3    = EXCLUDED.vendor_address_3,
    city                = EXCLUDED.city,
    state               = EXCLUDED.state,
    country             = EXCLUDED.country,
    pin_code            = EXCLUDED.pin_code,
    primary_ph_number   = EXCLUDED.primary_ph_number,
    email_primary       = EXCLUDED.email_primary,
    bank_ac_number      = EXCLUDED.bank_ac_number,
    bank_name           = EXCLUDED.bank_name;

-- ------------------------------------------------------------
-- item_master
-- ------------------------------------------------------------
CREATE TEMP TABLE stg_item_master (LIKE item_master INCLUDING ALL) ON COMMIT DROP;

\copy stg_item_master FROM :'item_csv' WITH (FORMAT csv, HEADER true)

INSERT INTO item_master
SELECT * FROM stg_item_master
ON CONFLICT (item_id) DO UPDATE SET
    item_name   = EXCLUDED.item_name,
    description = EXCLUDED.description,
    category    = EXCLUDED.category,
    units       = EXCLUDED.units,
    rate        = EXCLUDED.rate;

-- ------------------------------------------------------------
-- po_header
-- ------------------------------------------------------------
CREATE TEMP TABLE stg_po_header (LIKE po_header INCLUDING ALL) ON COMMIT DROP;

\copy stg_po_header FROM :'header_csv' WITH (FORMAT csv, HEADER true)

INSERT INTO po_header
SELECT * FROM stg_po_header
ON CONFLICT (po_id, business_unit) DO UPDATE SET
    vendor_id  = EXCLUDED.vendor_id,
    po_status  = EXCLUDED.po_status,
    po_type    = EXCLUDED.po_type,
    po_date    = EXCLUDED.po_date;

-- ------------------------------------------------------------
-- po_details
-- ------------------------------------------------------------
CREATE TEMP TABLE stg_po_details (LIKE po_details INCLUDING ALL) ON COMMIT DROP;

\copy stg_po_details FROM :'details_csv' WITH (FORMAT csv, HEADER true)

INSERT INTO po_details
SELECT * FROM stg_po_details
ON CONFLICT (po_id, business_unit, item_id) DO UPDATE SET
    line_number = EXCLUDED.line_number,
    rate        = EXCLUDED.rate,
    qty         = EXCLUDED.qty,
    units       = EXCLUDED.units,
    comments    = EXCLUDED.comments,
    total       = EXCLUDED.total;

COMMIT;
