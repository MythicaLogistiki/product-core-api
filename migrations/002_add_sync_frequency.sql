-- Add sync_frequency_hours column to plaid_items table
-- This controls how often each item should be auto-synced (in hours)

ALTER TABLE plaid_items
ADD COLUMN IF NOT EXISTS sync_frequency_hours INTEGER NOT NULL DEFAULT 24;

COMMENT ON COLUMN plaid_items.sync_frequency_hours IS 'How often to auto-sync transactions (in hours)';
