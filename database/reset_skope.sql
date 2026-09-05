-- Destructive reset for the dedicated local skope_db database.
DROP SCHEMA IF EXISTS restricted CASCADE;
DROP SCHEMA IF EXISTS skope CASCADE;

DROP TABLE IF EXISTS public.sys_rejected_records CASCADE;
DROP TABLE IF EXISTS public.sys_location_crosswalk CASCADE;
DROP TABLE IF EXISTS public.fact_customer_feedback CASCADE;
DROP TABLE IF EXISTS public.fact_freight_movement CASCADE;
DROP TABLE IF EXISTS public.fact_delivery CASCADE;
DROP TABLE IF EXISTS public.fact_order_fulfillment CASCADE;
DROP TABLE IF EXISTS public.dim_date CASCADE;
DROP TABLE IF EXISTS public.dim_location CASCADE;
DROP TABLE IF EXISTS public.dim_partner CASCADE;
DROP TABLE IF EXISTS public.dim_customer CASCADE;
DROP TABLE IF EXISTS public.dim_product CASCADE;
