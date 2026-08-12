-- ============================================================
-- Purchase Order (PO) Database Creation Script
-- Target: PostgreSQL
-- Tables: vendor_master, item_master, po_header, po_details
-- ============================================================

-- ------------------------------------------------------------
-- STEP 1: Create the database
-- NOTE: CREATE DATABASE cannot run inside a transaction block
-- and cannot run inside a script that's already connected to
-- another database in the same statement batch. Run this first,
-- on its own, while connected to any existing database
-- (e.g. postgres), then connect to PO_DB before running the
-- rest of this script (see psql commands below).
-- ------------------------------------------------------------
-- CREATE DATABASE "PO_DB";
-- \c PO_DB

BEGIN;



-- ------------------------------------------------------------
-- Vendor_master
-- ------------------------------------------------------------
DROP TABLE IF EXISTS vendor_master CASCADE;
CREATE TABLE vendor_master (
    vendor_id           VARCHAR(500)  NOT NULL,
    vendor_name         VARCHAR(100) NOT NULL,
    gst_tax_number      VARCHAR(100),
    tin_number          VARCHAR(100),
    vendor_address_1    VARCHAR(255),
    vendor_address_2    VARCHAR(255),
    vendor_address_3    VARCHAR(255),
    city                VARCHAR(20),
    state               VARCHAR(20),
    country             VARCHAR(20),
    pin_code            INT,
    primary_ph_number   VARCHAR(15),
    email_primary       VARCHAR(255),
    bank_ac_number      VARCHAR(34),
    bank_name           VARCHAR(125),
    CONSTRAINT pk_vendor_master PRIMARY KEY (vendor_id)
);

-- ------------------------------------------------------------
-- Item_master
-- ------------------------------------------------------------
DROP TABLE IF EXISTS item_master CASCADE;
CREATE TABLE item_master (
    item_id      VARCHAR(500) NOT NULL,
    item_name    TEXT,
    description  TEXT,
    category     VARCHAR(500),
    units        VARCHAR(10),
    rate         FLOAT,
    CONSTRAINT pk_item_master PRIMARY KEY (item_id)
);

-- ------------------------------------------------------------
-- PO_header
-- ------------------------------------------------------------
DROP TABLE IF EXISTS po_header CASCADE;
CREATE TABLE po_header (
    po_id         VARCHAR(500) NOT NULL,
    business_unit VARCHAR(500) NOT NULL,
    vendor_id     VARCHAR(500),
    po_status     VARCHAR(100),
    po_type       VARCHAR(100),
    po_date       TIMESTAMP,
    CONSTRAINT pk_po_header PRIMARY KEY (po_id, business_unit),
    CONSTRAINT fk_po_header_vendor
        FOREIGN KEY (vendor_id)
        REFERENCES vendor_master (vendor_id)
);

-- ------------------------------------------------------------
-- PO_details
-- ------------------------------------------------------------
DROP TABLE IF EXISTS po_details CASCADE;
CREATE TABLE po_details (
    po_id         VARCHAR(500) NOT NULL,
    business_unit VARCHAR(500) NOT NULL,
    item_id       VARCHAR(500) NOT NULL,
    line_number   INT,
    rate          FLOAT,
    qty           FLOAT,
    units         VARCHAR(10),
    comments      TEXT,
    total         FLOAT,
    CONSTRAINT pk_po_details PRIMARY KEY (po_id, business_unit, item_id),
    CONSTRAINT fk_po_details_header
        FOREIGN KEY (po_id, business_unit)
        REFERENCES po_header (po_id, business_unit),
    CONSTRAINT fk_po_details_item
        FOREIGN KEY (item_id)
        REFERENCES item_master (item_id)
);

COMMIT;
