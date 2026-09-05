"""Build the canonical Phase 1 SKOPE ground-truth dataset in a staging directory.

This script never writes to the raw source archive. It rebuilds tabular datasets and
synthetic email JSON from Structured Table40, records exclusions, and leaves Phase 2
documents untouched. Promotion into harmonized_data happens only after validation.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from generate_business_emails import generate_business_emails


BASE = Path(__file__).resolve().parents[1]
RAW = BASE / "RAG Project Dataset" / "Structured Table40"
CANONICAL = BASE / "RAG Project Dataset" / "harmonized_data"
REVIEW = BASE / ".data-review"
STAGING = REVIEW / "harmonized_phase1_staging"
BACKUP = REVIEW / "phase1_pre_ground_truth_backup_20260821"
CONTAINER_INTERMEDIATE = REVIEW / "container_ground_truth_rows.json"
CUTOFF_EOD = pd.Timestamp("2026-07-31 23:59:59.999999")
VERSION = "3.0.0-phase1"
EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)


def assert_review_path(path: Path) -> None:
    resolved = path.resolve()
    if REVIEW.resolve() not in resolved.parents:
        raise RuntimeError(f"Unsafe staging/backup path: {resolved}")


def reset_staging() -> None:
    assert_review_path(STAGING)
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)


def backup_phase1() -> None:
    assert_review_path(BACKUP)
    if BACKUP.exists():
        return
    for path in CANONICAL.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(CANONICAL)
        if rel.parts[0] in {"Supplier Contract30", "Purchase Order1600", "Manifest-BoL1200"}:
            continue
        if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".json", ".md"}:
            continue
        target = BACKUP / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def write_csv(df: pd.DataFrame, rel: str) -> None:
    target = STAGING / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(target, index=False)


def mask_name(val) -> str:
    if pd.isna(val) or str(val).strip() == "":
        return "Anonymous Customer"
    digest = hashlib.md5(str(val).strip().encode("utf-8")).hexdigest()[:6].upper()
    return f"Customer_{digest}"


def mask_email(val) -> str:
    if pd.isna(val) or str(val).strip() == "":
        return "customer.hidden@skopeglobal.com"
    digest = hashlib.md5(str(val).strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"c***_{digest}@skopeglobal.com"


def mask_phone(val) -> str:
    if pd.isna(val) or str(val).strip() == "":
        return "+91-XXXXX-XXXXX"
    digits = "".join(filter(str.isdigit, str(val)))
    suffix = digits[-4:] if len(digits) >= 4 else "0000"
    return f"+91-XXXXX-X{suffix}"


def mask_address(val) -> str:
    return "Masked Sourcing Address, India" if pd.isna(val) or not str(val).strip() else "Masked Address, India"


rejections: list[dict] = []


def reject_rows(source_file: str, df: pd.DataFrame, mask: pd.Series, reason: str, id_column: str | None = None) -> None:
    for idx, row in df.loc[mask].iterrows():
        rejections.append(
            {
                "source_file": source_file,
                "source_row_number": int(idx) + 2,
                "source_record_id": "" if id_column is None else str(row.get(id_column, "")),
                "reason": reason,
            }
        )


def build_indian() -> None:
    folder = "Indian E-Commerce Sales Analytics Dataset"
    customers = pd.read_csv(RAW / folder / "customers.csv")
    customers["Customer_Name"] = customers["Customer_Name"].apply(mask_name)
    customers["Email"] = customers["Email"].apply(mask_email)
    customers["Phone"] = customers["Phone"].apply(mask_phone)
    customers = customers.drop(columns=[c for c in ["Date_of_Birth"] if c in customers.columns])
    write_csv(customers, f"{folder}/customers.csv")

    write_csv(pd.read_csv(RAW / folder / "products.csv"), f"{folder}/products.csv")

    sales = pd.read_csv(RAW / folder / "sales.csv")
    sales["Order_Date"] = pd.to_datetime(sales["Order_Date"], format="%Y-%m-%d", errors="raise")
    sales["Delivery_Date"] = pd.to_datetime(sales["Delivery_Date"], format="%Y-%m-%d", errors="coerce")
    excluded = sales["Order_Date"] > CUTOFF_EOD
    reject_rows(f"{folder}/sales.csv", sales, excluded, "ORDER_AFTER_CUTOFF", "Order_ID")
    sales = sales.loc[~excluded].copy()
    future = sales["Delivery_Date"] > CUTOFF_EOD
    sales.loc[future, "Delivery_Date"] = pd.NaT
    sales.loc[future, "Order_Status"] = "SHIPPED_AS_OF_CUTOFF"
    sales["Order_Date"] = sales["Order_Date"].dt.strftime("%Y-%m-%d")
    sales["Delivery_Date"] = sales["Delivery_Date"].dt.strftime("%Y-%m-%d")
    write_csv(sales, f"{folder}/sales.csv")


def build_blinkit() -> None:
    folder = "Blinkit_Sales_dataset"
    customers = pd.read_csv(RAW / folder / "blinkit_customers.csv")
    customers["customer_name"] = customers["customer_name"].apply(mask_name)
    customers["email"] = customers["email"].apply(mask_email)
    customers["phone"] = customers["phone"].apply(mask_phone)
    customers["address"] = customers["address"].apply(mask_address)
    write_csv(customers, f"{folder}/blinkit_customers.csv")

    write_csv(pd.read_csv(RAW / folder / "blinkit_products.csv"), f"{folder}/blinkit_products.csv")

    orders = pd.read_csv(RAW / folder / "blinkit_orders.csv")
    for column in ["order_date", "promised_delivery_time", "actual_delivery_time"]:
        orders[column] = pd.to_datetime(orders[column], format="%Y-%m-%d %H:%M:%S", errors="coerce") + pd.DateOffset(years=2)
    excluded = orders["order_date"] > CUTOFF_EOD
    reject_rows(f"{folder}/blinkit_orders.csv", orders, excluded, "ORDER_AFTER_CUTOFF", "order_id")
    orders = orders.loc[~excluded].copy()
    future = orders["actual_delivery_time"] > CUTOFF_EOD
    orders.loc[future, "actual_delivery_time"] = pd.NaT
    orders.loc[future, "delivery_status"] = "Pending as of cutoff"
    active_order_ids = set(orders["order_id"].astype(str))
    for column in ["order_date", "promised_delivery_time", "actual_delivery_time"]:
        orders[column] = orders[column].dt.strftime("%Y-%m-%d %H:%M:%S")
    write_csv(orders, f"{folder}/blinkit_orders.csv")

    order_items = pd.read_csv(RAW / folder / "blinkit_order_items.csv")
    excluded = ~order_items["order_id"].astype(str).isin(active_order_ids)
    reject_rows(f"{folder}/blinkit_order_items.csv", order_items, excluded, "PARENT_ORDER_AFTER_CUTOFF", "order_id")
    order_items = order_items.loc[~excluded].copy()
    write_csv(order_items, f"{folder}/blinkit_order_items.csv")

    delivery = pd.read_csv(RAW / folder / "blinkit_delivery_performance.csv")
    for column in ["promised_time", "actual_time"]:
        delivery[column] = pd.to_datetime(delivery[column], format="%Y-%m-%d %H:%M:%S", errors="coerce") + pd.DateOffset(years=2)
    excluded = ~delivery["order_id"].astype(str).isin(active_order_ids)
    reject_rows(f"{folder}/blinkit_delivery_performance.csv", delivery, excluded, "PARENT_ORDER_AFTER_CUTOFF", "order_id")
    delivery = delivery.loc[~excluded].copy()
    future = delivery["actual_time"] > CUTOFF_EOD
    delivery.loc[future, "actual_time"] = pd.NaT
    delivery.loc[future, "delivery_status"] = "Pending as of cutoff"
    delivery.loc[future, "delivery_time_minutes"] = pd.NA
    for column in ["promised_time", "actual_time"]:
        delivery[column] = delivery[column].dt.strftime("%Y-%m-%d %H:%M:%S")
    write_csv(delivery, f"{folder}/blinkit_delivery_performance.csv")

    feedback = pd.read_csv(RAW / folder / "blinkit_customer_feedback.csv")
    feedback["feedback_date"] = pd.to_datetime(feedback["feedback_date"], format="%Y-%m-%d", errors="raise") + pd.DateOffset(years=2)
    excluded = (feedback["feedback_date"] > CUTOFF_EOD) | ~feedback["order_id"].astype(str).isin(active_order_ids)
    reject_rows(f"{folder}/blinkit_customer_feedback.csv", feedback, excluded, "FEEDBACK_OR_PARENT_ORDER_AFTER_CUTOFF", "order_id")
    feedback = feedback.loc[~excluded].copy()
    feedback["feedback_date"] = feedback["feedback_date"].dt.strftime("%Y-%m-%d")
    write_csv(feedback, f"{folder}/blinkit_customer_feedback.csv")

    inventory = pd.read_csv(RAW / folder / "blinkit_inventory.csv")
    inventory.insert(0, "inventory_event_id", [f"BLK-INV-{i + 1:07d}" for i in range(len(inventory))])
    inventory["date"] = pd.to_datetime(inventory["date"], format="%d-%m-%Y", errors="raise") + pd.DateOffset(years=2)
    excluded = inventory["date"] > CUTOFF_EOD
    reject_rows(f"{folder}/blinkit_inventory.csv", inventory, excluded, "INVENTORY_EVENT_AFTER_CUTOFF", "inventory_event_id")
    inventory = inventory.loc[~excluded].copy()
    inventory["date"] = inventory["date"].dt.strftime("%Y-%m-%d")
    write_csv(inventory, f"{folder}/blinkit_inventory.csv")


def build_dataco() -> pd.DataFrame:
    folder = "DataCoSC"
    data = pd.read_csv(RAW / folder / "DataCoSupplyChainDataset.csv", encoding="ISO-8859-1")
    data = data.drop(
        columns=[
            c
            for c in ["Customer Password", "Customer Email", "Customer Street", "Customer Zipcode", "Latitude", "Longitude"]
            if c in data.columns
        ]
    )
    data["Customer Fname"] = data["Customer Fname"].apply(mask_name)
    data["Customer Lname"] = ""
    data["order date (DateOrders)"] = pd.to_datetime(data["order date (DateOrders)"], format="%m/%d/%Y %H:%M", errors="raise") + pd.DateOffset(years=9)
    data["shipping date (DateOrders)"] = pd.to_datetime(data["shipping date (DateOrders)"], format="%m/%d/%Y %H:%M", errors="raise") + pd.DateOffset(years=9)
    excluded = data["order date (DateOrders)"] > CUTOFF_EOD
    reject_rows(f"{folder}/DataCoSupplyChainDataset.csv", data, excluded, "ORDER_AFTER_CUTOFF", "Order Item Id")
    data = data.loc[~excluded].copy()
    future = data["shipping date (DateOrders)"] > CUTOFF_EOD
    data.loc[future, "shipping date (DateOrders)"] = pd.NaT
    data.loc[future, "Delivery Status"] = "IN_TRANSIT_AS_OF_CUTOFF"
    data.loc[future, "Order Status"] = "PROCESSING_AS_OF_CUTOFF"
    data.loc[future, "Late_delivery_risk"] = pd.NA
    data["order date (DateOrders)"] = data["order date (DateOrders)"].dt.strftime("%Y-%m-%d %H:%M:%S")
    data["shipping date (DateOrders)"] = data["shipping date (DateOrders)"].dt.strftime("%Y-%m-%d %H:%M:%S")
    write_csv(data, f"{folder}/DataCoSupplyChainDataset.csv")
    write_csv(pd.read_csv(RAW / folder / "DescriptionDataCoSupplyChain.csv"), f"{folder}/DescriptionDataCoSupplyChain.csv")
    return data


def build_other_csvs() -> None:
    for filename in ["lastMileDeliveryTimesWithCats.csv", "GajurelKshitizEcommerce-Chat-Dataset.csv"]:
        write_csv(pd.read_csv(RAW / filename), filename)

    write_csv(
        pd.read_csv(RAW / "Flipkart Sales Data" / "products.csv"),
        "Flipkart Sales Data/products.csv",
    )

    shipping = pd.read_csv(RAW / "shipping_data.csv")
    shipping.insert(0, "shipment_record_id", [f"SHP-{i + 1:07d}" for i in range(len(shipping))])
    shipping["shipment date"] = pd.to_datetime(shipping["shipment date"], format="%Y-%m-%d", errors="raise") + pd.DateOffset(years=3)
    excluded = shipping["shipment date"] > CUTOFF_EOD
    reject_rows("shipping_data.csv", shipping, excluded, "SHIPMENT_AFTER_CUTOFF", "shipment_record_id")
    shipping = shipping.loc[~excluded].copy()
    shipping["shipment date"] = shipping["shipment date"].dt.strftime("%Y-%m-%d")
    write_csv(shipping, "shipping_data.csv")


def prepare_container_intermediate() -> None:
    source = RAW / "Container Tracking Data.xlsx"
    containers = pd.read_excel(source, sheet_name="Data with Main Column")
    definitions = pd.read_excel(source, sheet_name="Columns Definitions")
    containers.insert(0, "container_record_id", [f"CTR-{i + 1:06d}" for i in range(len(containers))])
    for column in ["PORT_OF_LOADING", "PORT_OF_DISCHARGE"]:
        containers[column] = containers[column].apply(
            lambda value: "UNKNOWN"
            if pd.isna(value) or isinstance(value, (pd.Timestamp, np.datetime64)) or hasattr(value, "year")
            else str(value).strip() or "UNKNOWN"
        )

    lifecycle_dates = [
        "PLACE_OF_DISPATCH_DATE",
        "PORT_OF_LOADING_DATE",
        "PORT_OF_DISCHARGE_DATE",
        "POST_PORT_OF_DISCHARGE_DATE",
        "LAST_TRACKED_WITH_VESSEL",
        "DELIVERED_DATE",
    ]
    for column in lifecycle_dates:
        containers[column] = pd.to_datetime(containers[column], errors="coerce")
        containers.loc[containers[column].dt.year < 2000, column] = pd.NaT
        containers[column] = containers[column] + pd.DateOffset(years=4)

    invalid_dispatch = containers["PLACE_OF_DISPATCH_DATE"].isna()
    reject_rows("Container Tracking Data.xlsx", containers, invalid_dispatch, "MISSING_OR_INVALID_DISPATCH_DATE", "container_record_id")
    too_old = containers["PLACE_OF_DISPATCH_DATE"] < pd.Timestamp("2024-01-01")
    reject_rows("Container Tracking Data.xlsx", containers, ~invalid_dispatch & too_old, "DISPATCH_BEFORE_CANONICAL_WINDOW", "container_record_id")
    too_new = containers["PLACE_OF_DISPATCH_DATE"] > CUTOFF_EOD
    reject_rows("Container Tracking Data.xlsx", containers, too_new, "DISPATCH_AFTER_CUTOFF", "container_record_id")
    containers = containers.loc[~(invalid_dispatch | too_old | too_new)].copy()

    for column in lifecycle_dates[1:]:
        containers.loc[containers[column] > CUTOFF_EOD, column] = pd.NaT
    containers.loc[containers["DELIVERED_DATE"].isna(), "DELIVERED_FLAG"] = "No"
    containers = containers.drop(columns=["PREDICTED_DELIVERED_DATE", "Another NEW Predicated Delivery Date"])

    records = []
    for row in containers.itertuples(index=False, name=None):
        output_row = []
        for value in row:
            if pd.isna(value):
                output_row.append(None)
            elif isinstance(value, (pd.Timestamp, np.datetime64)) or hasattr(value, "isoformat"):
                output_row.append({"__date__": pd.Timestamp(value).isoformat()})
            elif isinstance(value, np.generic):
                output_row.append(value.item())
            else:
                output_row.append(value)
        records.append(output_row)
    definition_rows = []
    for row in definitions.itertuples(index=False, name=None):
        output_row = []
        for value in row:
            if pd.isna(value):
                output_row.append(None)
            elif isinstance(value, (pd.Timestamp, np.datetime64)) or hasattr(value, "isoformat"):
                output_row.append(str(value))
            elif isinstance(value, np.generic):
                output_row.append(value.item())
            else:
                output_row.append(value)
        definition_rows.append(output_row)
    payload = {
        "columns": list(containers.columns),
        "rows": records,
        "definitions_columns": list(definitions.columns),
        "definitions_rows": definition_rows,
    }
    CONTAINER_INTERMEDIATE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # The canonical workbook was previously rendered and validated from this payload.
    # Preserve that workbook during Phase 1 rebuilds until workbook materialization is automated.
    shutil.copy2(CANONICAL / "Container Tracking Data.xlsx", STAGING / "Container Tracking Data.xlsx")

    # This workbook is already correctly conformed (+12-year OrderList shift). Preserve
    # the validated canonical copy until its rule is migrated to the workbook writer.
    shutil.copy2(CANONICAL / "Supply chain logisitcs problem.xlsx", STAGING / "Supply chain logisitcs problem.xlsx")


def build_emails(dataco: pd.DataFrame) -> None:
    generate_business_emails(STAGING, dataco, VERSION, CUTOFF_EOD, CANONICAL)


def build_restricted_contacts(dataco: pd.DataFrame) -> None:
    columns = [
        "contact_id", "source_system", "source_customer_id", "customer_name", "email", "email_status",
        "email_shared_customer_count", "email_send_eligible", "phone", "phone_send_eligible",
        "street_address", "city", "state_region", "postal_code", "country", "pii_classification",
        "rag_index_allowed", "source_file",
    ]

    indian = pd.read_csv(RAW / "Indian E-Commerce Sales Analytics Dataset" / "customers.csv", dtype={"Customer_ID": str, "Pincode": str})
    indian_contacts = pd.DataFrame(
        {
            "contact_id": "INDIAECOM-CUSTOMER-" + indian["Customer_ID"].astype(str),
            "source_system": "IndianEComm",
            "source_customer_id": indian["Customer_ID"].astype(str),
            "customer_name": indian["Customer_Name"],
            "email": indian["Email"],
            "phone": indian["Phone"],
            "street_address": "",
            "city": indian["City"],
            "state_region": indian["State"],
            "postal_code": indian["Pincode"],
            "country": "India",
            "source_file": "Indian E-Commerce Sales Analytics Dataset/customers.csv",
        }
    )

    blinkit = pd.read_csv(RAW / "Blinkit_Sales_dataset" / "blinkit_customers.csv", dtype={"customer_id": str})
    blinkit_contacts = pd.DataFrame(
        {
            "contact_id": "BLINKIT-CUSTOMER-" + blinkit["customer_id"].astype(str),
            "source_system": "Blinkit",
            "source_customer_id": blinkit["customer_id"].astype(str),
            "customer_name": blinkit["customer_name"],
            "email": blinkit["email"],
            "phone": blinkit["phone"],
            "street_address": blinkit["address"],
            "city": "",
            "state_region": "",
            "postal_code": "",
            "country": "India",
            "source_file": "Blinkit_Sales_dataset/blinkit_customers.csv",
        }
    )

    raw_dataco = pd.read_csv(RAW / "DataCoSC" / "DataCoSupplyChainDataset.csv", encoding="ISO-8859-1", low_memory=False)
    retained_customer_ids = set(dataco["Customer Id"].astype(str))
    raw_dataco = raw_dataco[raw_dataco["Customer Id"].astype(str).isin(retained_customer_ids)].copy()
    raw_dataco = raw_dataco.sort_values(["Customer Id", "Order Item Id"]).drop_duplicates("Customer Id", keep="first")
    names = (
        raw_dataco["Customer Fname"].fillna("").astype(str).str.strip()
        + " "
        + raw_dataco["Customer Lname"].fillna("").astype(str).str.strip()
    ).str.strip()
    dataco_contacts = pd.DataFrame(
        {
            "contact_id": "DATACO-CUSTOMER-" + raw_dataco["Customer Id"].astype(str),
            "source_system": "DataCo",
            "source_customer_id": raw_dataco["Customer Id"].astype(str),
            "customer_name": names,
            "email": raw_dataco["Customer Email"],
            "phone": "",
            "street_address": raw_dataco["Customer Street"],
            "city": raw_dataco["Customer City"],
            "state_region": raw_dataco["Customer State"],
            "postal_code": raw_dataco["Customer Zipcode"].apply(lambda value: "" if pd.isna(value) else str(int(value))),
            "country": raw_dataco["Customer Country"],
            "source_file": "DataCoSC/DataCoSupplyChainDataset.csv",
        }
    )

    contacts = pd.concat([indian_contacts, blinkit_contacts, dataco_contacts], ignore_index=True)
    contacts["email"] = contacts["email"].fillna("").astype(str).str.strip()
    contacts["phone"] = contacts["phone"].fillna("").astype(str).str.strip()

    def email_status(value: str) -> str:
        if not value:
            return "MISSING"
        compact = value.replace("*", "X").replace("_", "X").upper()
        if compact and set(compact) <= {"X"}:
            return "SOURCE_REDACTED"
        if not EMAIL_PATTERN.fullmatch(value):
            return "INVALID_FORMAT"
        return "VALID_FORMAT"

    contacts["email_status"] = contacts["email"].apply(email_status)
    contacts["email_shared_customer_count"] = contacts.groupby(["source_system", "email"])["contact_id"].transform("count")
    contacts.loc[contacts["email"] == "", "email_shared_customer_count"] = 0
    contacts["email_send_eligible"] = (
        contacts["email_status"].eq("VALID_FORMAT") & contacts["email_shared_customer_count"].eq(1)
    )
    contacts["phone_send_eligible"] = contacts["phone"].str.replace(r"\D", "", regex=True).str.len().ge(10)
    contacts["pii_classification"] = "RESTRICTED_OPERATIONAL"
    contacts["rag_index_allowed"] = False
    contacts = contacts[columns].sort_values(["source_system", "source_customer_id"]).reset_index(drop=True)
    write_csv(contacts, "restricted/customer_contacts.csv")

    access_policy = {
        "classification": "RESTRICTED_OPERATIONAL_PII",
        "canonical_file": "restricted/customer_contacts.csv",
        "join_contract": ["source_system", "source_customer_id"],
        "allowed_full_read_roles": ["customer_support", "authorized_logistics"],
        "allowed_send_roles": ["customer_support"],
        "masked_display_by_default": True,
        "explicit_confirmation_required_before_send": True,
        "audit_every_reveal_and_send": True,
        "prohibited": [
            "vector_embedding",
            "bulk_prompt_inclusion",
            "semantic_cache_storage",
            "evaluation_dataset_copy",
            "unredacted_application_logs",
        ],
        "send_rule": "Use contact_id/order context. Resolve the destination server-side only after authorization and confirmation.",
        "eligibility_rule": "Email must have valid format and be unique within its source system. Shared or redacted addresses require manual resolution.",
    }
    policy_path = STAGING / "restricted" / "access_policy.json"
    policy_path.write_text(json.dumps(access_policy, indent=2), encoding="utf-8")
    (STAGING / "restricted" / ".gitignore").write_text("customer_contacts.csv\n", encoding="utf-8")
    (STAGING / "restricted" / "README.md").write_text(
        "# Restricted operational contacts\n\n"
        "`customer_contacts.csv` contains canonical operational PII. Do not embed it, copy it into "
        "evaluation data, place it in prompts, or commit it to source control. Join through "
        "`source_system` + `source_customer_id`, display masked values by default, and resolve full "
        "destinations only inside an authorized, audited server-side action.\n",
        encoding="utf-8",
    )

    rag_policy = {
        "default": "include",
        "exclude_globs": [
            "restricted/**",
            "governance/**",
        ],
        "reason": "Restricted operational PII and governance artifacts must not enter embeddings or ordinary RAG context.",
    }
    (STAGING / "governance").mkdir(parents=True, exist_ok=True)
    (STAGING / "governance" / "rag_index_policy.json").write_text(json.dumps(rag_policy, indent=2), encoding="utf-8")


def write_metadata() -> None:
    rejected = pd.DataFrame(rejections, columns=["source_file", "source_row_number", "source_record_id", "reason"])
    write_csv(rejected, "governance/phase1_rejected_records.csv")
    policy = {
        "dataset_name": "SKOPE Phase 1 Canonical Ground Truth",
        "version": VERSION,
        "as_of_cutoff": CUTOFF_EOD.isoformat(),
        "canonical_root": str(CANONICAL),
        "source_archive": str(RAW),
        "source_archive_role": "IMMUTABLE_PROVENANCE_ONLY_NOT_APPLICATION_GROUND_TRUTH",
        "scope": "Phase 1 tabular data and generated email JSON",
        "phase2_untouched": ["Supplier Contract30", "Purchase Order1600", "Manifest-BoL1200"],
        "excluded_source_files": {
            "Blinkit_Sales_dataset/blinkit_inventoryNew.csv": "Quarantined alternative inventory file with extensive exact duplicates",
            "Blinkit_Sales_dataset/blinkit_marketing_performance.csv": "Marketing analytics outside supply-chain Phase 1 scope",
            "DataCoSC/tokenized_access_logs.csv": "Web/security telemetry outside supply-chain Phase 1 scope",
            "Blinkit_Sales_dataset/Category_Icons.xlsx": "UI asset lookup outside analytical ground truth",
            "Blinkit_Sales_dataset/Rating_Icon.xlsx": "UI asset lookup outside analytical ground truth",
        },
        "canonicalization_rules": [
            "All cutoff checks include the full day 2026-07-31.",
            "Related Blinkit order and delivery timestamps are shifted together by two calendar years.",
            "Future outcomes after cutoff are removed or labelled as-of-cutoff rather than leaked.",
            "DataCo Order Item Id is the canonical order-line identifier.",
            "Container prediction fields are excluded because prediction is outside project scope.",
            "Generated email JSON is synthetic and may state only facts present in its linked structured rows.",
            "EmailJson2500 contains 2,500 deterministic threads across fulfillment, delivery, inventory, order management, freight, container tracking, and shipping operations.",
            "Synthetic email namespaces identify their source family; no cross-dataset relationship is asserted without a shared key.",
            "Every filtered source row is recorded in governance/phase1_rejected_records.csv.",
            "Operational contact PII is preserved in restricted/customer_contacts.csv and excluded from vector indexing.",
            "Full contact values are resolved only by an authorized server-side action flow.",
        ],
    }
    (STAGING / "governance" / "ground_truth_policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")


def main() -> None:
    backup_phase1()
    reset_staging()
    build_indian()
    build_blinkit()
    dataco = build_dataco()
    build_other_csvs()
    prepare_container_intermediate()
    build_emails(dataco)
    build_restricted_contacts(dataco)
    write_metadata()
    print(f"Staged Phase 1 ground truth at {STAGING}")
    print(f"Recorded rejected/excluded source rows: {len(rejections)}")


if __name__ == "__main__":
    main()
