import os
import sys
import hashlib
import numpy as np
import pandas as pd

# Define paths
BASE_DIR = r"c:\Users\sancr\Desktop\SKOPE"
RAW_DIR = os.path.join(BASE_DIR, "RAG Project Dataset", "Structured Table40")
OUT_DIR = os.path.join(BASE_DIR, "RAG Project Dataset", "harmonized_data")

# Date boundary constraint (Today is August 2026)
CUTOFF_DATE = pd.to_datetime("2026-07-31")

print("=== STARTING DATA HARMONIZATION PIPELINE ===")
os.makedirs(OUT_DIR, exist_ok=True)

# Helper function to generate deterministic masked values to protect PII
def mask_name(val):
    if pd.isnull(val) or str(val).strip() == "":
        return "Anonymous Customer"
    h = hashlib.md5(str(val).strip().encode("utf-8")).hexdigest()[:6].upper()
    return f"Customer_{h}"

def mask_email(val):
    if pd.isnull(val) or str(val).strip() == "":
        return "customer.hidden@skopeglobal.com"
    h = hashlib.md5(str(val).strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"c***_{h}@skopeglobal.com"

def mask_phone(val):
    if pd.isnull(val) or str(val).strip() == "":
        return "+91-XXXXX-XXXXX"
    clean_val = "".join(filter(str.isdigit, str(val)))
    suffix = clean_val[-4:] if len(clean_val) >= 4 else "0000"
    return f"+91-XXXXX-X{suffix}"

def mask_address(val):
    if pd.isnull(val) or str(val).strip() == "":
        return "Masked Sourcing Address, India"
    return "Masked Address, India"


# --- 1. HARMONIZE RETAIL (INDIAN E-COMMERCE) ---
print("\n--- Harmonizing Indian E-Commerce (Anchor) ---")
ind_src_dir = os.path.join(RAW_DIR, "Indian E-Commerce Sales Analytics Dataset")
ind_out_dir = os.path.join(OUT_DIR, "Indian E-Commerce Sales Analytics Dataset")
os.makedirs(ind_out_dir, exist_ok=True)

# A. Customers (PII masking)
cust_path = os.path.join(ind_src_dir, "customers.csv")
if os.path.exists(cust_path):
    print("Masking Indian E-Commerce customers PII...")
    df_cust = pd.read_csv(cust_path)
    df_cust["Customer_Name"] = df_cust["Customer_Name"].apply(mask_name)
    df_cust["Email"] = df_cust["Email"].apply(mask_email)
    df_cust["Phone"] = df_cust["Phone"].apply(mask_phone)
    df_cust.to_csv(os.path.join(ind_out_dir, "customers.csv"), index=False)

# B. Products (Copy directly)
prod_path = os.path.join(ind_src_dir, "products.csv")
if os.path.exists(prod_path):
    df_prod = pd.read_csv(prod_path)
    df_prod.to_csv(os.path.join(ind_out_dir, "products.csv"), index=False)

# C. Sales (Apply 0-year anchor and July 31, 2026 boundary)
sales_path = os.path.join(ind_src_dir, "sales.csv")
if os.path.exists(sales_path):
    print("Conforming Indian E-Commerce sales dates and statuses...")
    df_sales = pd.read_csv(sales_path)
    df_sales["Order_Date"] = pd.to_datetime(df_sales["Order_Date"], format="%Y-%m-%d")
    df_sales["Delivery_Date"] = pd.to_datetime(df_sales["Delivery_Date"], format="%Y-%m-%d")
    
    # Filter out orders past cutoff date
    df_sales = df_sales[df_sales["Order_Date"] <= CUTOFF_DATE]
    
    # Handle future outcome leakage (Delivery date past cutoff)
    future_deliv_mask = df_sales["Delivery_Date"] > CUTOFF_DATE
    df_sales.loc[future_deliv_mask, "Delivery_Date"] = pd.NaT
    df_sales.loc[future_deliv_mask, "Order_Status"] = "SHIPPED" # Recalculate status to prevent leakage
    
    # Save with conformed dates format
    df_sales["Order_Date"] = df_sales["Order_Date"].dt.strftime("%Y-%m-%d")
    df_sales["Delivery_Date"] = df_sales["Delivery_Date"].dt.strftime("%Y-%m-%d")
    df_sales.to_csv(os.path.join(ind_out_dir, "sales.csv"), index=False)


# --- 2. HARMONIZE GROCERY (BLINKIT QUICK COMMERCE) ---
print("\n--- Harmonizing Blinkit Grocery (+2 Years Shift) ---")
b_src_dir = os.path.join(RAW_DIR, "Blinkit_Sales_dataset")
b_out_dir = os.path.join(OUT_DIR, "Blinkit_Sales_dataset")
os.makedirs(b_out_dir, exist_ok=True)

# A. Customers (PII masking)
b_cust_path = os.path.join(b_src_dir, "blinkit_customers.csv")
if os.path.exists(b_cust_path):
    print("Masking Blinkit customer PII...")
    df_bcust = pd.read_csv(b_cust_path)
    df_bcust["customer_name"] = df_bcust["customer_name"].apply(mask_name)
    df_bcust["email"] = df_bcust["email"].apply(mask_email)
    df_bcust["phone"] = df_bcust["phone"].apply(mask_phone)
    df_bcust["address"] = df_bcust["address"].apply(mask_address)
    df_bcust.to_csv(os.path.join(b_out_dir, "blinkit_customers.csv"), index=False)

# B. Products (Copy directly)
b_prod_path = os.path.join(b_src_dir, "blinkit_products.csv")
if os.path.exists(b_prod_path):
    df_bprod = pd.read_csv(b_prod_path)
    df_bprod.to_csv(os.path.join(b_out_dir, "blinkit_products.csv"), index=False)

# C. Order Items (Copy directly)
b_items_path = os.path.join(b_src_dir, "blinkit_order_items.csv")
if os.path.exists(b_items_path):
    df_bitems = pd.read_csv(b_items_path)
    df_bitems.to_csv(os.path.join(b_out_dir, "blinkit_order_items.csv"), index=False)

# D. Orders (+2 Years Shift and Truncation)
b_orders_path = os.path.join(b_src_dir, "blinkit_orders.csv")
if os.path.exists(b_orders_path):
    print("Shifting Blinkit orders by +2 years...")
    df_borders = pd.read_csv(b_orders_path)
    df_borders["order_date"] = pd.to_datetime(df_borders["order_date"], format="%Y-%m-%d %H:%M:%S")
    df_borders["order_date"] = df_borders["order_date"] + pd.DateOffset(years=2)
    
    # Filter orders past cutoff
    df_borders = df_borders[df_borders["order_date"] <= CUTOFF_DATE]
    df_borders["order_date"] = df_borders["order_date"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df_borders.to_csv(os.path.join(b_out_dir, "blinkit_orders.csv"), index=False)

# E. Delivery Performance (+2 Years Shift, Truncate Future Delivery)
b_deliv_path = os.path.join(b_src_dir, "blinkit_delivery_performance.csv")
if os.path.exists(b_deliv_path):
    print("Shifting Blinkit delivery times and resolving future outcome leakage...")
    df_bdeliv = pd.read_csv(b_deliv_path)
    df_bdeliv["promised_time"] = pd.to_datetime(df_bdeliv["promised_time"]) + pd.DateOffset(years=2)
    df_bdeliv["actual_time"] = pd.to_datetime(df_bdeliv["actual_time"]) + pd.DateOffset(years=2)
    
    # Filter voyages where dispatch exceeded cutoff
    df_bdeliv = df_bdeliv[df_bdeliv["promised_time"] <= CUTOFF_DATE]
    
    # If actual time was after cutoff, it is still in transit (leakage prevention)
    future_deliv = df_bdeliv["actual_time"] > CUTOFF_DATE
    df_bdeliv.loc[future_deliv, "actual_time"] = pd.NaT
    df_bdeliv.loc[future_deliv, "delivery_status"] = "PROCESSING"
    
    df_bdeliv["promised_time"] = df_bdeliv["promised_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df_bdeliv["actual_time"] = df_bdeliv["actual_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df_bdeliv.to_csv(os.path.join(b_out_dir, "blinkit_delivery_performance.csv"), index=False)

# F. Customer Feedback (+2 Years Shift)
b_feed_path = os.path.join(b_src_dir, "blinkit_customer_feedback.csv")
if os.path.exists(b_feed_path):
    df_bfeed = pd.read_csv(b_feed_path)
    df_bfeed["feedback_date"] = pd.to_datetime(df_bfeed["feedback_date"]) + pd.DateOffset(years=2)
    df_bfeed = df_bfeed[df_bfeed["feedback_date"] <= CUTOFF_DATE]
    df_bfeed["feedback_date"] = df_bfeed["feedback_date"].dt.strftime("%Y-%m-%d")
    df_bfeed.to_csv(os.path.join(b_out_dir, "blinkit_customer_feedback.csv"), index=False)

# G. Inventory Receipts (Using blinkit_inventory.csv only, blinkit_inventoryNew is quarantined)
b_inv_path = os.path.join(b_src_dir, "blinkit_inventory.csv")
if os.path.exists(b_inv_path):
    print("Shifting Blinkit inventory logs (+2 years)...")
    df_binv = pd.read_csv(b_inv_path)
    df_binv["date"] = pd.to_datetime(df_binv["date"]) + pd.DateOffset(years=2)
    df_binv = df_binv[df_binv["date"] <= CUTOFF_DATE]
    df_binv["date"] = df_binv["date"].dt.strftime("%Y-%m-%d")
    df_binv.to_csv(os.path.join(b_out_dir, "blinkit_inventory.csv"), index=False)


# --- 3. HARMONIZE GLOBAL RETAIL (DATACO) ---
print("\n--- Harmonizing DataCo Global Retail (+9 Years Shift) ---")
d_src_dir = os.path.join(RAW_DIR, "DataCoSC")
d_out_dir = os.path.join(OUT_DIR, "DataCoSC")
os.makedirs(d_out_dir, exist_ok=True)

d_sales_path = os.path.join(d_src_dir, "DataCoSupplyChainDataset.csv")
if os.path.exists(d_sales_path):
    print("Processing DataCo transactions, masking PII, and removing passwords...")
    # Load using fallback encoding
    df_dsales = pd.read_csv(d_sales_path, encoding="ISO-8859-1")
    
    # 1. Strip password and secure fields entirely (PII control)
    if "Customer Password" in df_dsales.columns:
        df_dsales = df_dsales.drop(columns=["Customer Password"])
    if "Customer Email" in df_dsales.columns:
        df_dsales = df_dsales.drop(columns=["Customer Email"])
        
    # 2. Anonymize customer names
    df_dsales["Customer Fname"] = df_dsales["Customer Fname"].apply(mask_name)
    df_dsales["Customer Lname"] = ""
    
    # 3. Parse and Shift Dates by +9 Years
    df_dsales["order date (DateOrders)"] = pd.to_datetime(df_dsales["order date (DateOrders)"], format="%m/%d/%Y %H:%M")
    df_dsales["shipping date (DateOrders)"] = pd.to_datetime(df_dsales["shipping date (DateOrders)"], format="%m/%d/%Y %H:%M")
    
    df_dsales["order date (DateOrders)"] = df_dsales["order date (DateOrders)"] + pd.DateOffset(years=9)
    df_dsales["shipping date (DateOrders)"] = df_dsales["shipping date (DateOrders)"] + pd.DateOffset(years=9)
    
    # 4. Filter out orders past cutoff
    df_dsales = df_dsales[df_dsales["order date (DateOrders)"] <= CUTOFF_DATE]
    
    # 5. Handle future outcome leakage (shipping date past cutoff)
    future_ship = df_dsales["shipping date (DateOrders)"] > CUTOFF_DATE
    df_dsales.loc[future_ship, "shipping date (DateOrders)"] = pd.NaT
    df_dsales.loc[future_ship, "Delivery Status"] = "Shipping on time"
    
    # Convert dates back to standard string format
    df_dsales["order date (DateOrders)"] = df_dsales["order date (DateOrders)"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df_dsales["shipping date (DateOrders)"] = df_dsales["shipping date (DateOrders)"].dt.strftime("%Y-%m-%d %H:%M:%S")
    
    df_dsales.to_csv(os.path.join(d_out_dir, "DataCoSupplyChainDataset.csv"), index=False)

# Copy DataCo description file directly
d_desc_path = os.path.join(d_src_dir, "DescriptionDataCoSupplyChain.csv")
if os.path.exists(d_desc_path):
    pd.read_csv(d_desc_path).to_csv(os.path.join(d_out_dir, "DescriptionDataCoSupplyChain.csv"), index=False)


# --- 4. HARMONIZE LOGISTICS & SHIPPING ---
print("\n--- Harmonizing Logistics & Container Tracking (+4 Years Shift) ---")

# A. Container Tracking Data
c_path = os.path.join(RAW_DIR, "Container Tracking Data.xlsx")
if os.path.exists(c_path):
    print("Processing Container Tracking logs and cleaning port cell type errors...")
    # Load Excel sheets
    xl = pd.ExcelFile(c_path)
    df_containers = xl.parse("Data with Main Column")
    df_defs = xl.parse("Columns Definitions")
    
    # 1. Clean dirty data inside port name columns (datetime cells)
    for col in ["PORT_OF_LOADING", "PORT_OF_DISCHARGE"]:
        df_containers[col] = df_containers[col].apply(lambda x: "UNKNOWN" if isinstance(x, (pd.Timestamp, np.datetime64)) or (hasattr(x, "year") and x.year > 2000) else str(x))
    
    # 2. Shift dates by +4 years (using errors='coerce' to clean dirty 'Yes' strings to NaT)
    date_cols = ["PLACE_OF_DISPATCH_DATE", "PORT_OF_LOADING_DATE", "PORT_OF_DISCHARGE_DATE", "POST_PORT_OF_DISCHARGE_DATE", "DELIVERED_DATE"]
    for col in date_cols:
        # Convert to datetime, coercing invalid values (like 'Yes') to NaT
        df_containers[col] = pd.to_datetime(df_containers[col], errors="coerce")
        # Filter out old 1900-01-01 Excel cell bugs
        df_containers.loc[df_containers[col].dt.year < 2000, col] = pd.NaT
        # Shift forward
        df_containers[col] = df_containers[col] + pd.DateOffset(years=4)
        
    # 3. Apply 2.5-Year Window Filter (Keep only conformed voyages starting 2024-01-01 onwards)
    df_containers = df_containers[df_containers["PLACE_OF_DISPATCH_DATE"] >= pd.to_datetime("2024-01-01")]
    df_containers = df_containers[df_containers["PLACE_OF_DISPATCH_DATE"] <= CUTOFF_DATE]
    
    # Leakage prevention on arrival dates
    for col in ["PORT_OF_DISCHARGE_DATE", "POST_PORT_OF_DISCHARGE_DATE", "DELIVERED_DATE"]:
        future_mask = df_containers[col] > CUTOFF_DATE
        df_containers.loc[future_mask, col] = pd.NaT
        if col == "DELIVERED_DATE":
            df_containers.loc[future_mask, "DELIVERED_FLAG"] = "No"
            
    # Save back to Excel inside harmonized_data
    out_xl_path = os.path.join(OUT_DIR, "Container Tracking Data.xlsx")
    with pd.ExcelWriter(out_xl_path) as writer:
        df_containers.to_excel(writer, sheet_name="Data with Main Column", index=False)
        df_defs.to_excel(writer, sheet_name="Columns Definitions", index=False)

# B. Supply Chain Logistics Problem
s_path = os.path.join(RAW_DIR, "Supply chain logisitcs problem.xlsx")
if os.path.exists(s_path):
    print("Shifting B2B Logistics snapshot dates (+12 years)...")
    xl_s = pd.ExcelFile(s_path)
    sheets_dict = {sheet: xl_s.parse(sheet) for sheet in xl_s.sheet_names}
    
    # Shift OrderList dates by +12 years
    df_orders = sheets_dict["OrderList"]
    df_orders["Order Date"] = pd.to_datetime(df_orders["Order Date"]) + pd.DateOffset(years=12)
    sheets_dict["OrderList"] = df_orders
    
    out_s_path = os.path.join(OUT_DIR, "Supply chain logisitcs problem.xlsx")
    with pd.ExcelWriter(out_s_path) as writer:
        for sheet, df in sheets_dict.items():
            df.to_excel(writer, sheet_name=sheet, index=False)

# C. Shipping Data (+3 Years Shift)
sh_path = os.path.join(RAW_DIR, "shipping_data.csv")
if os.path.exists(sh_path):
    print("Shifting physical package shipping dates (+3 years)...")
    df_sh = pd.read_csv(sh_path)
    df_sh["shipment date"] = pd.to_datetime(df_sh["shipment date"], format="%Y-%m-%d") + pd.DateOffset(years=3)
    df_sh = df_sh[df_sh["shipment date"] <= CUTOFF_DATE]
    df_sh["shipment date"] = df_sh["shipment date"].dt.strftime("%Y-%m-%d")
    df_sh.to_csv(os.path.join(OUT_DIR, "shipping_data.csv"), index=False)

# D. Last-Mile Telemetry (Copy directly, no date shifting needed)
lm_path = os.path.join(RAW_DIR, "lastMileDeliveryTimesWithCats.csv")
if os.path.exists(lm_path):
    pd.read_csv(lm_path).to_csv(os.path.join(OUT_DIR, "lastMileDeliveryTimesWithCats.csv"), index=False)

# E. Customer Chat Logs (Copy directly, only has unstructured text)
chat_path = os.path.join(RAW_DIR, "GajurelKshitizEcommerce-Chat-Dataset.csv")
if os.path.exists(chat_path):
    pd.read_csv(chat_path).to_csv(os.path.join(OUT_DIR, "GajurelKshitizEcommerce-Chat-Dataset.csv"), index=False)

# F. Flipkart Products (Copy directly)
flip_prod_path = os.path.join(RAW_DIR, "Flipkart Sales Data", "products.csv")
if os.path.exists(flip_prod_path):
    print("Copying Flipkart catalog products...")
    flip_out_dir = os.path.join(OUT_DIR, "Flipkart Sales Data")
    os.makedirs(flip_out_dir, exist_ok=True)
    pd.read_csv(flip_prod_path).to_csv(os.path.join(flip_out_dir, "products.csv"), index=False)

print("\n=== DATA HARMONIZATION PIPELINE COMPLETED SUCCESSFULLY ===")
print(f"All conformed data saved to: {OUT_DIR}")
