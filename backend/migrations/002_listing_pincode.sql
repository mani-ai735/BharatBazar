-- Applied idempotently at API startup for existing databases.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS pincode VARCHAR(10) NOT NULL DEFAULT '';
