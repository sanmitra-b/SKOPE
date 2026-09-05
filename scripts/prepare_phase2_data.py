"""Create deterministic, explicitly synthetic Phase 2 enterprise rows in PostgreSQL."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import random

import pandas as pd

import ingest_to_db as db


SEED = 20260823
VERSION = "phase2-synthetic-v1"
TRANSFORM = "1.0.0-phase2"

SUPPLIERS = [
    ("SUP-FARMFRESH", "FarmFresh Agro Suppliers", "FOOD_SUPPLIER", "IND", "LOW"),
    ("SUP-TECHHUB", "Shenzhen TechHub Manufacturing", "TECHNOLOGY_SUPPLIER", "CHN", "MEDIUM"),
    ("SUP-APEX", "Apex Packaging Solutions", "PACKAGING_SUPPLIER", "IND", "LOW"),
    ("SUP-NORTHSTAR", "Northstar Precision Components", "INDUSTRIAL_SUPPLIER", "IND", "LOW"),
    ("SUP-BLUEHAVEN", "Bluehaven Industrial Components", "INDUSTRIAL_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-GREENFIELD", "Greenfield Cold Chain Foods", "FOOD_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-ORBITAL", "Orbital Electronics Manufacturing", "TECHNOLOGY_SUPPLIER", "MYS", "MEDIUM"),
    ("SUP-PACIFICA", "Pacifica Home Goods", "CONSUMER_GOODS_SUPPLIER", "VNM", "LOW"),
    ("SUP-IRONBRIDGE", "Ironbridge Metals and Fabrication", "INDUSTRIAL_SUPPLIER", "IND", "HIGH"),
    ("SUP-CLEARWATER", "Clearwater Hygiene Products", "CONSUMER_GOODS_SUPPLIER", "IND", "LOW"),
    ("SUP-SUNCREST", "Suncrest Renewable Packaging", "PACKAGING_SUPPLIER", "IND", "LOW"),
    ("SUP-AURORA", "Aurora Medical Supplies", "HEALTHCARE_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-REDWOOD", "Redwood Furniture Works", "CONSUMER_GOODS_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-CASCADE", "Cascade Apparel Manufacturing", "APPAREL_SUPPLIER", "BGD", "MEDIUM"),
    ("SUP-HARBOR", "Harbor Industrial Tools", "INDUSTRIAL_SUPPLIER", "IND", "LOW"),
    ("SUP-SILVERLINE", "Silverline Consumer Electronics", "TECHNOLOGY_SUPPLIER", "CHN", "HIGH"),
    ("SUP-MEADOW", "Meadow Dairy Cooperative", "FOOD_SUPPLIER", "IND", "LOW"),
    ("SUP-CEDAR", "Cedar Paper and Board", "PACKAGING_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-TRIDENT", "Trident Auto Components", "INDUSTRIAL_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-VANTAGE", "Vantage Sports Equipment", "CONSUMER_GOODS_SUPPLIER", "IND", "LOW"),
    ("SUP-LUMINA", "Lumina Lighting Systems", "TECHNOLOGY_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-MARINER", "Mariner Export Foods", "FOOD_SUPPLIER", "THA", "MEDIUM"),
    ("SUP-PINNACLE", "Pinnacle Office Products", "CONSUMER_GOODS_SUPPLIER", "IND", "LOW"),
    ("SUP-VERDANT", "Verdant Personal Care", "CONSUMER_GOODS_SUPPLIER", "IND", "LOW"),
    ("SUP-ATLAS", "Atlas Material Handling", "INDUSTRIAL_SUPPLIER", "IND", "HIGH"),
    ("SUP-NIMBUS", "Nimbus Cloud Devices", "TECHNOLOGY_SUPPLIER", "TWN", "MEDIUM"),
    ("SUP-RIVERSTONE", "Riverstone Kitchenware", "CONSUMER_GOODS_SUPPLIER", "IND", "LOW"),
    ("SUP-SAFFRON", "Saffron Spice Exports", "FOOD_SUPPLIER", "IND", "MEDIUM"),
    ("SUP-QUANTUM", "Quantum Mobility Parts", "INDUSTRIAL_SUPPLIER", "IND", "HIGH"),
    ("SUP-ECOWRAP", "EcoWrap Flexible Packaging", "PACKAGING_SUPPLIER", "IND", "LOW"),
]


def clamp_day(value: datetime | None, fallback: date) -> date:
    if value is None:
        return fallback
    result = value.date()
    return min(max(result, date(2024, 1, 1)), date(2026, 7, 31))


def reset_phase2(conn) -> None:
    conn.execute("DELETE FROM skope.document_index WHERE metadata->>'phase'='PHASE2'")
    for table in (
        "customs_entry", "audit_finding", "warehouse_audit", "contract_clause", "contract",
        "goods_receipt", "shipment_container", "shipment", "purchase_order_line",
        "purchase_order", "supplier_product", "dim_supplier", "enterprise_policy",
    ):
        conn.execute(f"DELETE FROM skope.{table}")
    conn.execute("DELETE FROM skope.dim_partner WHERE source_system='Phase2Synthetic'")
    conn.commit()


def load_phase2(database_url: str, replace: bool) -> dict[str, int]:
    if not replace:
        raise SystemExit("Use --replace to explicitly replace only the Phase 2 synthetic layer")
    rng = random.Random(SEED)
    with db.psycopg.connect(database_url) as conn:
        reset_phase2(conn)
        run_id = conn.execute(
            "INSERT INTO skope.etl_run(dataset_version,transformation_version,source_root,status) "
            "VALUES (%s,%s,%s,'RUNNING') RETURNING run_id",
            (VERSION, TRANSFORM, "PostgreSQL Phase 1 + deterministic Phase 2 policy"),
        ).fetchone()[0]
        conn.commit()
        try:
            products = conn.execute("""
                SELECT product_key, source_system, source_product_id, product_name, category,
                       coalesce(unit_price_original, mrp_original, 100)::numeric
                FROM skope.dim_product
                WHERE source_system IN ('Blinkit','IndianEComm','DataCo')
                ORDER BY source_system, source_product_id
                LIMIT 900
            """).fetchall()
            locations = conn.execute("""
                SELECT location_key, source_system, source_location_id, facility_name, location_type
                FROM skope.dim_location WHERE location_type IN ('DC_PLANT','DARK_STORE')
                ORDER BY location_type, source_location_id LIMIT 120
            """).fetchall()
            containers = conn.execute("""
                SELECT c.container_tracking_key,c.container_number,c.origin_port_key,c.destination_port_key,
                       c.dispatch_date,c.port_loading_date,c.port_discharge_date,c.post_discharge_date,
                       c.delivered_date,c.delivered_flag,c.freight_forwarder,p.partner_key
                FROM skope.fact_container_tracking c
                LEFT JOIN skope.dim_partner p
                  ON p.source_system='ContainerTracking' AND p.source_partner_id=c.freight_forwarder
                WHERE c.origin_port_key IS NOT NULL AND c.destination_port_key IS NOT NULL
                ORDER BY c.container_number LIMIT 1200
            """).fetchall()
            carriers = conn.execute("""
                SELECT partner_key,source_partner_id,partner_name FROM skope.dim_partner
                WHERE partner_type='CARRIER' ORDER BY source_partner_id
            """).fetchall()
            if len(products) < 600 or len(locations) < 20 or len(containers) < 1200 or not carriers:
                raise RuntimeError("Phase 1 warehouse does not have enough canonical rows for Phase 2 generation")

            dhl_key = db.stable_key("Phase2Synthetic", "partner", "DHL-AIR-DEMO")
            partner_frame = pd.DataFrame([{
                "partner_key": dhl_key, "source_system": "Phase2Synthetic",
                "source_partner_id": "DHL-AIR-DEMO", "partner_name": "DHL Express",
                "partner_type": "CARRIER", "attributes": json.dumps({"authorized_logo": "DHL Global.png"}),
                "is_synthetic": True, "mapping_method": "EXPLICIT_DEMO_ENTITY",
                "transformation_version": TRANSFORM, "dataset_as_of": date(2026, 7, 31),
            }])
            db.copy_frame(conn, "skope.dim_partner", partner_frame)

            supplier_frame = pd.DataFrame([{
                "supplier_id": supplier_id, "supplier_name": name, "supplier_type": supplier_type,
                "risk_tier": risk, "country": country, "is_synthetic": True,
                "provenance": json.dumps({"phase": "PHASE2", "method": "EXPLICIT_SYNTHETIC_MASTER", "seed": SEED}),
                "dataset_as_of": date(2026, 7, 31),
            } for supplier_id, name, supplier_type, country, risk in SUPPLIERS])
            db.copy_frame(conn, "skope.dim_supplier", supplier_frame)

            supplier_products = []
            for supplier_index, supplier in enumerate(SUPPLIERS):
                for product in products[supplier_index * 20:(supplier_index + 1) * 20]:
                    supplier_products.append({
                        "supplier_id": supplier[0], "product_key": product[0],
                        "supplier_sku": f"{supplier[0].removeprefix('SUP-')}-{product[2]}",
                        "is_primary_supplier": True,
                        "provenance": json.dumps({"phase": "PHASE2", "method": "DETERMINISTIC_SYNTHETIC_ASSOCIATION"}),
                    })
            supplier_product_frame = pd.DataFrame(supplier_products)
            db.copy_frame(conn, "skope.supplier_product", supplier_product_frame)

            container_dates: dict[int, tuple[date, date]] = {}
            for index, container in enumerate(containers):
                departure = container[5] or container[4]
                arrival = container[8] or container[7] or container[6]
                required = clamp_day(arrival, date(2026, 6, 30))
                ordered = max(date(2024, 1, 1), clamp_day(departure, required) - timedelta(days=30))
                container_dates[index] = (ordered, max(ordered, required))

            purchase_orders = []
            po_lines = []
            receipts = []
            for index in range(1600):
                po_id = f"PO-P2-{index + 1:05d}"
                supplier = SUPPLIERS[index % len(SUPPLIERS)]
                location = locations[index % len(locations)]
                if index < len(containers):
                    order_day, required_day = container_dates[index]
                else:
                    order_day = date(2025, 1, 1) + timedelta(days=(index * 7) % 520)
                    required_day = min(order_day + timedelta(days=21), date(2026, 7, 31))
                currency = "USD" if supplier[3] in {"CHN", "MYS", "VNM", "THA", "TWN", "BGD"} else "INR"
                purchase_orders.append({
                    "po_id": po_id, "supplier_id": supplier[0], "order_date": order_day,
                    "required_date": required_day, "ship_to_location_key": location[0],
                    "currency_code": currency, "status": "CLOSED" if index < 1400 else "OPEN",
                    "payment_terms": "Net 30 days", "is_synthetic": True,
                    "provenance": json.dumps({"phase": "PHASE2", "method": "DETERMINISTIC_SYNTHETIC_TRANSACTION", "seed": SEED}),
                    "dataset_as_of": date(2026, 7, 31),
                })
                supplier_slice = products[(index % 30) * 20:((index % 30) + 1) * 20]
                for line_no in range(1, 4):
                    product = supplier_slice[(index + line_no) % len(supplier_slice)]
                    line_id = f"{po_id}-L{line_no:02d}"
                    quantity = 10 + ((index * 11 + line_no * 7) % 190)
                    base_price = float(product[5])
                    unit_price = round(max(5.0, base_price) * (0.82 + line_no * 0.03), 2)
                    po_lines.append({
                        "po_line_id": line_id, "po_id": po_id, "product_key": product[0],
                        "supplier_sku": f"{supplier[0].removeprefix('SUP-')}-{product[2]}",
                        "ordered_quantity": quantity, "unit_price": unit_price, "currency_code": currency,
                        "provenance": json.dumps({"phase": "PHASE2", "method": "PRICE_DERIVED_FROM_SOURCE_PRODUCT", "source_product": product[2]}),
                    })
                    shortage = (index + line_no) % 7 == 0
                    received = quantity - (1 + (index % min(quantity, 12))) if shortage else quantity
                    receipts.append({
                        "receipt_id": f"GR-P2-{index + 1:05d}-{line_no:02d}", "po_line_id": line_id,
                        "location_key": location[0], "receipt_date": required_day,
                        "received_quantity": max(received, 0), "rejected_quantity": 0 if not shortage else index % 3,
                        "provenance": json.dumps({"phase": "PHASE2", "method": "DETERMINISTIC_SYNTHETIC_RECEIPT", "shortage_recorded": shortage,
                                                  "root_cause": "Supplier quantity variance" if shortage else None}),
                    })
            db.copy_frame(conn, "skope.purchase_order", pd.DataFrame(purchase_orders))
            db.copy_frame(conn, "skope.purchase_order_line", pd.DataFrame(po_lines))

            v44 = next((row for row in carriers if row[1] == "V44_3"), carriers[0])
            shipments = []
            shipment_containers = []
            for index, container in enumerate(containers):
                po_id = f"PO-P2-{index + 1:05d}"
                order_day, required_day = container_dates[index]
                actual_departure = container[5] or container[4] or datetime.combine(order_day + timedelta(days=5), datetime.min.time())
                actual_arrival = container[8] or container[7] or container[6]
                if index % 20 == 0:
                    carrier_key = v44[0]
                elif index % 20 == 1:
                    carrier_key = dhl_key
                else:
                    carrier_key = container[11] or carriers[index % len(carriers)][0]
                shipment_id = f"SHP-P2-{index + 1:05d}"
                shipments.append({
                    "shipment_id": shipment_id, "po_id": po_id, "carrier_partner_key": carrier_key,
                    "origin_location_key": container[2], "destination_location_key": container[3],
                    "planned_departure": actual_departure - timedelta(days=1), "actual_departure": actual_departure,
                    "planned_arrival": datetime.combine(required_day, datetime.min.time()), "actual_arrival": actual_arrival,
                    "status": "DELIVERED" if container[9] is True else "IN_TRANSIT",
                    "is_synthetic": True,
                    "provenance": json.dumps({"phase": "PHASE2", "method": "EXPLICIT_SYNTHETIC_RELATIONSHIP",
                                              "source_container_tracking_key": container[0]}),
                    "dataset_as_of": date(2026, 7, 31),
                })
                primary_product = products[(index % 30) * 20 + ((index + 1) % 20)]
                shipment_containers.append({
                    "shipment_id": shipment_id, "container_number": container[1],
                    "container_tracking_key": container[0], "seal_number": f"SL{index + 100000:06d}",
                    "package_count": 20 + index % 180, "cargo_description": primary_product[3],
                    "gross_weight_kg": round(500 + (index % 100) * 37.5, 2),
                    "volume_cbm": round(8 + (index % 40) * 0.65, 2),
                    "provenance": json.dumps({"phase": "PHASE2", "method": "SYNTHETIC_CARGO_ON_SOURCE_CONTAINER"}),
                })
            db.copy_frame(conn, "skope.shipment", pd.DataFrame(shipments))
            db.copy_frame(conn, "skope.shipment_container", pd.DataFrame(shipment_containers))
            db.copy_frame(conn, "skope.goods_receipt", pd.DataFrame(receipts))

            contracts = []
            clauses = []
            clause_templates = {
                "3PL_WAREHOUSING": [
                    ("SCOPE", "Storage and Handling", "Provider shall receive, store, pick and release goods against authorized instructions.", {}),
                    ("SLA", "Service Level", "Inbound receipts shall be posted within 24 hours of physical acceptance.", {"hours": 24}),
                    ("PENALTY", "Delay Service Credit", "A service credit of INR 25000 applies for each verified day beyond the SLA.", {"rate": 25000, "currency": "INR", "unit": "DAY"}),
                    ("LIABILITY", "Liability Cap", "Aggregate liability is capped at the fees paid during the preceding six months.", {"cap_months": 6}),
                    ("TERMINATION", "Termination", "Either party may terminate with 60 days written notice.", {"notice_days": 60}),
                ],
                "VENDOR_SUPPLY": [
                    ("SCOPE", "Scope of Supply", "Supplier shall provide the approved products and quantities stated in purchase orders.", {}),
                    ("QUALITY", "Quality Standard", "Products must conform to specifications and inspection requirements in each purchase order.", {}),
                    ("SLA", "Delivery Schedule", "Supplier shall deliver by the required date stated on the purchase order.", {}),
                    ("PENALTY", "Late Delivery Credit", "A service credit equal to 1 percent of the delayed line value applies per verified week, capped at 5 percent.", {"rate_percent": 1, "unit": "WEEK", "cap_percent": 5}),
                    ("PAYMENT", "Payment Terms", "Undisputed invoices are payable within 30 days of acceptance.", {"days": 30}),
                ],
                "CARRIER_MSA": [
                    ("SCOPE", "Trade Lane Services", "Carrier shall provide booked transport services on the lanes stated in each shipment instruction.", {}),
                    ("SLA", "Transit Commitment", "Transit performance is measured against planned arrival recorded for each shipment.", {}),
                    ("DEMURRAGE", "Demurrage and Detention", "USD 150 per container day applies after seven free days where contractually attributable.", {"rate": 150, "currency": "USD", "free_days": 7}),
                    ("LIABILITY", "Cargo Liability", "Liability is determined under the applicable carriage convention and declared cargo value.", {}),
                    ("DISPUTE", "Dispute Resolution", "Operational disputes shall be escalated within ten business days with supporting records.", {"days": 10}),
                ],
            }
            all_carriers = [v44, (dhl_key, "DHL-AIR-DEMO", "DHL Express")] + carriers[:28]
            for index in range(300):
                kind = "3PL_WAREHOUSING" if index < 100 else "VENDOR_SUPPLY" if index < 200 else "CARRIER_MSA"
                contract_id = f"CON-P2-{index + 1:04d}"
                supplier_id = SUPPLIERS[index % 30][0] if kind != "CARRIER_MSA" else None
                carrier_key = all_carriers[index % len(all_carriers)][0] if kind == "CARRIER_MSA" else None
                effective = date(2025, 1, 1) + timedelta(days=index % 180)
                expiry = min(effective + timedelta(days=365), date(2026, 7, 31))
                contracts.append({
                    "contract_id": contract_id, "supplier_id": supplier_id, "carrier_partner_key": carrier_key,
                    "contract_type": kind, "effective_date": effective, "expiry_date": expiry,
                    "currency_code": "USD" if kind == "CARRIER_MSA" else "INR", "status": "ACTIVE",
                    "is_synthetic": True,
                    "provenance": json.dumps({"phase": "PHASE2", "method": "EXPLICIT_SYNTHETIC_POLICY", "seed": SEED}),
                    "dataset_as_of": date(2026, 7, 31),
                })
                for clause_no, (clause_type, title, clause_text, terms) in enumerate(clause_templates[kind], start=1):
                    clauses.append({
                        "clause_id": f"{contract_id}-CL{clause_no:02d}", "contract_id": contract_id,
                        "clause_type": clause_type, "clause_title": title, "clause_text": clause_text,
                        "structured_terms": json.dumps(terms),
                        "provenance": json.dumps({"phase": "PHASE2", "method": "APPROVED_SYNTHETIC_CLAUSE_TEMPLATE"}),
                    })
            db.copy_frame(conn, "skope.contract", pd.DataFrame(contracts))
            db.copy_frame(conn, "skope.contract_clause", pd.DataFrame(clauses))

            audits = []
            findings = []
            for index in range(100):
                location = locations[index % len(locations)]
                audit_id = f"AUD-P2-{index + 1:04d}"
                audit_day = date(2026, 1, 5) + timedelta(days=(index * 5) % 200)
                audits.append({
                    "audit_id": audit_id, "location_key": location[0], "audit_date": audit_day,
                    "audit_type": "SAFETY_RISK" if index < 80 else "INVENTORY_CONTROL",
                    "auditor_reference": f"AUDITOR-{index % 8 + 1:02d}", "status": "CLOSED",
                    "is_synthetic": True,
                    "provenance": json.dumps({"phase": "PHASE2", "method": "EXPLICIT_SYNTHETIC_AUDIT"}),
                })
                finding_texts = [
                    ("Emergency aisle marking requires refresh", "MEDIUM", "Repaint floor markings and verify clearance"),
                    ("Cycle-count variance exceeded local tolerance", "HIGH", "Recount affected bins and review adjustments"),
                    ("Visitor log review was not completed on schedule", "LOW", "Assign weekly security log review"),
                ]
                for finding_no, (finding, priority, action) in enumerate(finding_texts, start=1):
                    findings.append({
                        "finding_id": f"{audit_id}-F{finding_no:02d}", "audit_id": audit_id,
                        "finding_text": finding, "priority": priority, "required_action": action,
                        "due_date": min(audit_day + timedelta(days=14 * finding_no), date(2026, 7, 31)),
                        "provenance": json.dumps({"phase": "PHASE2", "method": "APPROVED_SYNTHETIC_FINDING_TEMPLATE"}),
                    })
            db.copy_frame(conn, "skope.warehouse_audit", pd.DataFrame(audits))
            db.copy_frame(conn, "skope.audit_finding", pd.DataFrame(findings))

            customs = []
            for index in range(200):
                line_slice = po_lines[index * 3:index * 3 + 3]
                value = round(sum(row["ordered_quantity"] * row["unit_price"] for row in line_slice), 2)
                entry_day = min(container_dates[index][1], date(2026, 7, 31))
                customs.append({
                    "customs_entry_id": f"CST-P2-{index + 1:05d}", "shipment_id": f"SHP-P2-{index + 1:05d}",
                    "entry_date": entry_day, "tariff_code": f"{8400 + index % 120:04d}.{index % 90:02d}",
                    "customs_value": value, "duty_amount": round(value * (0.05 + (index % 4) * 0.025), 2),
                    "currency_code": purchase_orders[index]["currency_code"],
                    "clearance_status": "CLEARED" if index % 9 else "DOCUMENT_REVIEW",
                    "provenance": json.dumps({"phase": "PHASE2", "method": "VALUE_DERIVED_FROM_SYNTHETIC_PO", "po_id": f"PO-P2-{index + 1:05d}"}),
                })
            db.copy_frame(conn, "skope.customs_entry", pd.DataFrame(customs))

            policy_topics = [
                "Inbound receiving", "Outbound dispatch", "Cycle counting", "Cold-chain handling",
                "Damaged goods", "Returns processing", "Dock safety", "Visitor access", "Container handover",
                "Customs document review", "Supplier escalation", "Purchase order approval", "Inventory adjustment",
                "Hazardous material segregation", "Fire response", "Theft response", "Quality hold", "Recall execution",
                "Carrier booking", "Proof of delivery", "Temperature excursion", "Pallet labeling", "Bin location control",
                "Warehouse housekeeping", "Forklift inspection", "Loading bay control", "Seal verification",
                "Shipment exception handling", "Late delivery review", "Invoice matching", "Supplier onboarding",
                "Contract renewal", "Data access", "Customer contact handling", "Email escalation", "Audit closure",
                "Shortage reporting", "Safety stock review", "Goods receipt", "Cross-dock transfer", "Export manifest",
                "Import clearance", "Packaging inspection", "Weight verification", "Document retention", "SLA monitoring",
                "Emergency shutdown", "Business continuity", "Incident reporting", "Management review",
            ]
            policies = []
            for index, topic in enumerate(policy_topics):
                quick = index >= 35
                body = (
                    f"Purpose: establish the controlled SKOPE process for {topic.lower()}.\n"
                    "Scope: applies to authorized employees and contracted operators working on the recorded transaction.\n"
                    "Responsibilities: the process owner approves exceptions; the operator records evidence; quality reviews closure.\n"
                    "Procedure: verify source identifiers, perform the required control, record the result, escalate exceptions, and retain evidence.\n"
                    "Quality and safety: stop work where required data or safe operating conditions are absent.\n"
                    "Escalation: notify the operations lead for overdue, high-risk, or policy-conflicting events."
                )
                policies.append({
                    "policy_id": f"SOP-SKOPE-{index + 1:03d}", "policy_type": "QUICK_REFERENCE" if quick else "FULL_SOP",
                    "title": f"{topic} procedure", "body_text": body,
                    "effective_date": date(2026, 1, 1) + timedelta(days=index),
                    "review_date": date(2026, 7, 1) + timedelta(days=index % 30), "version": "1.0",
                    "is_synthetic": True,
                    "provenance": json.dumps({"phase": "PHASE2", "method": "APPROVED_SYNTHETIC_POLICY_TEMPLATE"}),
                })
            db.copy_frame(conn, "skope.enterprise_policy", pd.DataFrame(policies))

            counts = {
                "suppliers": len(SUPPLIERS), "supplier_products": len(supplier_products),
                "purchase_orders": len(purchase_orders), "purchase_order_lines": len(po_lines),
                "shipments": len(shipments), "shipment_containers": len(shipment_containers),
                "goods_receipts": len(receipts), "contracts": len(contracts), "contract_clauses": len(clauses),
                "warehouse_audits": len(audits), "audit_findings": len(findings),
                "customs_entries": len(customs), "enterprise_policies": len(policies),
            }
            conn.execute(
                "UPDATE skope.etl_run SET completed_at=CURRENT_TIMESTAMP,status='SUCCEEDED',details=%s::jsonb WHERE run_id=%s",
                (json.dumps({"row_counts": counts, "seed": SEED}), run_id),
            )
            conn.execute("ANALYZE")
            conn.commit()
            print(json.dumps({"status": "SUCCEEDED", "counts": counts}, indent=2))
            return counts
        except Exception as exc:
            conn.rollback()
            conn.execute(
                "UPDATE skope.etl_run SET completed_at=CURRENT_TIMESTAMP,status='FAILED',details=%s::jsonb WHERE run_id=%s",
                (json.dumps({"error": str(exc)}), run_id),
            )
            conn.commit()
            raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=db.DEFAULT_DATABASE_URL)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    load_phase2(args.database_url, args.replace)
