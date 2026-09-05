"""Rebuild and load the canonical SKOPE PostgreSQL database from harmonized_data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]

import psycopg


ROOT = BASE / "RAG Project Dataset" / "harmonized_data"
SCHEMA_SQL = BASE / "database" / "skope_schema.sql"
RESET_SQL = BASE / "database" / "reset_skope.sql"
ACCESS_CONTROL_SQL = BASE / "database" / "access_control.sql"
CUTOFF = pd.Timestamp("2026-07-31")
DATASET_VERSION = "phase1-v3"
TRANSFORMATION_VERSION = "3.0.0-db"
DEFAULT_DATABASE_URL = "postgresql://skope_admin:skope_pass_secure@localhost:5432/skope_db"


def stable_key(source_system: str, entity_type: str, source_id: object) -> str:
    value = f"{source_system}:{entity_type}:{source_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sid(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def nullable(value: object):
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def to_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def read_csv(relative: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(ROOT / relative, low_memory=False, **kwargs)


def copy_frame(conn: psycopg.Connection, table: str, frame: pd.DataFrame) -> int:
    columns = list(frame.columns)
    quoted = ", ".join(f'"{column}"' for column in columns)
    statement = f"COPY {table} ({quoted}) FROM STDIN"
    with conn.cursor() as cursor:
        with cursor.copy(statement) as copy:
            for row in frame.itertuples(index=False, name=None):
                copy.write_row(tuple(nullable(value) for value in row))
    print(f"Loaded {len(frame):>8,} rows -> {table}")
    return len(frame)


def provenance_columns(frame: pd.DataFrame, *, synthetic: bool = False, method: str = "DIRECT_MAP") -> pd.DataFrame:
    frame["is_synthetic"] = synthetic
    frame["mapping_method"] = method
    frame["transformation_version"] = TRANSFORMATION_VERSION
    frame["dataset_as_of"] = CUTOFF.date()
    return frame


def build_dates() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", CUTOFF, freq="D")
    return pd.DataFrame({
        "date_key": dates.date,
        "day_of_week": dates.dayofweek + 1,
        "day_of_month": dates.day,
        "month": dates.month,
        "quarter": dates.quarter,
        "year": dates.year,
        "is_weekend": dates.dayofweek >= 5,
    })


def build_products() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    indian = read_csv("Indian E-Commerce Sales Analytics Dataset/products.csv", dtype={"Product_ID": str})
    frame = pd.DataFrame({
        "source_system": "IndianEComm",
        "source_product_id": indian["Product_ID"].map(sid),
        "product_name": indian["Product_Name"].fillna("Unknown product"),
        "category": indian["Category"],
        "brand": indian["Brand"],
        "unit_price_original": to_number(indian["Selling_Price"]),
        "currency_code": "INR",
        "mrp_original": to_number(indian["Original_Price"]),
        "attributes": [json.dumps({
            "stock_quantity": nullable(stock), "weight_kg": nullable(weight),
            "average_rating": nullable(rating), "total_reviews": nullable(reviews),
        }) for stock, weight, rating, reviews in zip(
            to_number(indian["Stock_Quantity"]), to_number(indian["Weight_kg"]),
            to_number(indian["Avg_Rating"]), to_number(indian["Total_Reviews"]), strict=True)],
    })
    frames.append(frame)

    blinkit = read_csv("Blinkit_Sales_dataset/blinkit_products.csv", dtype={"product_id": str})
    frame = pd.DataFrame({
        "source_system": "Blinkit",
        "source_product_id": blinkit["product_id"].map(sid),
        "product_name": blinkit["product_name"].fillna("Unknown product"),
        "category": blinkit["category"],
        "brand": blinkit["brand"],
        "unit_price_original": to_number(blinkit["price"]),
        "currency_code": "INR",
        "mrp_original": to_number(blinkit["mrp"]),
        "attributes": [json.dumps({
            "margin_percentage": nullable(margin), "shelf_life_days": nullable(shelf),
            "min_stock_level": nullable(minimum), "max_stock_level": nullable(maximum),
        }) for margin, shelf, minimum, maximum in zip(
            to_number(blinkit["margin_percentage"]), to_number(blinkit["shelf_life_days"]),
            to_number(blinkit["min_stock_level"]), to_number(blinkit["max_stock_level"]), strict=True)],
    })
    frames.append(frame)

    dataco = read_csv("DataCoSC/DataCoSupplyChainDataset.csv", dtype=str)
    dataco = dataco.sort_values("Order Item Id").drop_duplicates("Product Card Id")
    frame = pd.DataFrame({
        "source_system": "DataCo",
        "source_product_id": dataco["Product Card Id"].map(sid),
        "product_name": dataco["Product Name"].fillna("Unknown product"),
        "category": dataco["Category Name"],
        "brand": None,
        "unit_price_original": to_number(dataco["Product Price"]),
        "currency_code": "SOURCE_UNSPECIFIED",
        "mrp_original": None,
        "attributes": [json.dumps({"department": nullable(value)}) for value in dataco["Department Name"]],
    })
    frames.append(frame)

    flipkart = read_csv("Flipkart Sales Data/products.csv", dtype={"product_id": str})
    frame = pd.DataFrame({
        "source_system": "FlipkartCatalog",
        "source_product_id": flipkart["product_id"].map(sid),
        "product_name": flipkart["product_name"].fillna("Unknown product"),
        "category": flipkart["l0_category"],
        "brand": flipkart["brand_name"],
        "unit_price_original": None,
        "currency_code": None,
        "mrp_original": None,
        "attributes": [json.dumps({
            "unit": nullable(unit), "product_type": nullable(kind), "manufacturer": nullable(manufacturer),
            "category_l1": nullable(l1), "category_l2": nullable(l2),
        }) for unit, kind, manufacturer, l1, l2 in zip(
            flipkart["unit"], flipkart["product_type"], flipkart["manufacturer_name"],
            flipkart["l1_category"], flipkart["l2_category"], strict=True)],
    })
    frames.append(frame)

    result = pd.concat(frames, ignore_index=True)
    result.insert(0, "product_key", [stable_key(s, "product", p) for s, p in zip(result.source_system, result.source_product_id, strict=True)])
    return provenance_columns(result)


def build_customers() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    indian = read_csv("Indian E-Commerce Sales Analytics Dataset/customers.csv", dtype=str)
    frames.append(pd.DataFrame({
        "source_system": "IndianEComm", "source_customer_id": indian["Customer_ID"].map(sid),
        "display_name": indian["Customer_Name"].fillna("Unknown customer"), "customer_segment": indian["Customer_Tier"],
        "area": None, "city": indian["City"], "state_province": indian["State"], "country": "India",
        "registration_date": to_date(indian["Registration_Date"]),
        "contact_id": "INDIAECOM-CUSTOMER-" + indian["Customer_ID"].map(sid),
    }))

    blinkit = read_csv("Blinkit_Sales_dataset/blinkit_customers.csv", dtype=str)
    frames.append(pd.DataFrame({
        "source_system": "Blinkit", "source_customer_id": blinkit["customer_id"].map(sid),
        "display_name": blinkit["customer_name"].fillna("Unknown customer"), "customer_segment": blinkit["customer_segment"],
        "area": blinkit["area"], "city": None, "state_province": None, "country": "India",
        "registration_date": to_date(blinkit["registration_date"]),
        "contact_id": "BLINKIT-CUSTOMER-" + blinkit["customer_id"].map(sid),
    }))

    dataco = read_csv("DataCoSC/DataCoSupplyChainDataset.csv", dtype=str)
    dataco = dataco.sort_values("Order Item Id").drop_duplicates("Customer Id")
    frames.append(pd.DataFrame({
        "source_system": "DataCo", "source_customer_id": dataco["Customer Id"].map(sid),
        "display_name": (dataco["Customer Fname"].fillna("") + " " + dataco["Customer Lname"].fillna("")).str.strip().replace("", "Unknown customer"),
        "customer_segment": dataco["Customer Segment"], "area": None, "city": dataco["Customer City"],
        "state_province": dataco["Customer State"], "country": dataco["Customer Country"],
        "registration_date": None, "contact_id": "DATACO-CUSTOMER-" + dataco["Customer Id"].map(sid),
    }))

    result = pd.concat(frames, ignore_index=True)
    result.insert(0, "customer_key", [stable_key(s, "customer", c) for s, c in zip(result.source_system, result.source_customer_id, strict=True)])
    return provenance_columns(result)


def build_locations() -> pd.DataFrame:
    rows: list[dict] = []

    blinkit = read_csv("Blinkit_Sales_dataset/blinkit_orders.csv", dtype=str)
    for store_id in sorted(blinkit["store_id"].dropna().map(sid).unique()):
        rows.append({"source_system": "Blinkit", "source_location_id": store_id, "location_type": "DARK_STORE", "facility_name": f"Store {store_id}"})

    workbook = ROOT / "Supply chain logisitcs problem.xlsx"
    plants = pd.read_excel(workbook, sheet_name="WhCapacities", dtype=str)
    for plant_id in sorted(plants["Plant ID"].dropna().map(sid).unique()):
        rows.append({"source_system": "SupplyChainLogistics", "source_location_id": plant_id, "location_type": "DC_PLANT", "facility_name": plant_id})
    orders = pd.read_excel(workbook, sheet_name="OrderList", dtype=str)
    port_ids = sorted(set(orders["Origin Port"].dropna().map(sid)) | set(orders["Destination Port"].dropna().map(sid)))
    for port_id in port_ids:
        rows.append({"source_system": "SupplyChainLogistics", "source_location_id": port_id, "location_type": "PORT", "facility_name": port_id})

    containers = pd.read_excel(ROOT / "Container Tracking Data.xlsx", sheet_name="Data with Main Column", dtype=str)
    container_ports = sorted(set(containers["PORT_OF_LOADING"].dropna().map(sid)) | set(containers["PORT_OF_DISCHARGE"].dropna().map(sid)))
    for port_name in container_ports:
        rows.append({"source_system": "ContainerTracking", "source_location_id": port_name, "location_type": "PORT", "facility_name": port_name})

    shipping = read_csv("shipping_data.csv", dtype=str)
    for port_name in sorted(shipping["destination port"].dropna().map(sid).unique()):
        rows.append({"source_system": "ShippingData", "source_location_id": port_name, "location_type": "PORT", "facility_name": port_name})

    result = pd.DataFrame(rows).drop_duplicates(["source_system", "source_location_id"])
    result["location_key"] = [stable_key(s, "location", p) for s, p in zip(result.source_system, result.source_location_id, strict=True)]
    for column in ["city", "state_province", "country", "latitude", "longitude"]:
        result[column] = None
    result = result[["location_key", "source_system", "source_location_id", "location_type", "facility_name", "city", "state_province", "country", "latitude", "longitude"]]
    return provenance_columns(result, method="SOURCE_ID_ONLY")


def build_partners() -> pd.DataFrame:
    rows: list[dict] = []
    delivery = read_csv("Blinkit_Sales_dataset/blinkit_delivery_performance.csv", dtype=str)
    for partner_id in sorted(delivery["delivery_partner_id"].dropna().map(sid).unique()):
        rows.append({"source_system": "Blinkit", "source_partner_id": partner_id, "partner_name": f"Delivery Partner {partner_id}", "partner_type": "DELIVERY_PARTNER", "attributes": "{}", "mapping_method": "SOURCE_ID_ONLY"})

    workbook = ROOT / "Supply chain logisitcs problem.xlsx"
    orders = pd.read_excel(workbook, sheet_name="OrderList", dtype=str)
    for carrier in sorted(orders["Carrier"].dropna().map(sid).unique()):
        rows.append({"source_system": "SupplyChainLogistics", "source_partner_id": carrier, "partner_name": carrier, "partner_type": "CARRIER", "attributes": "{}", "mapping_method": "DIRECT_MAP"})

    containers = pd.read_excel(ROOT / "Container Tracking Data.xlsx", sheet_name="Data with Main Column", dtype=str)
    for forwarder in sorted(containers["FREIGHT_FORWARDER"].dropna().map(sid).unique()):
        rows.append({"source_system": "ContainerTracking", "source_partner_id": forwarder, "partner_name": forwarder, "partner_type": "FREIGHT_FORWARDER", "attributes": "{}", "mapping_method": "DIRECT_MAP"})

    result = pd.DataFrame(rows).drop_duplicates(["source_system", "source_partner_id"])
    result["partner_key"] = [stable_key(s, "partner", p) for s, p in zip(result.source_system, result.source_partner_id, strict=True)]
    result["is_synthetic"] = False
    result["transformation_version"] = TRANSFORMATION_VERSION
    result["dataset_as_of"] = CUTOFF.date()
    return result[["partner_key", "source_system", "source_partner_id", "partner_name", "partner_type", "attributes", "is_synthetic", "mapping_method", "transformation_version", "dataset_as_of"]]


def build_orders() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    sales = read_csv("Indian E-Commerce Sales Analytics Dataset/sales.csv", dtype=str)
    line_ids = sales["Order_ID"].map(sid)
    frame = pd.DataFrame({
        "source_system": "IndianEComm", "source_line_id": line_ids, "order_id": line_ids,
        "display_order_id": "INDIAECOM-ORDER-" + line_ids,
        "customer_key": [stable_key("IndianEComm", "customer", value) for value in sales["Customer_ID"].map(sid)],
        "product_key": [stable_key("IndianEComm", "product", value) for value in sales["Product_ID"].map(sid)],
        "origin_location_key": None, "order_date": to_date(sales["Order_Date"]), "quantity": to_number(sales["Quantity"]),
        "unit_price_original": to_number(sales["Unit_Price"]), "gross_amount_original": to_number(sales["Order_Value"]),
        "discount_original": to_number(sales["Coupon_Discount"]), "net_amount_original": to_number(sales["Total_Amount"]),
        "shipping_cost_original": to_number(sales["Shipping_Cost"]), "profit_original": None, "currency_code": "INR",
        "order_status": sales["Order_Status"], "payment_method": sales["Payment_Mode"], "source_status": sales["Order_Status"],
        "destination_city": sales["City"], "destination_state": sales["State"], "destination_country": "India",
    })
    frames.append(frame)

    orders = read_csv("Blinkit_Sales_dataset/blinkit_orders.csv", dtype=str)
    items = read_csv("Blinkit_Sales_dataset/blinkit_order_items.csv", dtype=str)
    merged = items.merge(orders, on="order_id", how="inner", validate="one_to_one")
    line_ids = merged["order_id"].map(sid) + ":" + merged["product_id"].map(sid)
    frame = pd.DataFrame({
        "source_system": "Blinkit", "source_line_id": line_ids, "order_id": merged["order_id"].map(sid),
        "display_order_id": "BLINKIT-ORDER-" + merged["order_id"].map(sid),
        "customer_key": [stable_key("Blinkit", "customer", value) for value in merged["customer_id"].map(sid)],
        "product_key": [stable_key("Blinkit", "product", value) for value in merged["product_id"].map(sid)],
        "origin_location_key": [stable_key("Blinkit", "location", value) for value in merged["store_id"].map(sid)],
        "order_date": to_date(merged["order_date"]), "quantity": to_number(merged["quantity"]),
        "unit_price_original": to_number(merged["unit_price"]),
        "gross_amount_original": to_number(merged["quantity"]) * to_number(merged["unit_price"]),
        "discount_original": None, "net_amount_original": to_number(merged["order_total"]),
        "shipping_cost_original": None, "profit_original": None, "currency_code": "INR",
        "order_status": merged["delivery_status"], "payment_method": merged["payment_method"], "source_status": merged["delivery_status"],
        "destination_city": None, "destination_state": None, "destination_country": "India",
    })
    frames.append(frame)

    dataco = read_csv("DataCoSC/DataCoSupplyChainDataset.csv", dtype=str)
    line_ids = dataco["Order Item Id"].map(sid)
    order_ids = dataco["Order Id"].map(sid)
    frame = pd.DataFrame({
        "source_system": "DataCo", "source_line_id": line_ids, "order_id": order_ids,
        "display_order_id": "DATACO-ORDER-" + order_ids,
        "customer_key": [stable_key("DataCo", "customer", value) for value in dataco["Customer Id"].map(sid)],
        "product_key": [stable_key("DataCo", "product", value) for value in dataco["Product Card Id"].map(sid)],
        "origin_location_key": None, "order_date": to_date(dataco["order date (DateOrders)"]),
        "quantity": to_number(dataco["Order Item Quantity"]), "unit_price_original": to_number(dataco["Order Item Product Price"]),
        "gross_amount_original": to_number(dataco["Sales"]), "discount_original": to_number(dataco["Order Item Discount"]),
        "net_amount_original": to_number(dataco["Order Item Total"]), "shipping_cost_original": None,
        "profit_original": to_number(dataco["Order Profit Per Order"]), "currency_code": "SOURCE_UNSPECIFIED",
        "order_status": dataco["Order Status"], "payment_method": dataco["Type"], "source_status": dataco["Order Status"],
        "destination_city": dataco["Order City"], "destination_state": dataco["Order State"], "destination_country": dataco["Order Country"],
    })
    frames.append(frame)

    result = pd.concat(frames, ignore_index=True)
    result.insert(0, "order_fact_id", [stable_key(s, "order_line", line) for s, line in zip(result.source_system, result.source_line_id, strict=True)])
    return provenance_columns(result)


def build_deliveries() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    sales = read_csv("Indian E-Commerce Sales Analytics Dataset/sales.csv", dtype=str)
    order_ids = sales["Order_ID"].map(sid)
    frames.append(pd.DataFrame({
        "source_system": "IndianEComm", "source_record_id": order_ids, "order_id": order_ids,
        "display_order_id": "INDIAECOM-ORDER-" + order_ids, "partner_key": None, "origin_location_key": None,
        "promised_time": None, "actual_time": to_datetime(sales["Delivery_Date"]), "delivery_time_minutes": None,
        "distance_km": None, "delivery_status": sales["Order_Status"], "source_status": sales["Order_Status"],
        "reason_if_delayed": None, "delivery_mode": None, "is_late": None,
    }))

    orders = read_csv("Blinkit_Sales_dataset/blinkit_orders.csv", dtype=str)
    delivery = read_csv("Blinkit_Sales_dataset/blinkit_delivery_performance.csv", dtype=str)
    merged = orders.merge(delivery, on=["order_id", "delivery_partner_id"], how="inner", validate="one_to_one")
    order_ids = merged["order_id"].map(sid)
    frames.append(pd.DataFrame({
        "source_system": "Blinkit", "source_record_id": order_ids, "order_id": order_ids,
        "display_order_id": "BLINKIT-ORDER-" + order_ids,
        "partner_key": [stable_key("Blinkit", "partner", value) for value in merged["delivery_partner_id"].map(sid)],
        "origin_location_key": [stable_key("Blinkit", "location", value) for value in merged["store_id"].map(sid)],
        "promised_time": to_datetime(merged["promised_time"]), "actual_time": to_datetime(merged["actual_time"]),
        "delivery_time_minutes": to_number(merged["delivery_time_minutes"]), "distance_km": to_number(merged["distance_km"]),
        "delivery_status": merged["delivery_status_y"], "source_status": merged["delivery_status_y"],
        "reason_if_delayed": merged["reasons_if_delayed"], "delivery_mode": "LAST_MILE",
        "is_late": merged["delivery_status_y"].str.contains("Delayed", case=False, na=False),
    }))

    dataco = read_csv("DataCoSC/DataCoSupplyChainDataset.csv", dtype=str)
    dataco = dataco.sort_values("Order Item Id").drop_duplicates("Order Id")
    order_ids = dataco["Order Id"].map(sid)
    order_time = to_datetime(dataco["order date (DateOrders)"])
    scheduled_days = to_number(dataco["Days for shipment (scheduled)"])
    actual_days = to_number(dataco["Days for shipping (real)"])
    frames.append(pd.DataFrame({
        "source_system": "DataCo", "source_record_id": order_ids, "order_id": order_ids,
        "display_order_id": "DATACO-ORDER-" + order_ids, "partner_key": None, "origin_location_key": None,
        "promised_time": order_time + pd.to_timedelta(scheduled_days, unit="D"),
        "actual_time": to_datetime(dataco["shipping date (DateOrders)"]),
        "delivery_time_minutes": actual_days * 1440, "distance_km": None,
        "delivery_status": dataco["Delivery Status"], "source_status": dataco["Delivery Status"],
        "reason_if_delayed": None, "delivery_mode": dataco["Shipping Mode"],
        "is_late": dataco["Delivery Status"].str.casefold().eq("late delivery"),
    }))

    result = pd.concat(frames, ignore_index=True)
    result.insert(0, "delivery_fact_id", [stable_key(s, "delivery", record) for s, record in zip(result.source_system, result.source_record_id, strict=True)])
    return provenance_columns(result)


def build_inventory() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    inventory = read_csv("Blinkit_Sales_dataset/blinkit_inventory.csv", dtype=str)
    event_ids = inventory["inventory_event_id"].map(sid)
    frames.append(pd.DataFrame({
        "source_system": "Blinkit", "source_event_id": event_ids,
        "product_key": [stable_key("Blinkit", "product", value) for value in inventory["product_id"].map(sid)],
        "location_key": None, "event_date": to_date(inventory["date"]), "event_type": "RECEIPT_AND_DAMAGE",
        "quantity_received": to_number(inventory["stock_received"]), "quantity_damaged": to_number(inventory["damaged_stock"]),
        "quantity_on_hand": None,
    }))
    products = read_csv("Indian E-Commerce Sales Analytics Dataset/products.csv", dtype=str)
    product_ids = products["Product_ID"].map(sid)
    frames.append(pd.DataFrame({
        "source_system": "IndianEComm", "source_event_id": "SNAPSHOT-" + product_ids,
        "product_key": [stable_key("IndianEComm", "product", value) for value in product_ids],
        "location_key": None, "event_date": CUTOFF.date(), "event_type": "ON_HAND_SNAPSHOT",
        "quantity_received": None, "quantity_damaged": None, "quantity_on_hand": to_number(products["Stock_Quantity"]),
    }))
    result = pd.concat(frames, ignore_index=True)
    result.insert(0, "inventory_event_key", [stable_key(s, "inventory_event", event) for s, event in zip(result.source_system, result.source_event_id, strict=True)])
    return provenance_columns(result)


def build_freight_orders() -> pd.DataFrame:
    frame = pd.read_excel(ROOT / "Supply chain logisitcs problem.xlsx", sheet_name="OrderList", dtype=str)
    record_ids = frame["Order ID"].map(sid)
    carrier_ids = frame["Carrier"].map(sid)
    result = pd.DataFrame({
        "freight_order_key": [stable_key("SupplyChainLogistics", "freight_order", value) for value in record_ids],
        "source_system": "SupplyChainLogistics", "source_record_id": record_ids,
        "display_order_id": "LOGISTICS-ORDER-" + record_ids, "order_date": to_date(frame["Order Date"]),
        "carrier_key": [stable_key("SupplyChainLogistics", "partner", value) for value in carrier_ids],
        "plant_location_key": [stable_key("SupplyChainLogistics", "location", value) for value in frame["Plant Code"].map(sid)],
        "origin_port_key": [stable_key("SupplyChainLogistics", "location", value) for value in frame["Origin Port"].map(sid)],
        "destination_port_key": [stable_key("SupplyChainLogistics", "location", value) for value in frame["Destination Port"].map(sid)],
        "customer_reference": frame["Customer"], "product_reference": frame["Product ID"], "service_level": frame["Service Level"],
        "transport_days": to_number(frame["TPT"]), "ship_ahead_days": to_number(frame["Ship ahead day count"]).astype("Int64"),
        "ship_late_days": to_number(frame["Ship Late Day count"]).astype("Int64"), "unit_quantity": to_number(frame["Unit quantity"]),
        "weight": to_number(frame["Weight"]),
    })
    return provenance_columns(result)


def build_containers() -> pd.DataFrame:
    frame = pd.read_excel(ROOT / "Container Tracking Data.xlsx", sheet_name="Data with Main Column", dtype=str)
    record_ids = frame["container_record_id"].map(sid)
    delivered = frame["DELIVERED_FLAG"].map(lambda value: True if sid(value).casefold() == "yes" else False if sid(value).casefold() == "no" else None)
    result = pd.DataFrame({
        "container_tracking_key": [stable_key("ContainerTracking", "container_tracking", value) for value in record_ids],
        "source_system": "ContainerTracking", "source_record_id": record_ids, "container_number": frame["CONTAINER_NUMBER"].map(sid),
        "vessel_name": frame["VESSEL_NAME"], "freight_forwarder": frame["FREIGHT_FORWARDER"], "linked_order_id": None,
        "origin_port_key": [stable_key("ContainerTracking", "location", value) for value in frame["PORT_OF_LOADING"].map(sid)],
        "destination_port_key": [stable_key("ContainerTracking", "location", value) for value in frame["PORT_OF_DISCHARGE"].map(sid)],
        "place_of_dispatch": frame["PLACE_OF_DISPATCH"], "post_discharge_place": frame["POST_PORT_OF_DISCHARGE"],
        "dispatch_date": to_datetime(frame["PLACE_OF_DISPATCH_DATE"]), "port_loading_date": to_datetime(frame["PORT_OF_LOADING_DATE"]),
        "port_discharge_date": to_datetime(frame["PORT_OF_DISCHARGE_DATE"]), "post_discharge_date": to_datetime(frame["POST_PORT_OF_DISCHARGE_DATE"]),
        "last_tracked_at": to_datetime(frame["LAST_TRACKED_WITH_VESSEL"]), "delivered_flag": delivered,
        "delivered_date": to_datetime(frame["DELIVERED_DATE"]),
    })
    return provenance_columns(result)


def build_shipments() -> pd.DataFrame:
    frame = read_csv("shipping_data.csv", dtype=str)
    record_ids = frame["shipment_record_id"].map(sid)
    result = pd.DataFrame({
        "shipment_record_key": [stable_key("ShippingData", "shipment_record", value) for value in record_ids],
        "source_system": "ShippingData", "source_record_id": record_ids, "item_name": frame["name"],
        "price_original": to_number(frame["price ($)"]), "currency_code": "USD", "weight_kg": to_number(frame["weight (kg)"]),
        "length_m": to_number(frame["length (m)"]), "width_m": to_number(frame["width (m)"]), "height_m": to_number(frame["height (m)"]),
        "shipment_date": to_date(frame["shipment date"]),
        "destination_port_key": [
            stable_key("ShippingData", "location", sid(value)) if pd.notna(value) else None
            for value in frame["destination port"]
        ],
    })
    return provenance_columns(result)


def build_feedback() -> pd.DataFrame:
    frame = read_csv("Blinkit_Sales_dataset/blinkit_customer_feedback.csv", dtype=str)
    feedback_ids = frame["feedback_id"].map(sid)
    order_ids = frame["order_id"].map(sid)
    result = pd.DataFrame({
        "feedback_key": [stable_key("Blinkit", "feedback", value) for value in feedback_ids],
        "source_system": "Blinkit", "source_feedback_id": feedback_ids, "order_id": order_ids,
        "display_order_id": "BLINKIT-ORDER-" + order_ids,
        "customer_key": [stable_key("Blinkit", "customer", value) for value in frame["customer_id"].map(sid)],
        "rating": to_number(frame["rating"]).astype("Int64"), "feedback_text": frame["feedback_text"],
        "feedback_category": frame["feedback_category"], "sentiment": frame["sentiment"],
        "feedback_date": to_date(frame["feedback_date"]), "link_status": "PROVENANCE_LINKED",
    })
    return provenance_columns(result)


def build_communications() -> pd.DataFrame:
    frame = read_csv("GajurelKshitizEcommerce-Chat-Dataset.csv", dtype=str)
    record_ids = pd.Series([f"CHAT-{index + 1:06d}" for index in range(len(frame))])
    result = pd.DataFrame({
        "communication_key": [stable_key("CustomerSupportChat", "communication", value) for value in record_ids],
        "source_system": "CustomerSupportChat", "source_record_id": record_ids, "communication_type": "CHAT",
        "body_text": frame["text"].fillna(""), "linked_order_id": None, "link_status": "UNLINKED",
    })
    return provenance_columns(result, method="UNLINKED_SOURCE_RECORD")


def build_emails() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    threads: list[dict] = []
    messages: list[dict] = []
    documents: list[dict] = []
    for path in sorted((ROOT / "EmailJson2500").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        relative = path.relative_to(ROOT).as_posix()
        primary = payload["primary_entity"]
        provenance = payload["provenance"]
        threads.append({
            "thread_id": payload["ticket_id"], "business_domain": payload["business_domain"], "event_type": payload["event_type"],
            "primary_entity_type": primary["entity_type"], "primary_entity_id": primary["global_id"],
            "source_system": provenance["source_system"], "related_entities": json.dumps(payload["related_entities"]),
            "source_records": json.dumps(provenance["source_records"]), "facts": json.dumps(payload["facts"]),
            "is_synthetic": bool(provenance["is_synthetic"]), "transformation_version": provenance["transformation_version"],
            "source_path": relative, "dataset_as_of": CUTOFF.date(),
        })
        for sequence, message in enumerate(payload["emails"], start=1):
            messages.append({
                "message_id": message["message_id"], "thread_id": payload["ticket_id"], "message_sequence": sequence,
                "sent_at": pd.Timestamp(message["sent_at"]), "sender_address": message["from"], "recipient_address": message["to"],
                "subject": message["subject"], "body_text": message["body"],
            })
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        documents.append({
            "document_id": stable_key("EmailJson", "document", relative), "document_type": "EMAIL_THREAD_JSON", "source_path": relative,
            "source_system": provenance["source_system"], "primary_entity_type": primary["entity_type"], "primary_entity_id": primary["global_id"],
            "sha256": digest, "byte_size": path.stat().st_size, "is_synthetic": True, "rag_index_allowed": True,
            "indexed_at": None, "metadata": json.dumps({"ticket_id": payload["ticket_id"], "business_domain": payload["business_domain"]}),
            "dataset_as_of": CUTOFF.date(),
        })
    return pd.DataFrame(threads), pd.DataFrame(messages), pd.DataFrame(documents)


def build_rejections() -> pd.DataFrame:
    frame = read_csv("governance/phase1_rejected_records.csv", dtype=str)
    result = pd.DataFrame({
        "source_file": frame["source_file"],
        "source_row_number": to_number(frame["source_row_number"]).astype("Int64"),
        "source_record_id": frame["source_record_id"], "rejection_reason": frame["reason"],
    })
    result.insert(0, "rejection_key", [stable_key("RejectionLedger", "rejection", f"{file}:{row}:{record}:{reason}")
        for file, row, record, reason in result.itertuples(index=False, name=None)])
    return result


def build_contacts() -> pd.DataFrame:
    frame = read_csv("restricted/customer_contacts.csv", dtype=str).fillna("")
    return pd.DataFrame({
        "contact_id": frame["contact_id"], "source_system": frame["source_system"],
        "source_customer_id": frame["source_customer_id"], "customer_name": frame["customer_name"],
        "email": frame["email"], "email_status": frame["email_status"],
        "email_shared_customer_count": to_number(frame["email_shared_customer_count"]).fillna(0).astype(int),
        "email_send_eligible": frame["email_send_eligible"].str.casefold().eq("true"),
        "phone": frame["phone"], "phone_send_eligible": frame["phone_send_eligible"].str.casefold().eq("true"),
        "street_address": frame["street_address"], "city": frame["city"], "state_region": frame["state_region"],
        "postal_code": frame["postal_code"], "country": frame["country"], "pii_classification": frame["pii_classification"],
        "rag_index_allowed": frame["rag_index_allowed"].str.casefold().eq("true"), "source_file": frame["source_file"],
    })


def build_manifest() -> pd.DataFrame:
    rows: list[dict] = []
    excluded_roots = {"P2"}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.name.endswith(".inspect.ndjson"):
            continue
        relative = path.relative_to(ROOT)
        if relative.parts[0] in excluded_roots or relative.as_posix() == "governance/phase1_validation_report.json":
            continue
        row_count = None
        if path.suffix.lower() == ".csv":
            row_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace")) - 1
        elif path.suffix.lower() == ".json" and relative.parts[0] == "EmailJson2500":
            row_count = 1
        role = "RESTRICTED" if relative.parts[0] == "restricted" else "GOVERNANCE" if relative.parts[0] == "governance" else "PHASE1_CANONICAL"
        rows.append({"source_path": relative.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "row_count": row_count, "byte_size": path.stat().st_size, "source_role": role})
    manifest = pd.DataFrame(rows)
    manifest["row_count"] = pd.to_numeric(manifest["row_count"], errors="coerce").astype("Int64")
    return manifest


def apply_schema(conn: psycopg.Connection, rebuild: bool) -> None:
    if rebuild:
        conn.execute(RESET_SQL.read_text(encoding="utf-8"), prepare=False)
    conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"), prepare=False)
    conn.execute(ACCESS_CONTROL_SQL.read_text(encoding="utf-8"), prepare=False)
    conn.commit()


def load(database_url: str, rebuild: bool) -> None:
    if not ROOT.exists():
        raise FileNotFoundError(ROOT)
    with psycopg.connect(database_url) as conn:
        apply_schema(conn, rebuild)
        run_id = conn.execute(
            "INSERT INTO skope.etl_run(dataset_version, transformation_version, source_root, status) VALUES (%s,%s,%s,'RUNNING') RETURNING run_id",
            (DATASET_VERSION, TRANSFORMATION_VERSION, str(ROOT)),
        ).fetchone()[0]
        conn.commit()
        counts: dict[str, int] = {}
        try:
            loaders = [
                ("skope.dim_date", build_dates), ("skope.dim_product", build_products),
                ("skope.dim_customer", build_customers), ("skope.dim_location", build_locations),
                ("skope.dim_partner", build_partners), ("skope.fact_order_fulfillment", build_orders),
                ("skope.fact_delivery", build_deliveries), ("skope.fact_inventory_event", build_inventory),
                ("skope.fact_freight_order", build_freight_orders), ("skope.fact_container_tracking", build_containers),
                ("skope.fact_shipment_record", build_shipments), ("skope.fact_customer_feedback", build_feedback),
                ("skope.customer_communication", build_communications),
            ]
            for table, builder in loaders:
                counts[table] = copy_frame(conn, table, builder())
            threads, messages, documents = build_emails()
            counts["skope.email_thread"] = copy_frame(conn, "skope.email_thread", threads)
            counts["skope.email_message"] = copy_frame(conn, "skope.email_message", messages)
            counts["skope.document_index"] = copy_frame(conn, "skope.document_index", documents)
            counts["skope.sys_rejected_record"] = copy_frame(conn, "skope.sys_rejected_record", build_rejections())
            counts["restricted.customer_contact"] = copy_frame(conn, "restricted.customer_contact", build_contacts())
            counts["skope.source_file_manifest"] = copy_frame(conn, "skope.source_file_manifest", build_manifest())
            conn.execute("ANALYZE")
            conn.execute(
                "UPDATE skope.etl_run SET completed_at=CURRENT_TIMESTAMP,status='SUCCEEDED',details=%s::jsonb WHERE run_id=%s",
                (json.dumps({"row_counts": counts}), run_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            conn.execute(
                "UPDATE skope.etl_run SET completed_at=CURRENT_TIMESTAMP,status='FAILED',details=%s::jsonb WHERE run_id=%s",
                (json.dumps({"row_counts_before_failure": counts}), run_id),
            )
            conn.commit()
            raise
    print(json.dumps({"status": "SUCCEEDED", "row_counts": counts}, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--rebuild", action="store_true", help="Drop existing SKOPE schemas/tables before loading")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    if not arguments.rebuild:
        raise SystemExit("Refusing non-empty load without --rebuild; use the explicit full-rebuild workflow")
    load(arguments.database_url, rebuild=True)
