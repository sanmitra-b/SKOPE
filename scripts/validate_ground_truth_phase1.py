"""Fail-closed validation for the staged or promoted SKOPE Phase 1 ground truth."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd


BASE = Path(__file__).resolve().parents[1]
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / ".data-review" / "harmonized_phase1_staging"
CUTOFF = pd.Timestamp("2026-07-31 23:59:59.999999")
checks: list[dict] = []


def check(name: str, passed: bool, detail) -> None:
    checks.append({"check": name, "passed": bool(passed), "detail": detail})


def read(rel: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(ROOT / rel, **kwargs)


orders = read("Blinkit_Sales_dataset/blinkit_orders.csv")
delivery = read("Blinkit_Sales_dataset/blinkit_delivery_performance.csv")
feedback = read("Blinkit_Sales_dataset/blinkit_customer_feedback.csv")
items = read("Blinkit_Sales_dataset/blinkit_order_items.csv")
inventory = read("Blinkit_Sales_dataset/blinkit_inventory.csv")

order_ids = set(orders["order_id"].astype(str))
check("blinkit_order_ids_unique", orders["order_id"].astype(str).is_unique, len(orders))
check("blinkit_delivery_matches_orders", set(delivery["order_id"].astype(str)) == order_ids, len(delivery))
check("blinkit_feedback_has_parent", set(feedback["order_id"].astype(str)).issubset(order_ids), len(feedback))
check("blinkit_items_have_parent", set(items["order_id"].astype(str)).issubset(order_ids), len(items))

merged = orders[["order_id", "promised_delivery_time", "actual_delivery_time"]].merge(
    delivery[["order_id", "promised_time", "actual_time"]], on="order_id", how="inner"
)
promised_equal = pd.to_datetime(merged["promised_delivery_time"], errors="coerce").equals(
    pd.to_datetime(merged["promised_time"], errors="coerce")
)
actual_equal = pd.to_datetime(merged["actual_delivery_time"], errors="coerce").equals(
    pd.to_datetime(merged["actual_time"], errors="coerce")
)
check("blinkit_promised_times_consistent", promised_equal, len(merged))
check("blinkit_actual_times_consistent", actual_equal, len(merged))
check("blinkit_inventory_event_ids_unique", inventory["inventory_event_id"].is_unique, len(inventory))

for label, frame, columns in [
    ("blinkit", orders, ["order_date", "actual_delivery_time"]),
    ("blinkit_delivery", delivery, ["actual_time"]),
    ("blinkit_feedback", feedback, ["feedback_date"]),
    ("blinkit_inventory", inventory, ["date"]),
]:
    for column in columns:
        values = pd.to_datetime(frame[column], errors="coerce")
        check(f"{label}_{column}_within_cutoff", not (values > CUTOFF).any(), str(values.max()))

dataco = read("DataCoSC/DataCoSupplyChainDataset.csv", low_memory=False)
check("dataco_order_item_id_unique", dataco["Order Item Id"].is_unique, len(dataco))
check("dataco_order_product_collisions_preserved", len(dataco) - dataco[["Order Id", "Product Card Id"]].drop_duplicates().shape[0] > 0, len(dataco))
check(
    "dataco_direct_pii_columns_removed",
    not any(column in dataco.columns for column in ["Customer Password", "Customer Email", "Customer Street", "Customer Zipcode", "Latitude", "Longitude"]),
    list(dataco.columns),
)
for column in ["order date (DateOrders)", "shipping date (DateOrders)"]:
    values = pd.to_datetime(dataco[column], errors="coerce")
    check(f"dataco_{column}_within_cutoff", not (values > CUTOFF).any(), str(values.max()))

shipping = read("shipping_data.csv")
check("shipping_record_ids_unique", shipping["shipment_record_id"].is_unique, len(shipping))
ship_dates = pd.to_datetime(shipping["shipment date"], errors="coerce")
check("shipping_dates_within_cutoff", not (ship_dates > CUTOFF).any(), str(ship_dates.max()))

containers = pd.read_excel(ROOT / "Container Tracking Data.xlsx", sheet_name="Data with Main Column")
check("container_record_ids_unique", containers["container_record_id"].is_unique, len(containers))
check("container_ports_complete", not containers[["PORT_OF_LOADING", "PORT_OF_DISCHARGE"]].isna().any().any(), containers[["PORT_OF_LOADING", "PORT_OF_DISCHARGE"]].isna().sum().to_dict())
check("container_prediction_columns_removed", not any("PREDICT" in str(column).upper() for column in containers.columns), list(containers.columns))
for column in [
    "PLACE_OF_DISPATCH_DATE", "PORT_OF_LOADING_DATE", "PORT_OF_DISCHARGE_DATE",
    "POST_PORT_OF_DISCHARGE_DATE", "LAST_TRACKED_WITH_VESSEL", "DELIVERED_DATE",
]:
    values = pd.to_datetime(containers[column], errors="coerce")
    check(f"container_{column}_within_cutoff", not (values > CUTOFF).any(), str(values.max()))

email_files = sorted((ROOT / "EmailJson2500").glob("*.json"))
emails = [json.loads(path.read_text(encoding="utf-8")) for path in email_files]
expected_email_families = {
    "TICKET-DATACO-ORDER-": 550,
    "TICKET-BLINKIT-ORDER-": 450,
    "TICKET-BLINKIT-INVENTORY-": 250,
    "TICKET-INDIAECOM-ORDER-": 450,
    "TICKET-LOGISTICS-ORDER-": 300,
    "TICKET-CONTAINER-": 300,
    "TICKET-SHIPPING-": 200,
}
family_counts = {
    prefix: sum(email["ticket_id"].startswith(prefix) for email in emails)
    for prefix in expected_email_families
}
check("email_count", len(emails) == 2500, len(emails))
check("email_family_counts", family_counts == expected_email_families, family_counts)
check("email_ticket_ids_unique", len({email["ticket_id"] for email in emails}) == len(emails), len(emails))
check("email_primary_global_ids_unique", len({email["primary_entity"]["global_id"] for email in emails}) == len(emails), len(emails))
check("emails_all_synthetic", all(email["provenance"]["is_synthetic"] is True for email in emails), len(emails))
check("emails_have_message_ids_and_timestamps", all(all(message.get("message_id") and message.get("sent_at") for message in email["emails"]) for email in emails), len(emails))
message_ids = [message["message_id"] for email in emails for message in email["emails"]]
check("email_message_ids_unique", len(message_ids) == len(set(message_ids)), len(message_ids))
message_times = [pd.Timestamp(message["sent_at"]) for email in emails for message in email["emails"]]
check("email_timestamps_within_cutoff", all(value <= CUTOFF for value in message_times), str(max(message_times)))
check(
    "email_ambiguous_glo_namespace_removed",
    all("GLO-" not in email["ticket_id"] and "GLO-" not in email["primary_entity"]["global_id"]
        and all("GLO-" not in message["message_id"] for message in email["emails"]) for email in emails),
    len(emails),
)

logistics_orders = pd.read_excel(ROOT / "Supply chain logisitcs problem.xlsx", sheet_name="OrderList", dtype={"Order ID": str})
source_registry = {
    ("DataCoSC/DataCoSupplyChainDataset.csv", "Order Item Id"): set(dataco["Order Item Id"].astype(str)),
    ("Blinkit_Sales_dataset/blinkit_orders.csv", "order_id"): set(orders["order_id"].astype(str)),
    ("Blinkit_Sales_dataset/blinkit_delivery_performance.csv", "order_id"): set(delivery["order_id"].astype(str)),
    ("Blinkit_Sales_dataset/blinkit_customer_feedback.csv", "order_id"): set(feedback["order_id"].astype(str)),
    ("Blinkit_Sales_dataset/blinkit_order_items.csv", "order_id"): set(items["order_id"].astype(str)),
    ("Blinkit_Sales_dataset/blinkit_inventory.csv", "inventory_event_id"): set(inventory["inventory_event_id"].astype(str)),
    ("Blinkit_Sales_dataset/blinkit_products.csv", "product_id"): set(read("Blinkit_Sales_dataset/blinkit_products.csv")["product_id"].astype(str)),
    ("Indian E-Commerce Sales Analytics Dataset/sales.csv", "Order_ID"): set(read("Indian E-Commerce Sales Analytics Dataset/sales.csv", dtype={"Order_ID": str})["Order_ID"]),
    ("Supply chain logisitcs problem.xlsx", "Order ID"): set(logistics_orders["Order ID"]),
    ("Container Tracking Data.xlsx", "container_record_id"): set(containers["container_record_id"].astype(str)),
    ("shipping_data.csv", "shipment_record_id"): set(shipping["shipment_record_id"].astype(str)),
}
source_links_valid = True
unknown_source_contracts = []
for email in emails:
    for record in email["provenance"]["source_records"]:
        contract = (record["source_file"], record["key_field"])
        if contract not in source_registry:
            unknown_source_contracts.append(contract)
            source_links_valid = False
            continue
        if not set(map(str, record["record_ids"])).issubset(source_registry[contract]):
            source_links_valid = False
check("email_source_records_exist", source_links_valid, unknown_source_contracts[:10])

dataco_order_by_item = dataco.set_index(dataco["Order Item Id"].astype(str))["Order Id"].astype(str).to_dict()
dataco_order_links_valid = True
for email in (item for item in emails if item["ticket_id"].startswith("TICKET-DATACO-ORDER-")):
    expected_order = email["primary_entity"]["source_id"]
    item_ids = email["related_entities"]["order_item_ids"]
    if not all(dataco_order_by_item.get(item_id) == expected_order for item_id in item_ids):
        dataco_order_links_valid = False
check("dataco_email_items_belong_to_order", dataco_order_links_valid, 550)
check(
    "dataco_emails_do_not_invent_carrier",
    all(email["facts"].get("carrier") is None for email in emails if email["ticket_id"].startswith("TICKET-DATACO-ORDER-")),
    550,
)

rejected = read("governance/phase1_rejected_records.csv")
check("rejection_ledger_nonempty", len(rejected) > 0, len(rejected))
check("rejection_ledger_complete_columns", list(rejected.columns) == ["source_file", "source_row_number", "source_record_id", "reason"], list(rejected.columns))

policy = json.loads((ROOT / "governance" / "ground_truth_policy.json").read_text(encoding="utf-8"))
check("policy_declares_canonical_role", policy.get("dataset_name") == "SKOPE Phase 1 Canonical Ground Truth", policy.get("dataset_name"))
check("policy_declares_phase2_untouched", len(policy.get("phase2_untouched", [])) == 3, policy.get("phase2_untouched"))

indian_customers = read("Indian E-Commerce Sales Analytics Dataset/customers.csv")
check("indian_exact_dob_removed", "Date_of_Birth" not in indian_customers.columns, list(indian_customers.columns))

raw_logistics = pd.ExcelFile(BASE / "RAG Project Dataset" / "Structured Table40" / "Supply chain logisitcs problem.xlsx")
canonical_logistics = pd.ExcelFile(ROOT / "Supply chain logisitcs problem.xlsx")
check("logistics_sheet_set_preserved", raw_logistics.sheet_names == canonical_logistics.sheet_names, canonical_logistics.sheet_names)
raw_orders = raw_logistics.parse("OrderList")
canonical_orders = canonical_logistics.parse("OrderList")
expected_order_dates = pd.to_datetime(raw_orders["Order Date"]) + pd.DateOffset(years=12)
actual_order_dates = pd.to_datetime(canonical_orders["Order Date"])
check("logistics_order_dates_shifted_12_years", expected_order_dates.equals(actual_order_dates), str(actual_order_dates.max()))
check("logistics_order_row_count_preserved", len(raw_orders) == len(canonical_orders), len(canonical_orders))

contacts = read("restricted/customer_contacts.csv", dtype=str).fillna("")
check("restricted_contact_ids_unique", contacts["contact_id"].is_unique, len(contacts))
check(
    "restricted_contact_namespaces_explicit",
    contacts["contact_id"].str.startswith(("DATACO-CUSTOMER-", "BLINKIT-CUSTOMER-", "INDIAECOM-CUSTOMER-")).all(),
    contacts["contact_id"].str.extract(r"^([A-Z]+)")[0].value_counts().to_dict(),
)
check("restricted_contact_keys_complete", not contacts[["source_system", "source_customer_id"]].eq("").any().any(), len(contacts))
check("restricted_not_rag_indexable", contacts["rag_index_allowed"].str.casefold().eq("false").all(), len(contacts))
check("restricted_passwords_absent", not any("password" in column.casefold() for column in contacts.columns), list(contacts.columns))

indian_contact_ids = set(contacts.loc[contacts["source_system"] == "IndianEComm", "source_customer_id"])
blinkit_contact_ids = set(contacts.loc[contacts["source_system"] == "Blinkit", "source_customer_id"])
dataco_contact_ids = set(contacts.loc[contacts["source_system"] == "DataCo", "source_customer_id"])
indian_sales = read("Indian E-Commerce Sales Analytics Dataset/sales.csv", dtype={"Customer_ID": str})
check("restricted_covers_indian_orders", set(indian_sales["Customer_ID"].astype(str)).issubset(indian_contact_ids), len(indian_contact_ids))
check("restricted_covers_blinkit_orders", set(orders["customer_id"].astype(str)).issubset(blinkit_contact_ids), len(blinkit_contact_ids))
check("restricted_covers_dataco_orders", set(dataco["Customer Id"].astype(str)).issubset(dataco_contact_ids), len(dataco_contact_ids))
email_contact_refs = {
    email["related_entities"]["customer_contact_id"]
    for email in emails
    if "customer_contact_id" in email["related_entities"]
}
check("email_contact_references_exist", email_contact_refs.issubset(set(contacts["contact_id"])), len(email_contact_refs))

shared = pd.to_numeric(contacts["email_shared_customer_count"], errors="coerce").fillna(0).gt(1)
eligible = contacts["email_send_eligible"].str.casefold().eq("true")
check("shared_emails_not_auto_sendable", not (shared & eligible).any(), int(shared.sum()))
dataco_contacts = contacts[contacts["source_system"] == "DataCo"]
check("dataco_redacted_email_not_sendable", (~dataco_contacts["email_send_eligible"].str.casefold().eq("true")).all(), len(dataco_contacts))
check("dataco_email_status_redacted", dataco_contacts["email_status"].eq("SOURCE_REDACTED").all(), len(dataco_contacts))

rag_policy = json.loads((ROOT / "governance" / "rag_index_policy.json").read_text(encoding="utf-8"))
check("rag_policy_excludes_restricted", "restricted/**" in rag_policy.get("exclude_globs", []), rag_policy.get("exclude_globs", []))
check("rag_policy_excludes_governance", "governance/**" in rag_policy.get("exclude_globs", []), rag_policy.get("exclude_globs", []))
access_policy = json.loads((ROOT / "restricted" / "access_policy.json").read_text(encoding="utf-8"))
check("contact_send_requires_confirmation", access_policy.get("explicit_confirmation_required_before_send") is True, True)
check("contact_actions_audited", access_policy.get("audit_every_reveal_and_send") is True, True)

files = []
for path in sorted(ROOT.rglob("*")):
    rel = path.relative_to(ROOT)
    if rel.parts and rel.parts[0] in {"Supplier Contract30", "Purchase Order1600", "Manifest-BoL1200"}:
        continue
    if not path.is_file() or path.name.endswith(".inspect.ndjson") or path.name == "phase1_validation_report.json":
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    row_count = None
    if path.suffix.lower() == ".csv":
        row_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace")) - 1
    elif path.suffix.lower() == ".json" and path.parent.name == "EmailJson2500":
        row_count = 1
    files.append({"path": rel.as_posix(), "sha256": digest, "row_count": row_count})

result = {
    "root": str(ROOT),
    "passed": all(item["passed"] for item in checks),
    "checks": checks,
    "files": files,
}
(ROOT / "governance" / "phase1_validation_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
if "--quiet" in sys.argv:
    print(json.dumps({"root": result["root"], "passed": result["passed"], "check_count": len(checks), "file_count": len(files)}, indent=2))
else:
    print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
