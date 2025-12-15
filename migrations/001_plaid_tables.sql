-- Migration: Create Plaid tables with RLS
-- Description: Creates plaid_items and transactions tables with Row Level Security policies

-- Create plaid_items table
CREATE TABLE IF NOT EXISTS plaid_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    item_id VARCHAR(255) NOT NULL UNIQUE,
    encrypted_access_token TEXT NOT NULL,
    institution_id VARCHAR(255),
    institution_name VARCHAR(255),
    transaction_cursor TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

-- Create indexes for plaid_items
CREATE INDEX IF NOT EXISTS ix_plaid_items_tenant_id ON plaid_items(tenant_id);
CREATE INDEX IF NOT EXISTS ix_plaid_items_user_id ON plaid_items(user_id);
CREATE INDEX IF NOT EXISTS ix_plaid_items_item_id ON plaid_items(item_id);
CREATE INDEX IF NOT EXISTS ix_plaid_items_tenant_user ON plaid_items(tenant_id, user_id);

-- Create transactions table
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    plaid_item_id UUID NOT NULL REFERENCES plaid_items(id) ON DELETE CASCADE,
    plaid_transaction_id VARCHAR(255) NOT NULL UNIQUE,
    account_id VARCHAR(255) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    iso_currency_code VARCHAR(3),
    name VARCHAR(512) NOT NULL,
    merchant_name VARCHAR(255),
    category_primary VARCHAR(255),
    category_detailed VARCHAR(255),
    transaction_date DATE NOT NULL,
    authorized_date DATE,
    pending BOOLEAN NOT NULL DEFAULT FALSE,
    payment_channel VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

-- Create indexes for transactions
CREATE INDEX IF NOT EXISTS ix_transactions_tenant_id ON transactions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_transactions_plaid_item_id ON transactions(plaid_item_id);
CREATE INDEX IF NOT EXISTS ix_transactions_plaid_transaction_id ON transactions(plaid_transaction_id);
CREATE INDEX IF NOT EXISTS ix_transactions_account_id ON transactions(account_id);
CREATE INDEX IF NOT EXISTS ix_transactions_transaction_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS ix_transactions_tenant_date ON transactions(tenant_id, transaction_date);
CREATE INDEX IF NOT EXISTS ix_transactions_account_date ON transactions(account_id, transaction_date);

-- Enable Row Level Security
ALTER TABLE plaid_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;

-- RLS Policies for plaid_items
-- Policy: Users can only see their own tenant's items
CREATE POLICY plaid_items_tenant_isolation ON plaid_items
    FOR ALL
    USING (tenant_id::text = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));

-- RLS Policies for transactions
-- Policy: Users can only see their own tenant's transactions
CREATE POLICY transactions_tenant_isolation ON transactions
    FOR ALL
    USING (tenant_id::text = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));

-- Grant permissions to application user (adjust role name as needed)
-- GRANT ALL ON plaid_items TO app_user;
-- GRANT ALL ON transactions TO app_user;

-- Comments for documentation
COMMENT ON TABLE plaid_items IS 'Stores Plaid Item connections with encrypted access tokens';
COMMENT ON COLUMN plaid_items.encrypted_access_token IS 'Fernet-encrypted Plaid access token - NEVER store in plain text';
COMMENT ON COLUMN plaid_items.transaction_cursor IS 'Cursor for incremental transaction sync';
COMMENT ON TABLE transactions IS 'Stores transaction data from Plaid with tenant isolation';
