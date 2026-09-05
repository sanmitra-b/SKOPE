-- SKOPE canonical PostgreSQL schema.
-- Phase 1 tables are populated from harmonized_data.
-- Phase 2 tables remain empty until their canonical data is approved.

CREATE SCHEMA IF NOT EXISTS skope;
CREATE SCHEMA IF NOT EXISTS restricted;
SET search_path TO skope, public;

CREATE TABLE etl_run (
    run_id BIGSERIAL PRIMARY KEY,
    dataset_version VARCHAR(40) NOT NULL,
    transformation_version VARCHAR(40) NOT NULL,
    source_root TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE source_file_manifest (
    source_path TEXT PRIMARY KEY,
    sha256 CHAR(64) NOT NULL,
    row_count BIGINT,
    byte_size BIGINT NOT NULL,
    source_role VARCHAR(40) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dim_date (
    date_key DATE PRIMARY KEY,
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    day_of_month SMALLINT NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    month SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    quarter SMALLINT NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    year SMALLINT NOT NULL,
    is_weekend BOOLEAN NOT NULL
);

CREATE TABLE dim_product (
    product_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_product_id VARCHAR(100) NOT NULL,
    product_name TEXT NOT NULL,
    category TEXT,
    brand TEXT,
    unit_price_original NUMERIC(18,4),
    currency_code VARCHAR(20),
    mrp_original NUMERIC(18,4),
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_product_id)
);

CREATE TABLE dim_customer (
    customer_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_customer_id VARCHAR(100) NOT NULL,
    display_name TEXT NOT NULL,
    customer_segment TEXT,
    area TEXT,
    city TEXT,
    state_province TEXT,
    country TEXT,
    registration_date DATE,
    contact_id VARCHAR(160) UNIQUE,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_customer_id)
);

CREATE TABLE dim_location (
    location_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_location_id VARCHAR(160) NOT NULL,
    location_type VARCHAR(50) NOT NULL,
    facility_name TEXT,
    city TEXT,
    state_province TEXT,
    country TEXT,
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_location_id)
);

CREATE TABLE dim_partner (
    partner_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_partner_id VARCHAR(160) NOT NULL,
    partner_name TEXT NOT NULL,
    partner_type VARCHAR(50) NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_partner_id)
);

CREATE TABLE fact_order_fulfillment (
    order_fact_id CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_line_id VARCHAR(160) NOT NULL,
    order_id VARCHAR(160) NOT NULL,
    display_order_id VARCHAR(220) NOT NULL,
    customer_key CHAR(64) REFERENCES dim_customer(customer_key),
    product_key CHAR(64) REFERENCES dim_product(product_key),
    origin_location_key CHAR(64) REFERENCES dim_location(location_key),
    order_date DATE REFERENCES dim_date(date_key),
    quantity NUMERIC(18,4) NOT NULL,
    unit_price_original NUMERIC(18,4),
    gross_amount_original NUMERIC(18,4),
    discount_original NUMERIC(18,4),
    net_amount_original NUMERIC(18,4),
    shipping_cost_original NUMERIC(18,4),
    profit_original NUMERIC(18,4),
    currency_code VARCHAR(20),
    order_status TEXT,
    payment_method TEXT,
    source_status TEXT,
    destination_city TEXT,
    destination_state TEXT,
    destination_country TEXT,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_line_id)
);

CREATE TABLE fact_delivery (
    delivery_fact_id CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(160) NOT NULL,
    order_id VARCHAR(160) NOT NULL,
    display_order_id VARCHAR(220) NOT NULL,
    partner_key CHAR(64) REFERENCES dim_partner(partner_key),
    origin_location_key CHAR(64) REFERENCES dim_location(location_key),
    promised_time TIMESTAMP,
    actual_time TIMESTAMP,
    delivery_time_minutes NUMERIC(14,2),
    distance_km NUMERIC(14,3),
    delivery_status TEXT NOT NULL,
    source_status TEXT,
    reason_if_delayed TEXT,
    delivery_mode TEXT,
    is_late BOOLEAN,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE fact_inventory_event (
    inventory_event_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_event_id VARCHAR(160) NOT NULL,
    product_key CHAR(64) NOT NULL REFERENCES dim_product(product_key),
    location_key CHAR(64) REFERENCES dim_location(location_key),
    event_date DATE REFERENCES dim_date(date_key),
    event_type VARCHAR(50) NOT NULL,
    quantity_received NUMERIC(18,4),
    quantity_damaged NUMERIC(18,4),
    quantity_on_hand NUMERIC(18,4),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_event_id)
);

CREATE TABLE fact_freight_order (
    freight_order_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(160) NOT NULL,
    display_order_id VARCHAR(220) NOT NULL,
    order_date DATE REFERENCES dim_date(date_key),
    carrier_key CHAR(64) REFERENCES dim_partner(partner_key),
    plant_location_key CHAR(64) REFERENCES dim_location(location_key),
    origin_port_key CHAR(64) REFERENCES dim_location(location_key),
    destination_port_key CHAR(64) REFERENCES dim_location(location_key),
    customer_reference TEXT,
    product_reference TEXT,
    service_level TEXT,
    transport_days NUMERIC(12,2),
    ship_ahead_days INTEGER,
    ship_late_days INTEGER,
    unit_quantity NUMERIC(18,4),
    weight NUMERIC(18,4),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE fact_container_tracking (
    container_tracking_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(160) NOT NULL,
    container_number VARCHAR(100) NOT NULL UNIQUE,
    vessel_name TEXT,
    freight_forwarder TEXT,
    linked_order_id VARCHAR(160),
    origin_port_key CHAR(64) REFERENCES dim_location(location_key),
    destination_port_key CHAR(64) REFERENCES dim_location(location_key),
    place_of_dispatch TEXT,
    post_discharge_place TEXT,
    dispatch_date TIMESTAMP,
    port_loading_date TIMESTAMP,
    port_discharge_date TIMESTAMP,
    post_discharge_date TIMESTAMP,
    last_tracked_at TIMESTAMP,
    delivered_flag BOOLEAN,
    delivered_date TIMESTAMP,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE fact_shipment_record (
    shipment_record_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(160) NOT NULL,
    item_name TEXT,
    price_original NUMERIC(18,4),
    currency_code VARCHAR(20),
    weight_kg NUMERIC(18,4),
    length_m NUMERIC(18,4),
    width_m NUMERIC(18,4),
    height_m NUMERIC(18,4),
    shipment_date DATE REFERENCES dim_date(date_key),
    destination_port_key CHAR(64) REFERENCES dim_location(location_key),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE fact_customer_feedback (
    feedback_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_feedback_id VARCHAR(160) NOT NULL,
    order_id VARCHAR(160),
    display_order_id VARCHAR(220),
    customer_key CHAR(64) REFERENCES dim_customer(customer_key),
    rating INTEGER,
    feedback_text TEXT,
    feedback_category TEXT,
    sentiment TEXT,
    feedback_date DATE REFERENCES dim_date(date_key),
    link_status VARCHAR(50) NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL DEFAULT 'DIRECT_MAP',
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_feedback_id)
);

CREATE TABLE customer_communication (
    communication_key CHAR(64) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(160) NOT NULL,
    communication_type VARCHAR(50) NOT NULL,
    body_text TEXT NOT NULL,
    linked_order_id VARCHAR(160),
    link_status VARCHAR(50) NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_method VARCHAR(50) NOT NULL,
    transformation_version VARCHAR(40) NOT NULL,
    dataset_as_of DATE NOT NULL,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE email_thread (
    thread_id VARCHAR(260) PRIMARY KEY,
    business_domain VARCHAR(80) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    primary_entity_type VARCHAR(80) NOT NULL,
    primary_entity_id VARCHAR(220) NOT NULL,
    source_system VARCHAR(50) NOT NULL,
    related_entities JSONB NOT NULL,
    source_records JSONB NOT NULL,
    facts JSONB NOT NULL,
    is_synthetic BOOLEAN NOT NULL,
    transformation_version VARCHAR(40) NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    dataset_as_of DATE NOT NULL
);

CREATE TABLE email_message (
    message_id VARCHAR(300) PRIMARY KEY,
    thread_id VARCHAR(260) NOT NULL REFERENCES email_thread(thread_id) ON DELETE CASCADE,
    message_sequence SMALLINT NOT NULL,
    sent_at TIMESTAMP NOT NULL,
    sender_address TEXT NOT NULL,
    recipient_address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    UNIQUE (thread_id, message_sequence)
);

CREATE TABLE document_index (
    document_id CHAR(64) PRIMARY KEY,
    document_type VARCHAR(80) NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    source_system VARCHAR(50),
    primary_entity_type VARCHAR(80),
    primary_entity_id VARCHAR(220),
    sha256 CHAR(64) NOT NULL,
    byte_size BIGINT NOT NULL,
    is_synthetic BOOLEAN NOT NULL,
    rag_index_allowed BOOLEAN NOT NULL DEFAULT TRUE,
    indexed_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    dataset_as_of DATE NOT NULL
);

CREATE TABLE sys_rejected_record (
    rejection_key CHAR(64) PRIMARY KEY,
    source_file TEXT NOT NULL,
    source_row_number BIGINT,
    source_record_id TEXT,
    rejection_reason TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Phase 2-ready entities.
CREATE TABLE dim_supplier (
    supplier_id VARCHAR(160) PRIMARY KEY,
    supplier_name TEXT NOT NULL,
    supplier_type VARCHAR(80),
    risk_tier VARCHAR(40),
    country TEXT,
    is_synthetic BOOLEAN NOT NULL,
    provenance JSONB NOT NULL,
    dataset_as_of DATE NOT NULL
);

CREATE TABLE supplier_product (
    supplier_id VARCHAR(160) NOT NULL REFERENCES dim_supplier(supplier_id),
    product_key CHAR(64) NOT NULL REFERENCES dim_product(product_key),
    supplier_sku TEXT,
    is_primary_supplier BOOLEAN,
    provenance JSONB NOT NULL,
    PRIMARY KEY (supplier_id, product_key)
);

CREATE TABLE purchase_order (
    po_id VARCHAR(160) PRIMARY KEY,
    supplier_id VARCHAR(160) NOT NULL REFERENCES dim_supplier(supplier_id),
    order_date DATE REFERENCES dim_date(date_key),
    required_date DATE REFERENCES dim_date(date_key),
    ship_to_location_key CHAR(64) REFERENCES dim_location(location_key),
    currency_code VARCHAR(20),
    status VARCHAR(50),
    payment_terms TEXT,
    is_synthetic BOOLEAN NOT NULL,
    provenance JSONB NOT NULL,
    dataset_as_of DATE NOT NULL
);

CREATE TABLE purchase_order_line (
    po_line_id VARCHAR(200) PRIMARY KEY,
    po_id VARCHAR(160) NOT NULL REFERENCES purchase_order(po_id) ON DELETE CASCADE,
    product_key CHAR(64) REFERENCES dim_product(product_key),
    supplier_sku TEXT,
    ordered_quantity NUMERIC(18,4) NOT NULL,
    unit_price NUMERIC(18,4),
    currency_code VARCHAR(20),
    provenance JSONB NOT NULL
);

CREATE TABLE shipment (
    shipment_id VARCHAR(160) PRIMARY KEY,
    po_id VARCHAR(160) REFERENCES purchase_order(po_id),
    carrier_partner_key CHAR(64) REFERENCES dim_partner(partner_key),
    origin_location_key CHAR(64) REFERENCES dim_location(location_key),
    destination_location_key CHAR(64) REFERENCES dim_location(location_key),
    planned_departure TIMESTAMP,
    actual_departure TIMESTAMP,
    planned_arrival TIMESTAMP,
    actual_arrival TIMESTAMP,
    status VARCHAR(50),
    is_synthetic BOOLEAN NOT NULL,
    provenance JSONB NOT NULL,
    dataset_as_of DATE NOT NULL
);

CREATE TABLE shipment_container (
    shipment_id VARCHAR(160) NOT NULL REFERENCES shipment(shipment_id) ON DELETE CASCADE,
    container_number VARCHAR(100) NOT NULL,
    container_tracking_key CHAR(64) REFERENCES fact_container_tracking(container_tracking_key),
    seal_number TEXT,
    package_count INTEGER,
    cargo_description TEXT,
    gross_weight_kg NUMERIC(18,4),
    volume_cbm NUMERIC(18,4),
    provenance JSONB NOT NULL,
    PRIMARY KEY (shipment_id, container_number)
);

CREATE TABLE goods_receipt (
    receipt_id VARCHAR(160) PRIMARY KEY,
    po_line_id VARCHAR(200) NOT NULL REFERENCES purchase_order_line(po_line_id),
    location_key CHAR(64) REFERENCES dim_location(location_key),
    receipt_date DATE REFERENCES dim_date(date_key),
    received_quantity NUMERIC(18,4) NOT NULL,
    rejected_quantity NUMERIC(18,4),
    provenance JSONB NOT NULL
);

CREATE TABLE contract (
    contract_id VARCHAR(160) PRIMARY KEY,
    supplier_id VARCHAR(160) REFERENCES dim_supplier(supplier_id),
    carrier_partner_key CHAR(64) REFERENCES dim_partner(partner_key),
    contract_type VARCHAR(80) NOT NULL,
    effective_date DATE REFERENCES dim_date(date_key),
    expiry_date DATE REFERENCES dim_date(date_key),
    currency_code VARCHAR(20),
    status VARCHAR(50),
    is_synthetic BOOLEAN NOT NULL,
    provenance JSONB NOT NULL,
    dataset_as_of DATE NOT NULL,
    CHECK (supplier_id IS NOT NULL OR carrier_partner_key IS NOT NULL)
);

CREATE TABLE contract_clause (
    clause_id VARCHAR(200) PRIMARY KEY,
    contract_id VARCHAR(160) NOT NULL REFERENCES contract(contract_id) ON DELETE CASCADE,
    clause_type VARCHAR(80) NOT NULL,
    clause_title TEXT,
    clause_text TEXT NOT NULL,
    structured_terms JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL
);

CREATE TABLE warehouse_audit (
    audit_id VARCHAR(160) PRIMARY KEY,
    location_key CHAR(64) NOT NULL REFERENCES dim_location(location_key),
    audit_date DATE REFERENCES dim_date(date_key),
    audit_type VARCHAR(80) NOT NULL,
    auditor_reference TEXT,
    status VARCHAR(50),
    is_synthetic BOOLEAN NOT NULL,
    provenance JSONB NOT NULL
);

CREATE TABLE audit_finding (
    finding_id VARCHAR(200) PRIMARY KEY,
    audit_id VARCHAR(160) NOT NULL REFERENCES warehouse_audit(audit_id) ON DELETE CASCADE,
    finding_text TEXT NOT NULL,
    priority VARCHAR(20),
    required_action TEXT,
    due_date DATE REFERENCES dim_date(date_key),
    provenance JSONB NOT NULL
);

CREATE TABLE customs_entry (
    customs_entry_id VARCHAR(160) PRIMARY KEY,
    shipment_id VARCHAR(160) NOT NULL REFERENCES shipment(shipment_id),
    entry_date DATE REFERENCES dim_date(date_key),
    tariff_code TEXT,
    customs_value NUMERIC(18,4),
    duty_amount NUMERIC(18,4),
    currency_code VARCHAR(20),
    clearance_status VARCHAR(50),
    provenance JSONB NOT NULL
);

CREATE TABLE enterprise_policy (
    policy_id VARCHAR(160) PRIMARY KEY,
    policy_type VARCHAR(80) NOT NULL,
    title TEXT NOT NULL,
    body_text TEXT NOT NULL,
    effective_date DATE REFERENCES dim_date(date_key),
    review_date DATE REFERENCES dim_date(date_key),
    version VARCHAR(40) NOT NULL,
    is_synthetic BOOLEAN NOT NULL,
    provenance JSONB NOT NULL
);

CREATE TABLE restricted.customer_contact (
    contact_id VARCHAR(160) PRIMARY KEY,
    source_system VARCHAR(50) NOT NULL,
    source_customer_id VARCHAR(100) NOT NULL,
    customer_name TEXT,
    email TEXT,
    email_status VARCHAR(40),
    email_shared_customer_count INTEGER,
    email_send_eligible BOOLEAN NOT NULL,
    phone TEXT,
    phone_send_eligible BOOLEAN NOT NULL,
    street_address TEXT,
    city TEXT,
    state_region TEXT,
    postal_code TEXT,
    country TEXT,
    pii_classification VARCHAR(50) NOT NULL,
    rag_index_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    source_file TEXT NOT NULL,
    UNIQUE (source_system, source_customer_id)
);

CREATE INDEX idx_order_order_id ON fact_order_fulfillment (source_system, order_id);
CREATE INDEX idx_order_date ON fact_order_fulfillment (order_date);
CREATE INDEX idx_order_customer ON fact_order_fulfillment (customer_key);
CREATE INDEX idx_order_product ON fact_order_fulfillment (product_key);
CREATE INDEX idx_delivery_order ON fact_delivery (source_system, order_id);
CREATE INDEX idx_delivery_status ON fact_delivery (delivery_status);
CREATE INDEX idx_inventory_product_date ON fact_inventory_event (product_key, event_date);
CREATE INDEX idx_freight_order_date ON fact_freight_order (order_date);
CREATE INDEX idx_container_ports ON fact_container_tracking (origin_port_key, destination_port_key);
CREATE INDEX idx_shipment_date ON fact_shipment_record (shipment_date);
CREATE INDEX idx_feedback_order ON fact_customer_feedback (source_system, order_id);
CREATE INDEX idx_email_entity ON email_thread (primary_entity_type, primary_entity_id);
CREATE INDEX idx_email_source ON email_thread (source_system, business_domain);
CREATE INDEX idx_document_entity ON document_index (primary_entity_type, primary_entity_id);
CREATE INDEX idx_document_rag ON document_index (rag_index_allowed, document_type);

REVOKE ALL ON SCHEMA restricted FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA restricted FROM PUBLIC;

COMMENT ON SCHEMA skope IS 'Canonical SKOPE analytical and operational schema';
COMMENT ON SCHEMA restricted IS 'Operational PII; never embed or expose to ordinary RAG/SQL-agent roles';
COMMENT ON TABLE fact_order_fulfillment IS 'One row per canonical source order line; DataCo grain is Order Item Id';
COMMENT ON TABLE fact_container_tracking IS 'Container facts remain unlinked to orders unless an explicit source-backed relationship exists';
COMMENT ON TABLE document_index IS 'RAG document metadata and lineage; vector content is stored separately';
