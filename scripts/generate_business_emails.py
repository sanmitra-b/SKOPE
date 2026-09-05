"""Generate deterministic, source-grounded synthetic business email threads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


QUOTAS = {
    "DATACO_ORDER": 550,
    "BLINKIT_ORDER": 450,
    "BLINKIT_INVENTORY": 250,
    "INDIAECOM_ORDER": 450,
    "LOGISTICS_ORDER": 300,
    "CONTAINER": 300,
    "SHIPPING": 200,
}


def generate_business_emails(root: Path, dataco: pd.DataFrame, version: str, cutoff: pd.Timestamp,
                             canonical_root: Path | None = None) -> None:
    output = root / "EmailJson2500"
    output.mkdir(parents=True, exist_ok=True)

    def txt(value, default="not recorded") -> str:
        return default if pd.isna(value) or not str(value).strip() else str(value).strip()

    def ts(value) -> pd.Timestamp:
        parsed = pd.to_datetime(value, errors="coerce")
        return cutoff if pd.isna(parsed) else min(pd.Timestamp(parsed), cutoff)

    def pick(values, count: int, namespace: str) -> list:
        unique = list(dict.fromkeys(values))
        if len(unique) < count:
            raise RuntimeError(f"{namespace} has only {len(unique)} eligible records; needs {count}")
        return sorted(unique, key=lambda value: hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest())[:count]

    def prov(system: str, records: list[dict]) -> dict:
        return {
            "source_system": system,
            "source_records": records,
            "mapping_method": "GENERATED_FROM_STRUCTURED_FACTS",
            "is_synthetic": True,
            "ground_truth_role": "SYNTHETIC_COMMUNICATION_DERIVED_FROM_CANONICAL_FACTS",
            "transformation_version": version,
        }

    def add(namespace: str, source_id, domain: str, event: str, entity_type: str,
            related: dict, provenance: dict, sent_at, sender: str, recipient: str,
            subject: str, summary: str, response: str, facts: dict) -> None:
        local_id = f"{namespace}-{source_id}"
        event_time = ts(sent_at)
        payload = {
            "ticket_id": f"TICKET-{local_id}",
            "business_domain": domain,
            "event_type": event,
            "primary_entity": {
                "entity_type": entity_type,
                "global_id": f"{namespace.replace('-', ':')}:{source_id}",
                "source_id": str(source_id),
            },
            "related_entities": related,
            "provenance": provenance,
            "facts": facts,
            "emails": [
                {
                    "message_id": f"MSG-{local_id}-01",
                    "sent_at": event_time.isoformat(),
                    "from": sender,
                    "to": recipient,
                    "subject": subject,
                    "body": summary,
                },
                {
                    "message_id": f"MSG-{local_id}-02",
                    "sent_at": min(event_time + pd.Timedelta(minutes=30), cutoff).isoformat(),
                    "from": recipient,
                    "to": sender,
                    "subject": f"Re: {subject}",
                    "body": response,
                },
            ],
        }
        (output / f"Email_{local_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # DataCo fulfillment exceptions.
    late = dataco[dataco["Delivery Status"].astype(str).str.casefold().eq("late delivery")]
    for order_id in pick(late["Order Id"].dropna().astype(int).unique(), QUOTAS["DATACO_ORDER"], "DATACO_ORDER"):
        rows = late[late["Order Id"].astype(int).eq(order_id)].sort_values("Order Item Id")
        row = rows.iloc[0]
        item_ids = rows["Order Item Id"].astype(int).astype(str).tolist()
        product_ids = sorted(rows["Product Card Id"].dropna().astype(int).astype(str).unique().tolist())
        customer_id = str(int(row["Order Customer Id"]))
        scheduled = int(row["Days for shipment (scheduled)"])
        actual = int(row["Days for shipping (real)"])
        order_time = ts(row["order date (DateOrders)"])
        promised = order_time + pd.Timedelta(days=scheduled)
        add(
            "DATACO-ORDER", order_id, "FULFILLMENT", "LATE_DELIVERY", "order",
            {"order_item_ids": item_ids, "product_ids": product_ids,
             "customer_contact_id": f"DATACO-CUSTOMER-{customer_id}"},
            prov("DataCo", [{"source_file": "DataCoSC/DataCoSupplyChainDataset.csv",
                              "key_field": "Order Item Id", "record_ids": item_ids}]),
            promised + pd.Timedelta(hours=1), "customer_support@skopeglobal.com",
            "logistics_coordination@skopeglobal.com",
            f"Delivery window exceeded for DataCo order {order_id}",
            f"DataCo order {order_id} is {actual - scheduled} day(s) beyond its scheduled window. "
            f"It has {len(item_ids)} line(s), destination {txt(row['Order City'])}, {txt(row['Order Country'])}, "
            f"and shipping mode {txt(row['Shipping Mode'])}. Carrier and delay cause are not recorded.",
            f"Canonical facts confirm {scheduled} scheduled day(s) and {actual} actual day(s). "
            f"Customer contact DATACO-CUSTOMER-{customer_id} is available only through the authorized contact workflow. "
            "No carrier penalty or root cause is asserted.",
            {"order_id": str(order_id), "scheduled_days": scheduled, "actual_days": actual,
             "delay_duration_minutes": (actual - scheduled) * 1440, "carrier": None,
             "delivery_mode": txt(row["Shipping Mode"]), "order_time": order_time.isoformat()},
        )

    # Blinkit delivery exceptions joined to order, delivery, feedback, items, and customer contact.
    b = root / "Blinkit_Sales_dataset"
    orders = pd.read_csv(b / "blinkit_orders.csv", dtype=str)
    delivery = pd.read_csv(b / "blinkit_delivery_performance.csv", dtype=str).drop_duplicates("order_id").set_index("order_id")
    feedback = pd.read_csv(b / "blinkit_customer_feedback.csv", dtype=str).drop_duplicates("order_id").set_index("order_id")
    items = pd.read_csv(b / "blinkit_order_items.csv", dtype=str)
    delayed = orders[orders["delivery_status"].str.contains("Delayed", case=False, na=False)]
    for order_id in pick(delayed["order_id"], QUOTAS["BLINKIT_ORDER"], "BLINKIT_ORDER"):
        row = delayed[delayed["order_id"].eq(order_id)].iloc[0]
        detail = delivery.loc[order_id]
        product_ids = sorted(items.loc[items["order_id"].eq(order_id), "product_id"].dropna().unique().tolist())
        rating = int(float(feedback.loc[order_id, "rating"])) if order_id in feedback.index else None
        sentiment = txt(feedback.loc[order_id, "sentiment"]) if order_id in feedback.index else "not recorded"
        sources = [
            {"source_file": "Blinkit_Sales_dataset/blinkit_orders.csv", "key_field": "order_id", "record_ids": [order_id]},
            {"source_file": "Blinkit_Sales_dataset/blinkit_delivery_performance.csv", "key_field": "order_id", "record_ids": [order_id]},
            {"source_file": "Blinkit_Sales_dataset/blinkit_order_items.csv", "key_field": "order_id", "record_ids": [order_id]},
        ]
        if order_id in feedback.index:
            sources.append({"source_file": "Blinkit_Sales_dataset/blinkit_customer_feedback.csv",
                            "key_field": "order_id", "record_ids": [order_id]})
        add(
            "BLINKIT-ORDER", order_id, "LAST_MILE_DELIVERY", "DELIVERY_EXCEPTION", "order",
            {"product_ids": product_ids, "customer_contact_id": f"BLINKIT-CUSTOMER-{row['customer_id']}",
             "delivery_partner_id": txt(row["delivery_partner_id"]), "store_id": txt(row["store_id"])},
            prov("Blinkit", sources), row["actual_delivery_time"],
            "delivery_operations@skopeglobal.com", "customer_support@skopeglobal.com",
            f"Blinkit delivery exception for order {order_id}",
            f"Blinkit order {order_id} is {txt(row['delivery_status'])}. Recorded delivery time is "
            f"{int(float(detail['delivery_time_minutes']))} minute(s), with reason: {txt(detail['reasons_if_delayed'])}.",
            f"The order links {len(product_ids)} product(s), delivery partner {txt(row['delivery_partner_id'])}, "
            f"customer contact BLINKIT-CUSTOMER-{row['customer_id']}, rating {rating}, and sentiment {sentiment}.",
            {"order_id": order_id, "delivery_status": txt(row["delivery_status"]),
             "delivery_time_minutes": int(float(detail["delivery_time_minutes"])),
             "delay_reason": txt(detail["reasons_if_delayed"]), "rating": rating, "sentiment": sentiment},
        )

    # Blinkit inventory damage events linked to product master.
    inventory = pd.read_csv(b / "blinkit_inventory.csv", dtype=str)
    products = pd.read_csv(b / "blinkit_products.csv", dtype=str).drop_duplicates("product_id").set_index("product_id")
    damaged = inventory[pd.to_numeric(inventory["damaged_stock"], errors="coerce").fillna(0).gt(0)]
    for event_id in pick(damaged["inventory_event_id"], QUOTAS["BLINKIT_INVENTORY"], "BLINKIT_INVENTORY"):
        row = damaged[damaged["inventory_event_id"].eq(event_id)].iloc[0]
        product_id = row["product_id"]
        product = products.loc[product_id]
        damaged_units = int(float(row["damaged_stock"]))
        received_units = int(float(row["stock_received"]))
        add(
            "BLINKIT-INVENTORY", event_id, "INVENTORY", "DAMAGED_STOCK", "inventory_event",
            {"product_ids": [product_id]},
            prov("Blinkit", [
                {"source_file": "Blinkit_Sales_dataset/blinkit_inventory.csv", "key_field": "inventory_event_id", "record_ids": [event_id]},
                {"source_file": "Blinkit_Sales_dataset/blinkit_products.csv", "key_field": "product_id", "record_ids": [product_id]},
            ]), row["date"], "inventory_control@skopeglobal.com", "warehouse_operations@skopeglobal.com",
            f"Damaged stock recorded for product {product_id}",
            f"Inventory event {event_id} records {damaged_units} damaged and {received_units} received unit(s) "
            f"for {txt(product['product_name'])} ({product_id}).",
            f"Product master lists category {txt(product['category'])}, brand {txt(product['brand'])}, and minimum "
            f"stock level {txt(product['min_stock_level'])}. The damage cause is not recorded and is not inferred.",
            {"inventory_event_id": event_id, "product_id": product_id, "damaged_stock": damaged_units,
             "stock_received": received_units, "event_date": ts(row["date"]).isoformat()},
        )

    # Indian e-commerce cancellations and returns.
    i = root / "Indian E-Commerce Sales Analytics Dataset"
    sales = pd.read_csv(i / "sales.csv", dtype={"Order_ID": str, "Customer_ID": str, "Product_ID": str}, low_memory=False)
    exceptions = sales[sales["Order_Status"].astype(str).str.casefold().isin({"cancelled", "returned"})]
    for order_id in pick(exceptions["Order_ID"], QUOTAS["INDIAECOM_ORDER"], "INDIAECOM_ORDER"):
        rows = exceptions[exceptions["Order_ID"].eq(order_id)]
        row = rows.iloc[0]
        product_ids = sorted(rows["Product_ID"].dropna().unique().tolist())
        status = txt(row["Order_Status"])
        add(
            "INDIAECOM-ORDER", order_id, "ORDER_MANAGEMENT", status.upper(), "order",
            {"product_ids": product_ids, "customer_contact_id": f"INDIAECOM-CUSTOMER-{row['Customer_ID']}"},
            prov("IndianEComm", [{"source_file": "Indian E-Commerce Sales Analytics Dataset/sales.csv",
                                  "key_field": "Order_ID", "record_ids": [order_id]}]),
            row["Delivery_Date"] if not pd.isna(row["Delivery_Date"]) else row["Order_Date"],
            "order_management@skopeglobal.com", "customer_support@skopeglobal.com",
            f"Indian e-commerce order {order_id}: {status}",
            f"Order {order_id} is {status}, with total amount {float(row['Total_Amount']):.2f}, payment mode "
            f"{txt(row['Payment_Mode'])}, and {len(product_ids)} linked product(s).",
            f"Customer contact INDIAECOM-CUSTOMER-{row['Customer_ID']} is available through the authorized contact "
            "workflow. The source provides no cancellation or return reason, so none is asserted.",
            {"order_id": order_id, "order_status": status, "total_amount": float(row["Total_Amount"]),
             "payment_mode": txt(row["Payment_Mode"]), "order_date": ts(row["Order_Date"]).isoformat()},
        )

    # Freight order schedule deviations.
    logistics = pd.read_excel(root / "Supply chain logisitcs problem.xlsx", sheet_name="OrderList",
                              dtype={"Order ID": str, "Product ID": str})
    late_days = pd.to_numeric(logistics["Ship Late Day count"], errors="coerce").fillna(0)
    ahead_days = pd.to_numeric(logistics["Ship ahead day count"], errors="coerce").fillna(0)
    deviation = logistics[late_days.gt(0) | ahead_days.gt(0)]
    for order_id in pick(deviation["Order ID"], QUOTAS["LOGISTICS_ORDER"], "LOGISTICS_ORDER"):
        row = deviation[deviation["Order ID"].eq(order_id)].iloc[0]
        late = int(row["Ship Late Day count"])
        ahead = int(row["Ship ahead day count"])
        event = "SHIP_LATE" if late > 0 else "SHIP_AHEAD"
        deviation_text = f"{late} late day(s)" if late > 0 else f"{ahead} ahead day(s)"
        add(
            "LOGISTICS-ORDER", order_id, "FREIGHT", event, "freight_order",
            {"carrier": txt(row["Carrier"]), "product_ids": [txt(row["Product ID"])],
             "plant_code": txt(row["Plant Code"]), "origin_port": txt(row["Origin Port"]),
             "destination_port": txt(row["Destination Port"])},
            prov("LogisticsWorkbook", [{"source_file": "Supply chain logisitcs problem.xlsx", "sheet": "OrderList",
                                        "key_field": "Order ID", "record_ids": [order_id]}]),
            row["Order Date"], "freight_operations@skopeglobal.com", "logistics_planning@skopeglobal.com",
            f"Freight schedule deviation for order {order_id}",
            f"Freight order {order_id} records {deviation_text} for carrier {txt(row['Carrier'])}, service level "
            f"{txt(row['Service Level'])}, from {txt(row['Origin Port'])} to {txt(row['Destination Port'])}.",
            f"The order links product {txt(row['Product ID'])}, plant {txt(row['Plant Code'])}, quantity "
            f"{txt(row['Unit quantity'])}, and weight {txt(row['Weight'])}. The deviation cause is not recorded.",
            {"order_id": order_id, "ship_late_days": late, "ship_ahead_days": ahead,
             "service_level": txt(row["Service Level"]), "order_date": ts(row["Order Date"]).isoformat()},
        )

    # Container records not marked delivered.
    container_file = root / "Container Tracking Data.xlsx"
    if not container_file.exists() and canonical_root is not None:
        container_file = canonical_root / "Container Tracking Data.xlsx"
    containers = pd.read_excel(container_file, sheet_name="Data with Main Column",
                               dtype={"container_record_id": str})
    open_rows = containers[~containers["DELIVERED_FLAG"].astype(str).str.casefold().eq("yes")]
    for record_id in pick(open_rows["container_record_id"], QUOTAS["CONTAINER"], "CONTAINER"):
        row = open_rows[open_rows["container_record_id"].eq(record_id)].iloc[0]
        add(
            "CONTAINER", record_id, "OCEAN_FREIGHT", "NOT_MARKED_DELIVERED", "container_tracking_record",
            {"container_number": txt(row["CONTAINER_NUMBER"]), "vessel_name": txt(row["VESSEL_NAME"]),
             "freight_forwarder": txt(row["FREIGHT_FORWARDER"]), "port_of_loading": txt(row["PORT_OF_LOADING"]),
             "port_of_discharge": txt(row["PORT_OF_DISCHARGE"])},
            prov("ContainerTracking", [{"source_file": "Container Tracking Data.xlsx", "sheet": "Data with Main Column",
                                        "key_field": "container_record_id", "record_ids": [record_id]}]),
            row["LAST_TRACKED_WITH_VESSEL"], "ocean_tracking@skopeglobal.com",
            "logistics_coordination@skopeglobal.com",
            f"Container tracking status for {txt(row['CONTAINER_NUMBER'])}",
            f"Container {txt(row['CONTAINER_NUMBER'])} is not marked delivered in tracking record {record_id}. "
            f"Route: {txt(row['PORT_OF_LOADING'])} to {txt(row['PORT_OF_DISCHARGE'])}; vessel: {txt(row['VESSEL_NAME'])}; "
            f"forwarder: {txt(row['FREIGHT_FORWARDER'])}.",
            f"Last tracked timestamp is {ts(row['LAST_TRACKED_WITH_VESSEL']).isoformat()}; delivered date is "
            f"{txt(row['DELIVERED_DATE'])}. This status does not establish a delay cause or future outcome.",
            {"container_record_id": record_id, "delivered_flag": txt(row["DELIVERED_FLAG"]),
             "last_tracked_at": ts(row["LAST_TRACKED_WITH_VESSEL"]).isoformat(),
             "delivered_date": txt(row["DELIVERED_DATE"])},
        )

    # General shipment handling records; no unsupported order/customer/carrier join.
    shipping = pd.read_csv(root / "shipping_data.csv", dtype={"shipment_record_id": str})
    for record_id in pick(shipping["shipment_record_id"], QUOTAS["SHIPPING"], "SHIPPING"):
        row = shipping[shipping["shipment_record_id"].eq(record_id)].iloc[0]
        add(
            "SHIPPING", record_id, "SHIPPING_OPERATIONS", "SHIPMENT_RECORD", "shipment_record",
            {"destination_port": txt(row["destination port"])},
            prov("ShippingData", [{"source_file": "shipping_data.csv", "key_field": "shipment_record_id",
                                   "record_ids": [record_id]}]),
            row["shipment date"], "shipping_desk@skopeglobal.com", "port_operations@skopeglobal.com",
            f"Shipment record {record_id} to {txt(row['destination port'])}",
            f"Shipment {record_id} records item {txt(row['name'])}, weight {float(row['weight (kg)']):.2f} kg, "
            f"dimensions {float(row['length (m)']):.2f} x {float(row['width (m)']):.2f} x "
            f"{float(row['height (m)']):.2f} m, destination {txt(row['destination port'])}.",
            f"Recorded shipment date is {ts(row['shipment date']).date().isoformat()} and price is "
            f"{float(row['price ($)']):.2f}. No order, customer, carrier, or delivery-status key is inferred.",
            {"shipment_record_id": record_id, "item_name": txt(row["name"]), "price": float(row["price ($)"]),
             "weight_kg": float(row["weight (kg)"]), "shipment_date": ts(row["shipment date"]).isoformat()},
        )

    generated = len(list(output.glob("*.json")))
    expected = sum(QUOTAS.values())
    if generated != expected:
        raise RuntimeError(f"Expected {expected} email threads, generated {generated}")
