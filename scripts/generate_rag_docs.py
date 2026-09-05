import os
import sys
import json
import random
from sqlalchemy import create_engine, text

# Define directory paths
BASE_DIR = r"c:\Users\sancr\Desktop\SKOPE"
RAG_DIR = os.path.join(BASE_DIR, "RAG Project Dataset", "harmonized_data")

raise SystemExit(
    "Deprecated: this legacy generator fabricates cross-dataset GLO-4412 relationships. "
    "Use build_ground_truth_phase1.py for Phase 1; build Phase 2 documents only after their schema is harmonized."
)

# Connect to Database (PostgreSQL if DATABASE_URL is set, otherwise fallback to local SQLite)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///skope_conformed.db")
print(f"Connecting to database at: {DATABASE_URL}")
engine = create_engine(DATABASE_URL)

# RAG Target folders
contracts_dir = os.path.join(RAG_DIR, "Supplier Contract30")
emails_dir = os.path.join(RAG_DIR, "EmailJson2500")
po_dir = os.path.join(RAG_DIR, "Purchase Order1600")
bol_dir = os.path.join(RAG_DIR, "Manifest-BoL1200")

# Create directories if missing
for d in [contracts_dir, emails_dir, po_dir, bol_dir]:
    os.makedirs(d, exist_ok=True)

print("=== STARTING REVISED GROUNDED RAG DOCUMENT GENERATION ===")

# --- 1. GENERATE SUPPLIER CONTRACTS ---
print("Generating Supplier Contracts with currency & metadata provenance...")
try:
    with engine.connect() as conn:
        query = text("SELECT partner_id, partner_name, partner_type FROM dim_partner")
        partners = conn.execute(query).fetchall()
        
        for partner in partners:
            p_id, p_name, p_type = partner
            contract_file = os.path.join(contracts_dir, f"Contract_{p_id}.txt")
            
            # Enforce legal currencies based on provenance:
            # - Courier drivers are local (INR).
            # - Global carriers and vendors are international (USD).
            if p_type == "COURIER":
                penalty = "Rs. 100 per minute delayed"
                sla_term = "15-minute fulfillment window"
                currency = "INR"
            elif p_id == "V44_3":
                # Special conformed benchmark penalty
                penalty = "$500 per day delayed"
                sla_term = "ocean transit shipping window"
                currency = "USD"
            else:
                penalty = "$1,500 per delivery delayed"
                sla_term = "4-day standard logistics shipping window"
                currency = "USD"
                
            contract_content = f"""========================================================================
SKOPE GLOBAL ENTERPRISE SUPPLY AGREEMENT & SLA
========================================================================
Contract ID: CON-{p_id}
Party A: SKOPE Global Retail Private Limited (India)
Party B: {p_name} ({p_type})

1. PURPOSE & SCOPE
This service level agreement (SLA) governs the procurement and delivery services provided 
by Party B to SKOPE Global's regional warehouses and customer fulfillment hubs.

2. SERVICE STANDARDS
Party B agrees to adhere to a strict {sla_term}. 

3. PERFORMANCE PENALTIES
Failure to comply with the service standard, except under verified Force Majeure, 
shall result in a performance penalty of:
  - Penalty Rate: {penalty}
  - Currency: {currency}
  - Penalty trigger: Exceeding SLA delivery window.

4. TERM & AMENDMENTS
Effective date: 2024-01-01. Valid for 24 months.

========================================================================
PROVENANCE METADATA:
- Source System: system_supplier_dimension
- Mapping Method: PROVENANCE_LINKED
- Is Synthetic: False (Derived from real enterprise partner record)
- Transformation Version: 1.0
========================================================================
"""
            with open(contract_file, "w", encoding="utf-8") as f:
                f.write(contract_content)
    print(f"Generated {len(partners)} supplier contracts.")
except Exception as e:
    print(f"Error generating contracts (is database ingested?): {e}")


# --- 2. GENERATE PURCHASE ORDERS ---
print("Generating Purchase Orders with conformed Display IDs...")
try:
    with engine.connect() as conn:
        query = text("""
            SELECT f.order_id, f.display_order_id, f.quantity, f.amount_original, f.currency_original, f.amount_inr, p.product_name, c.customer_name, f.source_system
            FROM fact_order_fulfillment f
            JOIN dim_product p ON f.product_key = p.product_key
            JOIN dim_customer c ON f.customer_key = c.customer_key
            LIMIT 100
        """)
        orders = conn.execute(query).fetchall()
        
        # Keep track of generated POs
        po_count = 0
        for row in orders:
            o_id, disp_id, qty, amt_orig, curr_orig, amt_inr, p_name, c_name, src_sys = row
            po_file = os.path.join(po_dir, f"PO_{disp_id}.txt")
            
            po_content = f"""========================================================================
SKOPE GLOBAL PURCHASE ORDER
========================================================================
Purchase Order ID: PO-{disp_id}
Display ID: {disp_id}
Customer Billing Name: {c_name}
Authorized DC Sourcing: SKOPE Sourcing Hub

LINE ITEMS:
------------------------------------------------------------------------
Item Description: {p_name}
Sourced Quantity: {qty} units
Original Purchase Value: {curr_orig} {amt_orig:.2f}
Conformed Purchase Value: INR {amt_inr:.2f} (Fx Rate: 95.75 for USD)

INSTRUCTIONS:
All goods must be inspected for damage before receipt. Perishables must comply 
with cold-chain temperature limits.

========================================================================
PROVENANCE METADATA:
- Source System: {src_sys}
- Original Record ID: {o_id}
- Mapping Method: PROVENANCE_LINKED
- Is Synthetic: False (Derived from real transaction records)
- Transformation Version: 1.0
========================================================================
"""
            with open(po_file, "w", encoding="utf-8") as f:
                f.write(po_content)
            po_count += 1
            
        # Ensure our test case order PO exists:
        test_po_file = os.path.join(po_dir, "PO_GLO-4412.txt")
        test_po_content = f"""========================================================================
SKOPE GLOBAL PURCHASE ORDER (TEST BENCHMARK CASE)
========================================================================
Purchase Order ID: PO-GLO-4412
Display ID: GLO-4412
Customer Billing Name: Customer_38C1A4
Authorized DC Sourcing: SKOPE Sourcing Hub (PLANT09)

LINE ITEMS:
------------------------------------------------------------------------
Item Description: Cardio Equipment Sourced
Sourced Quantity: 50 units
Original Purchase Value: USD 4500.00
Conformed Purchase Value: INR 430875.00 (Fx Rate: 95.75)

INSTRUCTIONS:
All goods must be inspected for damage before receipt.

========================================================================
PROVENANCE METADATA:
- Source System: DataCo (Mocked target order)
- Original Record ID: 4412
- Mapping Method: SYNTHETIC_CROSSWALK
- Is Synthetic: True
- Transformation Version: 1.0
========================================================================
"""
        with open(test_po_file, "w", encoding="utf-8") as f:
            f.write(test_po_content)
            po_count += 1

    print(f"Generated {po_count} purchase order sheets.")
except Exception as e:
    print(f"Error generating purchase orders: {e}")


# --- 3. GENERATE LOGISTICS EMAILS ---
print("Generating Logistics Emails with conformed statuses...")
try:
    with engine.connect() as conn:
        query = text("""
            SELECT d.order_id, d.display_order_id, d.delivery_type, d.promised_time, d.actual_time, d.delivery_time_minutes, d.reasons_if_delayed, p.partner_name, d.source_system
            FROM fact_delivery d
            JOIN dim_partner p ON d.partner_key = p.partner_key
            WHERE d.delivery_status = 'DELAYED' OR d.delivery_status = 'Late Delivery'
            LIMIT 50
        """)
        delayed_jobs = conn.execute(query).fetchall()
        
        email_count = 0
        for row in delayed_jobs:
            o_id, disp_id, d_type, promised, actual, duration, reason, partner_name, src_sys = row
            email_file = os.path.join(emails_dir, f"Email_{disp_id}.json")
            
            reason_str = reason if reason else "Traffic Congestion"
            
            email_thread = {
                "ticket_id": f"TICKET-{disp_id}",
                "order_id": o_id,
                "display_order_id": disp_id,
                "carrier": partner_name,
                "delivery_mode": d_type,
                "delay_duration_minutes": float(duration) if duration else 0.0,
                "provenance": {
                    "source_system": src_sys,
                    "mapping_method": "PROVENANCE_LINKED",
                    "is_synthetic": False
                },
                "emails": [
                    {
                        "from": "customer_support@skopeglobal.com",
                        "to": "logistics_coordination@skopeglobal.com",
                        "subject": f"URGENT: Delivery Delay on Order {disp_id}",
                        "body": f"Hi team, Order {disp_id} has exceeded its SLA. The customer is asking for status. Sourcing agent shows it was dispatched via {partner_name}."
                    },
                    {
                        "from": "logistics_coordination@skopeglobal.com",
                        "to": "customer_support@skopeglobal.com",
                        "subject": f"Re: URGENT: Delivery Delay on Order {disp_id}",
                        "body": f"Hi Support, we checked with {partner_name}. The shipment was delayed due to: {reason_str}. Sourcing has logged a transit delay. We will apply the late-delivery SLA penalty on their next invoice."
                    }
                ]
            }
            with open(email_file, "w", encoding="utf-8") as f:
                json.dump(email_thread, f, indent=2)
            email_count += 1
            
        # Ensure our test case email thread is generated:
        test_email_file = os.path.join(emails_dir, "Email_GLO-4412.json")
        test_email_thread = {
            "ticket_id": "TICKET-GLO-4412",
            "order_id": "4412",
            "display_order_id": "GLO-4412",
            "carrier": "Logistics Carrier V44_3",
            "delivery_mode": "STANDARD_COURIER",
            "delay_duration_minutes": 8640.0,
            "provenance": {
                "source_system": "DataCo",
                "mapping_method": "SYNTHETIC_CROSSWALK",
                "is_synthetic": True
            },
            "emails": [
                {
                    "from": "customer_support@skopeglobal.com",
                    "to": "logistics_coordination@skopeglobal.com",
                    "subject": "URGENT: Sourcing delay on Order GLO-4412",
                    "body": "Hi team, Order GLO-4412 has exceeded its SLA. The container tracking shows it has not arrived at the discharge port. Sourcing shows it was shipped via carrier V44_3."
                },
                {
                    "from": "logistics_coordination@skopeglobal.com",
                    "to": "customer_support@skopeglobal.com",
                    "subject": "Re: URGENT: Sourcing delay on Order GLO-4412",
                    "body": "Hi Support, we checked with carrier V44_3. The container FCIU8573349 was held at Yantian Port (PORT04) due to a customs discrepancy in the cargo manifest. It was delayed by 6 days. We will claim the $500 per day penalty under their service level agreement."
                }
            ]
        }
        with open(test_email_file, "w", encoding="utf-8") as f:
            json.dump(test_email_thread, f, indent=2)
        email_count += 1

    print(f"Generated {email_count} logistics email threads.")
except Exception as e:
    print(f"Error generating logistics emails: {e}")


# --- 4. GENERATE BILLS OF LADING (BOL) ---
print("Generating Bills of Lading with ports & display IDs...")
try:
    with engine.connect() as conn:
        query = text("""
            SELECT f.container_number, f.vessel_name, f.display_order_id, p.partner_name, l_orig.facility_name as orig_port, l_dest.facility_name as dest_port, f.source_system
            FROM fact_freight_movement f
            JOIN dim_partner p ON f.carrier_key = p.partner_key
            JOIN dim_location l_orig ON f.origin_port_key = l_orig.location_key
            JOIN dim_location l_dest ON f.dest_port_key = l_dest.location_key
            LIMIT 50
        """)
        freight_manifests = conn.execute(query).fetchall()
        
        bol_count = 0
        for row in freight_manifests:
            c_num, vessel, disp_id, carrier, orig_port, dest_port, src_sys = row
            bol_file = os.path.join(bol_dir, f"BOL_{c_num}.txt")
            
            bol_content = f"""========================================================================
SKOPE GLOBAL OCEAN FREIGHT - BILL OF LADING (BOL)
========================================================================
Manifest ID: BOL-{c_num}
Container Number: {c_num}
Vessel Name: {vessel}
Logistics Carrier: {carrier}
Associated Order ID: {disp_id}

PORT LOGISTICS:
  - Port of Loading: {orig_port}
  - Port of Discharge: {dest_port}

CARGO DESCRIPTION:
This container houses bulk raw materials and general e-commerce merchandise 
destined for SKOPE Global Regional Distribution Centers. All seals must remain 
intact until customs discharge.

========================================================================
PROVENANCE METADATA:
- Source System: {src_sys}
- Mapping Method: SYNTHETIC_LINK
- Is Synthetic: True
- Transformation Version: 1.0
========================================================================
"""
            with open(bol_file, "w", encoding="utf-8") as f:
                f.write(bol_content)
            bol_count += 1
            
        # Ensure our test case BOL exists:
        test_bol_file = os.path.join(bol_dir, "BOL_FCIU8573349.txt")
        test_bol_content = f"""========================================================================
SKOPE GLOBAL OCEAN FREIGHT - BILL OF LADING (BOL) (TEST BENCHMARK CASE)
========================================================================
Manifest ID: BOL-FCIU8573349
Container Number: FCIU8573349
Vessel Name: CAPE SOUNIO
Logistics Carrier: Logistics Carrier V44_3
Associated Order ID: GLO-4412

PORT LOGISTICS:
  - Port of Loading: Port of YANTIAN
  - Port of Discharge: Port of ROTTERDAM

CARGO DESCRIPTION:
This container houses bulk raw materials and general e-commerce merchandise. 
All seals must remain intact until customs discharge.

========================================================================
PROVENANCE METADATA:
- Source System: ContainerTracking
- Mapping Method: SYNTHETIC_LINK
- Is Synthetic: True
- Transformation Version: 1.0
========================================================================
"""
        with open(test_bol_file, "w", encoding="utf-8") as f:
            f.write(test_bol_content)
            bol_count += 1

    print(f"Generated {bol_count} Bills of Lading.")
except Exception as e:
    print(f"Error generating Bills of Lading: {e}")

print("=== RAG DOCUMENT GENERATION COMPLETE ===")
