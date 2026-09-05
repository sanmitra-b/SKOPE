BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.supplier_performance AS
SELECT
    s.supplier_id,
    s.supplier_name,
    s.supplier_type,
    s.risk_tier,
    s.country,
    count(DISTINCT po.po_id) AS purchase_order_count,
    count(DISTINCT sh.shipment_id) AS shipment_count,
    count(DISTINCT sh.shipment_id) FILTER (
        WHERE sh.actual_arrival IS NOT NULL
          AND sh.planned_arrival IS NOT NULL
          AND sh.actual_arrival > sh.planned_arrival
    ) AS late_shipment_count,
    round(
        100.0 * count(DISTINCT sh.shipment_id) FILTER (
            WHERE sh.actual_arrival IS NOT NULL
              AND sh.planned_arrival IS NOT NULL
              AND sh.actual_arrival <= sh.planned_arrival
        ) / NULLIF(count(DISTINCT sh.shipment_id) FILTER (WHERE sh.actual_arrival IS NOT NULL), 0),
        2
    ) AS on_time_percentage,
    count(DISTINCT po.po_id) FILTER (
        WHERE upper(coalesce(po.status, '')) NOT IN ('CLOSED', 'COMPLETED', 'CANCELLED')
    ) AS open_purchase_order_count,
    max(GREATEST(po.dataset_as_of, sh.dataset_as_of)) AS data_as_of
FROM skope.dim_supplier s
LEFT JOIN skope.purchase_order po ON po.supplier_id = s.supplier_id
LEFT JOIN skope.shipment sh ON sh.po_id = po.po_id
GROUP BY s.supplier_id, s.supplier_name, s.supplier_type, s.risk_tier, s.country;

CREATE OR REPLACE VIEW analytics.shipment_operations AS
SELECT
    sh.shipment_id,
    sh.po_id,
    po.supplier_id,
    supplier.supplier_name,
    carrier.partner_name AS carrier_name,
    origin.facility_name AS origin_facility,
    origin.city AS origin_city,
    destination.facility_name AS destination_facility,
    destination.city AS destination_city,
    sh.planned_departure,
    sh.actual_departure,
    sh.planned_arrival,
    sh.actual_arrival,
    sh.status,
    CASE
        WHEN sh.actual_arrival IS NOT NULL AND sh.planned_arrival IS NOT NULL
        THEN sh.actual_arrival > sh.planned_arrival
        ELSE NULL
    END AS is_late,
    CASE
        WHEN sh.actual_arrival IS NOT NULL AND sh.planned_arrival IS NOT NULL
        THEN round(extract(epoch FROM (sh.actual_arrival - sh.planned_arrival)) / 86400.0, 2)
        ELSE NULL
    END AS arrival_variance_days,
    sh.dataset_as_of AS data_as_of
FROM skope.shipment sh
LEFT JOIN skope.purchase_order po ON po.po_id = sh.po_id
LEFT JOIN skope.dim_supplier supplier ON supplier.supplier_id = po.supplier_id
LEFT JOIN skope.dim_partner carrier ON carrier.partner_key = sh.carrier_partner_key
LEFT JOIN skope.dim_location origin ON origin.location_key = sh.origin_location_key
LEFT JOIN skope.dim_location destination ON destination.location_key = sh.destination_location_key;

CREATE OR REPLACE VIEW analytics.purchase_order_lines AS
SELECT
    po.po_id,
    po.supplier_id,
    supplier.supplier_name,
    po.order_date,
    po.required_date,
    po.status,
    po.payment_terms,
    location.facility_name AS ship_to_facility,
    line.po_line_id,
    product.source_product_id,
    product.product_name,
    product.category,
    line.supplier_sku,
    line.ordered_quantity,
    line.unit_price,
    line.currency_code,
    line.ordered_quantity * line.unit_price AS line_value,
    coalesce(sum(receipt.received_quantity), 0) AS received_quantity,
    coalesce(sum(receipt.rejected_quantity), 0) AS rejected_quantity,
    line.ordered_quantity - coalesce(sum(receipt.received_quantity), 0) AS open_quantity,
    po.dataset_as_of AS data_as_of
FROM skope.purchase_order po
JOIN skope.purchase_order_line line ON line.po_id = po.po_id
JOIN skope.dim_supplier supplier ON supplier.supplier_id = po.supplier_id
LEFT JOIN skope.dim_product product ON product.product_key = line.product_key
LEFT JOIN skope.dim_location location ON location.location_key = po.ship_to_location_key
LEFT JOIN skope.goods_receipt receipt ON receipt.po_line_id = line.po_line_id
GROUP BY po.po_id, po.supplier_id, supplier.supplier_name, po.order_date, po.required_date,
         po.status, po.payment_terms, location.facility_name, line.po_line_id,
         product.source_product_id, product.product_name, product.category,
         line.supplier_sku, line.ordered_quantity, line.unit_price, line.currency_code,
         po.dataset_as_of;

CREATE OR REPLACE VIEW analytics.sales_orders AS
SELECT
    order_line.source_system,
    order_line.display_order_id,
    order_line.order_id,
    order_line.order_date,
    product.source_product_id,
    product.product_name,
    product.category,
    order_line.quantity,
    order_line.gross_amount_original AS gross_sales,
    order_line.discount_original AS discount_amount,
    order_line.net_amount_original AS net_sales,
    order_line.currency_code,
    order_line.order_status,
    order_line.dataset_as_of AS data_as_of
FROM skope.fact_order_fulfillment order_line
LEFT JOIN skope.dim_product product ON product.product_key = order_line.product_key;

CREATE OR REPLACE VIEW analytics.current_inventory AS
WITH latest AS (
    SELECT
        event.*,
        row_number() OVER (
            PARTITION BY event.product_key, event.location_key
            ORDER BY event.event_date DESC, event.inventory_event_key DESC
        ) AS row_number
    FROM skope.fact_inventory_event event
    WHERE event.quantity_on_hand IS NOT NULL
)
SELECT
    product.source_product_id,
    product.product_name,
    product.category,
    location.source_location_id,
    location.facility_name,
    location.city,
    latest.event_date,
    latest.quantity_on_hand,
    latest.quantity_damaged,
    latest.dataset_as_of AS data_as_of
FROM latest
JOIN skope.dim_product product ON product.product_key = latest.product_key
LEFT JOIN skope.dim_location location ON location.location_key = latest.location_key
WHERE latest.row_number = 1;

CREATE OR REPLACE VIEW analytics.delivery_performance AS
SELECT
    delivery.display_order_id,
    delivery.order_id,
    partner.partner_name AS delivery_partner,
    location.facility_name AS origin_facility,
    delivery.promised_time,
    delivery.actual_time,
    delivery.delivery_time_minutes,
    delivery.distance_km,
    delivery.delivery_status,
    delivery.reason_if_delayed,
    delivery.delivery_mode,
    delivery.is_late,
    delivery.dataset_as_of AS data_as_of
FROM skope.fact_delivery delivery
LEFT JOIN skope.dim_partner partner ON partner.partner_key = delivery.partner_key
LEFT JOIN skope.dim_location location ON location.location_key = delivery.origin_location_key;

CREATE OR REPLACE VIEW analytics.container_status AS
SELECT
    tracking.container_number,
    tracking.vessel_name,
    tracking.freight_forwarder,
    tracking.linked_order_id,
    origin.facility_name AS origin_port,
    destination.facility_name AS destination_port,
    tracking.dispatch_date,
    tracking.port_loading_date,
    tracking.port_discharge_date,
    tracking.last_tracked_at,
    tracking.delivered_flag,
    tracking.delivered_date,
    tracking.dataset_as_of AS data_as_of
FROM skope.fact_container_tracking tracking
LEFT JOIN skope.dim_location origin ON origin.location_key = tracking.origin_port_key
LEFT JOIN skope.dim_location destination ON destination.location_key = tracking.destination_port_key;

CREATE OR REPLACE VIEW analytics.contract_terms AS
SELECT
    contract.contract_id,
    contract.contract_type,
    supplier.supplier_id,
    supplier.supplier_name,
    carrier.partner_name AS carrier_name,
    contract.effective_date,
    contract.expiry_date,
    contract.status,
    clause.clause_id,
    clause.clause_type,
    clause.clause_title,
    clause.clause_text,
    clause.structured_terms,
    contract.dataset_as_of AS data_as_of
FROM skope.contract contract
LEFT JOIN skope.dim_supplier supplier ON supplier.supplier_id = contract.supplier_id
LEFT JOIN skope.dim_partner carrier ON carrier.partner_key = contract.carrier_partner_key
LEFT JOIN skope.contract_clause clause ON clause.contract_id = contract.contract_id;

GRANT USAGE ON SCHEMA analytics TO skope_rag_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO skope_rag_reader;

COMMIT;
