CREATE TABLE public.store_current_positions (
    id UUID DEFAULT uuidv7(), 	-- generated here
    tenant_id UUID,				-- supplied by caller, generated in tenants	
    store_id UUID,				-- supplied by caller, generated in stores
    sku_id TEXT,
    sku_variant TEXT,
    sku_batch TEXT,
    retail_price NUMERIC(12, 4),
    unit_cost NUMERIC(12, 4),
    stock_qty NUMERIC(14, 3),
    promo_price NUMERIC(12, 4),
    promo_identifier TEXT,
    expiry_date DATE,
    expiry_source TEXT,
    expiry_confidence REAL,
    receipt_date DATE,
    lead_time_days SMALLINT,
    reorder_point NUMERIC(14, 3),
    sku_status TEXT,
    product_name TEXT,
    product_description TEXT,
    product_category TEXT,
    product_sub_category TEXT,
    packaging_type TEXT,
    sku_weight NUMERIC(8, 3),
    unit_of_measure TEXT,
    regulatory_flag BOOLEAN,
    regulatory_type TEXT,
    tax_treatment TEXT,
    currency CHAR(3),
    last_updated_at TIMESTAMPTZ,
    column_timestamps JSONB,
    CONSTRAINT pk_store_current_positions PRIMARY KEY (id)
);